"""Shared construction helpers for direct ``git`` CLI commands.

Every fact and operation in this package runs the ``git`` CLI on the target
host through pyinfra's connector. This module is the single place where those
commands are assembled; domain modules build commands with these helpers and
never execute anything themselves.

Security contract — keep this intact when the sample domain is replaced:

- Every user-supplied value placed in argv (repository paths, configuration
  keys, configuration values, ...) MUST be wrapped in
  :class:`pyinfra.api.QuoteString` by the caller so it is shell-quoted when
  the command renders. Bare ``str`` bits are reserved for trusted literals:
  the subcommand words and flags this package itself chooses.
- Shell quoting cannot protect against the ``git`` CLI's own option parsing:
  a quoted value beginning with ``-`` still parses as an option once the
  shell layer is gone (``git config --local user.name --global`` does not set
  a value, it re-scopes the command). :func:`git_command` therefore rejects
  every ``QuoteString``-wrapped value that starts with ``-`` by raising
  :class:`CommandError`.
- Nothing secret ever goes in argv. Values that must stay off the process
  table travel on standard input via the ``stdin`` keyword, which maps to
  pyinfra's ``_stdin`` connector argument. The sample ``git config`` domain
  has no such value, so nothing passes ``stdin`` today; the plumbing is here
  because the real domain that replaces it usually needs it.
"""

from __future__ import annotations

from pyinfra.api import QuoteString, StringCommand

__all__ = [
    "CommandError",
    "QuoteString",
    "StringCommand",
    "git_command",
]


class CommandError(RuntimeError):
    """Raised when a value cannot be rendered safely into ``git`` argv."""


def _reject_option_lookalike(value: str) -> None:
    if value.startswith("-"):
        raise CommandError(
            f"value {value!r} cannot start with '-': git would parse it as a command-line option",
        )


def git_command(
    *args: str | QuoteString | StringCommand,
    path: str | None = None,
    stdin: str | None = None,
) -> StringCommand:
    """Build a :class:`StringCommand` invoking ``git`` with ``args``.

    ``args`` follow the ``git`` executable verbatim; wrap every user-supplied
    value in :class:`QuoteString`. When ``path`` is given, ``-C <path>``
    (quoted) is inserted *before* ``args`` — ``git`` only honours ``-C`` as a
    top-level option, so a repository-scoped command renders as
    ``git -C <path> config ...``. When ``stdin`` is given it becomes the
    command's standard input.

    Every ``QuoteString`` value (and ``path``) is rejected with
    :class:`CommandError` when it starts with ``-``: shell quoting does not
    stop git's own parser from consuming such a token as an option, which
    would silently change what the command does.
    """

    for arg in args:
        if isinstance(arg, QuoteString):
            _reject_option_lookalike(str(arg.obj))
    bits: list[str | QuoteString | StringCommand] = ["git"]
    if path is not None:
        _reject_option_lookalike(path)
        bits.extend(("-C", QuoteString(path)))
    bits.extend(args)
    if stdin is not None:
        return StringCommand(*bits, _stdin=stdin)
    return StringCommand(*bits)
