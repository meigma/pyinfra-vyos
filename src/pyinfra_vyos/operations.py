"""Config mutations executed through each host's pyinfra connector.

:func:`config_load` uploads a rendered config and runs one ``sg vyattacfg``
session that loads, compare-gates, and optionally saves. There is no
prepare-time diff: the device's ``load`` + ``sessionChanged`` result is the
authority (D3), so this operation always yields commands and pyinfra always
reports it changed.

:func:`config` owns a scoped subtree instead: it diffs the caller's desired
values against the active tree (the :class:`~pyinfra_vyos.facts.Configuration`
fact) on the controller, noops on an empty delta, and otherwise applies the
``set`` / ``delete`` delta in one session behind the same ``sessionChanged``
commit gate — the device stays the authority on whether anything actually
changed.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Generator
from io import StringIO
from typing import IO, Any, TypeVar

from pyinfra import host, state
from pyinfra.api import FileUploadCommand, QuoteString, StringCommand, operation
from pyinfra.api.exceptions import OperationValueError

from pyinfra_vyos._cli import session_run_sequence, sg_probe, sg_vbash_run
from pyinfra_vyos._parse import stream_is_nonempty
from pyinfra_vyos._session import (
    PlannedCommand,
    build_commands_script,
    build_load_script,
    build_save_script,
    staging_dir,
)
from pyinfra_vyos._tree import TreeError, diff_tree, normalize_tree, select_subtree, validate_path
from pyinfra_vyos.facts import Configuration, PendingSave

__all__ = ["config", "config_load", "config_save"]

_T = TypeVar("_T")


class _SourceError(Exception):
    """Rejected ``config_load`` src; translated to OperationValueError."""


_DOMAIN_ERRORS = (_SourceError, TreeError)


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

    **Config-version footer**: VyOS ``load`` runs its migrators on every
    uploaded file and treats a config without a ``// vyos-config-version``
    footer as version 0, executing the full historical migration chain
    against it. Callers SHOULD supply a footer-bearing config (anything
    saved by VyOS — ``/config/config.boot``, ``save <file>`` output —
    carries it; bare ``show configuration`` output does not). The library
    does not detect or inject the footer; documented, not enforced.

    **Concurrency precondition**: the caller MUST serialize all
    ``config_load`` mutations per host — at most one mutation session may
    run at a time, including runs from the same controller (§2 / D4).
    Overlapping mutation runs are out of contract.

    **Cleanup residual**: any yielded command failing before the session
    runs — an upload, a chmod, the remote non-whitespace guard — or
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


@operation()
def config(
    path: list[str] | tuple[str, ...],
    values: dict[str, Any] | None = None,
    *,
    replace: bool = False,
    present: bool = True,
    save: bool = False,
) -> Generator[StringCommand | FileUploadCommand, None, None]:
    """Configure an owned subtree of the VyOS config tree.

    ``path`` is the owned config path as separate tokens, e.g.
    ``["service", "ntp"]``. ``values`` is the desired subtree beneath it,
    mirroring ``show configuration json`` shapes: nested ``dict`` for a
    subtree, ``{}`` for a valueless node, ``str`` for a single-value leaf,
    ``list[str]`` for a multi-value leaf. ``values=None`` means "ensure the
    bare path node exists".

    **Ownership**: by default omitted state is unmanaged — only ``set``
    commands for missing or differing desired values are applied (merge).
    With ``replace=True`` the subtree under ``path`` becomes exactly
    ``values``: active keys and leaf values absent from ``values`` are
    deleted. Choose the owned ``path`` accordingly; a broad path with
    ``replace=True`` can remove management access. ``present=False``
    deletes the whole path (``values`` and ``replace`` must be left unset).

    **Idempotency**: the desired subtree is diffed against the active tree
    (:class:`~pyinfra_vyos.facts.Configuration`) on the controller; an empty
    delta noops without touching the device. When commands are applied, the
    session's ``sessionChanged`` gate remains the authority: if the device
    canonicalizes the supplied values into what is already active, the
    session truthfully reports the ``noop`` sentinel (and the controller
    diff will re-emit the delta on every run until the caller supplies the
    device-canonical form). Multi-value leaves compare as unordered sets;
    value ordering is not managed.

    **Value constraints**: every path token, key, and value must be a
    nonempty string that does not begin with ``-`` (they become device-side
    ``set`` / ``delete`` argv). Secret-bearing values travel inside the
    uploaded 0600 session script, never on process argv; on failure the
    library logs only the command ordinal and verb, plus device output.

    **Atomicity**: the whole delta is staged in one configure session and
    committed once, so dependent fields under the owned path validate
    together. ``save=True`` persists only when this run commits; an empty
    delta noops regardless of ``save``.

    **Concurrency precondition**: as with :func:`config_load`, the caller
    MUST serialize all mutation sessions per host (§2 / D4).

    **Example**:

    .. code:: python

        config(
            name="Manage NTP servers",
            path=["service", "ntp"],
            values={"server": {"time1.example.net": {}, "time2.example.net": {}}},
            replace=True,
            save=True,
        )
    """

    path_tokens = _guarded(validate_path, path)
    label = " ".join(path_tokens)

    if not present:
        if values is not None:
            raise OperationValueError("values must be omitted when present=False")
        if replace:
            raise OperationValueError("replace has no meaning when present=False")
        active = select_subtree(host.get_fact(Configuration), path_tokens)
        if active is None:
            host.noop(f"config path already absent: {label}")
            return
        commands: list[PlannedCommand] = [PlannedCommand(["delete", *path_tokens])]
    else:
        desired = _guarded(normalize_tree, values if values is not None else {}, strict=True)
        active = select_subtree(host.get_fact(Configuration), path_tokens)
        deletes, sets = diff_tree(active, desired, path_tokens, replace=replace)
        if not deletes and not sets:
            host.noop(f"config subtree already matches: {label}")
            return
        commands = [PlannedCommand(["delete", *argv]) for argv in deletes]
        commands.extend(PlannedCommand(["set", *argv]) for argv in sets)

    staging = staging_dir()
    script_text = build_commands_script(staging, commands, save=save)
    yield from session_run_sequence(staging, script_text)


@operation()
def config_save() -> Generator[StringCommand | FileUploadCommand, None, None]:
    """Persist the active configuration to ``/config/config.boot``.

    Save is device-global: it writes the complete active configuration to
    ``/config/config.boot``, including unrelated active changes that were
    already unsaved or arrived from another controller. The caller must
    serialize or otherwise account for those changes; typed ownership does
    not scope persistence.

    **Verify-then-persist**: the recommended workflow for risky changes is
    ``op(..., save=False)`` → verify reachability / facts →
    :func:`config_save`. This operation is the second phase of that
    workflow. It noops honestly when :class:`~pyinfra_vyos.facts.PendingSave`
    is ``False``, fails closed when saved-state cannot be established
    (``PendingSave`` ``None`` → :class:`~pyinfra.api.exceptions.OperationValueError`),
    and otherwise saves idempotently.

    **Concurrency precondition**: the caller MUST serialize all mutation
    sessions per host — at most one mutation session may run at a time,
    including runs from the same controller (§2 / D4). Overlapping
    mutation runs are out of contract.
    """

    pending = host.get_fact(PendingSave)
    if pending is None:
        raise OperationValueError("saved-state could not be established")
    if pending is False:
        host.noop("configuration already saved")
        return
    staging = staging_dir()
    yield from session_run_sequence(staging, build_save_script(staging))
