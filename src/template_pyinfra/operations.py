"""Declarative git operations executed through each host's pyinfra connector.

Every operation reconciles from facts at prepare time — read the fact,
validate, diff, then no-op or yield direct ``git`` CLI commands built by the
domain module. Commands fail hard (nonzero exit) on mid-deploy drift; there
are no execution-time re-checks.

:func:`config_entry` is the sample operation and shows the whole idempotency
loop in one place. Replace it when molding this template into a real package;
keep the loop.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any, TypeVar

from pyinfra import host
from pyinfra.api import StringCommand, operation
from pyinfra.api.exceptions import OperationValueError

from template_pyinfra._cli import CommandError
from template_pyinfra._gitconfig import (
    GitConfigError,
    config_changes,
    config_commands,
    validate_config_key,
    validate_repository_path,
)
from template_pyinfra.facts import DEFAULT_PATH, GitConfig

__all__ = ["config_entry"]

_DOMAIN_ERRORS = (CommandError, GitConfigError)

_T = TypeVar("_T")


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


@operation()
def config_entry(
    key: str,
    value: str | None = None,
    *,
    present: bool = True,
    path: str = DEFAULT_PATH,
) -> Generator[StringCommand, None, None]:
    """Ensure one repository-local git configuration entry.

    Reads :class:`~template_pyinfra.facts.GitConfig` for ``path``, and when
    the entry already matches, no-ops. Otherwise it runs
    ``git -C <path> config --local --replace-all <key> <value>`` to set it,
    or ``git -C <path> config --local --unset-all <key>`` when
    ``present=False``. ``--local`` keeps every write inside the repository's
    own ``.git/config``.

    ``value`` is required when ``present`` is true and ignored otherwise.
    Note that the entry's key is ``key``, not ``name``: pyinfra reserves
    ``name`` for the operation label and never delivers it to the operation
    (``tests/test_cli.py`` enforces this for every exported operation).
    """

    _guarded(validate_repository_path, path)
    _guarded(validate_config_key, key)
    if present and value is None:
        raise OperationValueError(f"value is required to set git config entry {key}")

    current = host.get_fact(GitConfig, path=path)
    changes = _guarded(config_changes, current, {key: value if present else None})
    if not changes:
        settled = f"already set to {value!r}" if present else "already absent"
        host.noop(f"git config entry {key} in {path} is {settled}")
        return
    yield from _guarded(config_commands, path=path, changes=changes)
