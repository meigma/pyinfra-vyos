"""Whole-config load executed through each host's pyinfra connector.

:func:`config_load` uploads a rendered config and runs one ``sg vyattacfg``
session that loads, compare-gates, and optionally saves. There is no
prepare-time diff: the device's ``load`` + ``sessionChanged`` result is the
authority (D3), so this operation always yields commands and pyinfra always
reports it changed.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from io import StringIO
from typing import IO, Any, TypeVar

from pyinfra import state
from pyinfra.api import FileUploadCommand, QuoteString, StringCommand, operation
from pyinfra.api.exceptions import OperationValueError

from pyinfra_vyos._cli import sg_probe, sg_vbash_run
from pyinfra_vyos._parse import stream_is_nonempty
from pyinfra_vyos._session import build_load_script, staging_dir

__all__ = ["config_load"]

_T = TypeVar("_T")


class _SourceError(Exception):
    """Rejected ``config_load`` src; translated to OperationValueError."""


_DOMAIN_ERRORS = (_SourceError,)


def _guarded(function: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Run a domain validator or command builder, surfacing rejections clearly.

    Domain modules raise their own exception types; pyinfra only presents
    :class:`OperationValueError` to the user as a rejected operation
    argument, so the translation happens once, here.
    """

    try:
        return function(*args, **kwargs)
    except _DOMAIN_ERRORS as error:
        raise OperationValueError(str(error)) from error


def _resolve_put_path(src: str) -> str:
    """Resolve a controller-local path the way pyinfra ``files.put`` does.

    Pinned to pyinfra 3.10.0 ``files.put`` (default ``add_deploy_dir=True``):
    ``os.path.join(state.cwd, src)`` when ``state.cwd`` is set, else ``src``.
    ``os.path.join`` keeps an absolute ``src`` unchanged.
    """

    if state.cwd:
        return os.path.join(state.cwd, src)
    return src


def _is_seekable(src: object) -> bool:
    seekable = getattr(src, "seekable", None)
    if callable(seekable):
        return bool(seekable())
    return callable(getattr(src, "seek", None))


def _require_nonempty(fileobj: IO[Any], *, src: str) -> None:
    if not stream_is_nonempty(fileobj):
        raise _SourceError(f"src is empty or whitespace-only: {src}")


def _validate_src(src: str | IO[Any]) -> str | IO[Any]:
    """Return the FileUploadCommand src after controller-side checks."""

    if isinstance(src, str):
        path = _resolve_put_path(src)
        try:
            with open(path, "rb") as fileobj:
                _require_nonempty(fileobj, src=path)
        except OSError as error:
            raise _SourceError(f"cannot read src: {path}") from error
        return path

    if not hasattr(src, "read"):
        raise _SourceError("src must be a path or readable file-like object")
    if not _is_seekable(src):
        raise _SourceError("src file-like object must be seekable")
    _require_nonempty(src, src=repr(src))
    return src


@operation(
    is_idempotent=False,
    idempotent_notice=(
        "device mutation is compare-gated on the target; "
        "pyinfra always reports this operation as changed"
    ),
)
def config_load(
    src: str | IO[Any],
    *,
    save: bool = False,
) -> Generator[StringCommand | FileUploadCommand, None, None]:
    """Load a whole config onto a VyOS host in one configure session.

    ``src`` is a controller-local path (``str``) or a readable, seekable
    file-like object. A ``str`` is resolved against the deploy directory with
    the same rule ``files.put`` uses (``os.path.join(state.cwd, src)`` when
    ``state.cwd`` is set). The controller checks that ``src`` contains at
    least one non-whitespace byte; that check is TOCTOU-gapped, and the
    remote grep in the yielded sequence is the enforcing check. pyinfra
    ``seek(0)``s file-like sources on execute.

    **Concurrency precondition**: the caller MUST serialize all
    ``config_load`` mutations per host — at most one mutation session may
    run at a time, including runs from the same controller (§2 / D4).
    Overlapping mutation runs are out of contract.

    **Cleanup residual**: any yielded command failing before the session
    runs - an upload, a chmod, the remote non-whitespace guard - or
    connector loss strands the 0600/0700 staging directory in ``/tmp``.
    Paths that reach session execution are cleaned up by the EXIT trap
    and by command 7.

    **Commit-verify-save**: a bad full config can sever SSH. Call with
    ``save=False``, verify reachability / facts, then call again with
    ``save=True``.

    **Change reporting**: pyinfra always reports this operation as changed
    (D3; nonempty executed command list). A save-only run (no candidate
    diff, boot file still written) reports ``changed`` via the device
    sentinel, never ``noop``.
    """

    upload_src = _guarded(_validate_src, src)
    staging = staging_dir()
    config_path = f"{staging}/config"
    script_path = f"{staging}/session.sh"

    yield sg_probe()
    yield StringCommand("mkdir", "-m", "700", QuoteString(staging))
    yield FileUploadCommand(upload_src, config_path)
    yield StringCommand(
        "chmod",
        "600",
        QuoteString(config_path),
        "&&",
        "LC_ALL=C",
        "grep",
        "-q",
        QuoteString("[^[:space:]]"),
        QuoteString(config_path),
    )
    yield FileUploadCommand(StringIO(build_load_script(staging, save=save)), script_path)
    yield StringCommand("chmod", "600", QuoteString(script_path))
    yield sg_vbash_run(script_path, staging)
