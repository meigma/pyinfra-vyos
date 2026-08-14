"""Repository-local ``git config`` parsing, diffing, and command builders.

This is the sample domain module: pure functions with no I/O and no pyinfra
state, which is what lets the unit tests run without mocks. ``facts.py``
feeds it raw CLI output, ``operations.py`` feeds it a desired state and
executes the commands it returns.

Everything here is scoped to one repository's ``.git/config`` via ``git -C
<path> config --local ...``. Nothing ever touches the machine's global or
system configuration, so the operation is safe to run on a CI runner.

Source-verified ``git`` behaviour this module relies on (git 2.x,
``Documentation/git-config.adoc``):

- ``git config --list --null`` writes ``<key>\\n<value>\\0`` per entry, and
  ``<key>\\0`` with no newline for a valueless key (``[section] key`` with no
  ``=``). Values may themselves contain newlines, so the NUL is the only
  reliable record separator.
- ``--replace-all`` sets a key whether or not it already exists and collapses
  a multi-valued key to the single given value, so one builder covers both
  creation and update.
- ``--unset-all`` removes every value of a key and exits nonzero when the key
  is already absent, so callers must diff against the fact first rather than
  unsetting unconditionally.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from template_pyinfra._cli import QuoteString, StringCommand, git_command

__all__ = [
    "GitConfigError",
    "config_changes",
    "config_commands",
    "parse_config_list",
    "set_config_entry",
    "unset_config_entry",
    "validate_config_key",
    "validate_repository_path",
]

# `<section>.<name>` or `<section>.<subsection>.<name>`: sections and names are
# restricted to alphanumerics and dashes (names must start with a letter),
# while a subsection is free-form and may contain dots and spaces.
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9-]+\.(?:.+\.)?[A-Za-z][A-Za-z0-9-]*$")


class GitConfigError(RuntimeError):
    """Raised when git configuration cannot be parsed or reconciled safely."""


def parse_config_list(payload: str, *, source: str = "") -> dict[str, str]:
    """Parse ``git config --list --null`` output into ``{key: value}``.

    ``payload`` is the raw NUL-delimited stdout with its lines already
    rejoined — values legitimately contain newlines, so the caller must not
    process the output line by line. ``source`` names the repository in error
    messages so one bad host is identifiable in a multi-host run.

    A key listed more than once (git allows multi-valued keys) keeps its last
    value, matching what ``git config --get`` reports. A valueless key raises
    :class:`GitConfigError` rather than being guessed at as an implicit
    boolean: this package manages explicit ``key = value`` entries only.
    """

    entries: dict[str, str] = {}
    for record in payload.split("\0"):
        if not record:
            continue
        key, separator, value = record.partition("\n")
        if not separator:
            raise GitConfigError(
                f"git config entry {record!r} in {source or 'the repository'} has no value; "
                "only explicit `key = value` entries are supported",
            )
        if not key:
            raise GitConfigError(f"git config entry in {source or 'the repository'} has no key")
        entries[key] = value
    return entries


def validate_config_key(key: str) -> None:
    """Reject keys ``git config`` would not accept as ``section.name``."""

    if not _KEY_PATTERN.match(key):
        raise GitConfigError(
            f"invalid git config key {key!r}: expected `section.name` or `section.subsection.name`",
        )


def validate_repository_path(path: str) -> None:
    """Reject a repository path that cannot be passed to ``git -C``.

    :func:`~template_pyinfra._cli.git_command` guards the same thing, but it
    is reached first from inside fact collection, where the failure surfaces
    as an unhandled error rather than a rejected operation argument. Checking
    here lets the operation reject the path up front.
    """

    if not path:
        raise GitConfigError("repository path must not be empty")
    if path.startswith("-"):
        raise GitConfigError(
            f"invalid repository path {path!r}: git would parse it as a command-line option",
        )


def config_changes(
    current: Mapping[str, str],
    desired: Mapping[str, str | None],
) -> dict[str, str | None]:
    """Return the subset of ``desired`` that differs from ``current``.

    A ``None`` value means "unset"; it is reported only when the key is
    actually present, so an already-absent key produces no change. Keys are
    ordered deterministically so a run yields the same commands every time.
    """

    changes: dict[str, str | None] = {}
    for key, value in sorted(desired.items()):
        if value is None:
            if key in current:
                changes[key] = None
        elif current.get(key) != value:
            changes[key] = value
    return changes


def set_config_entry(*, path: str, key: str, value: str) -> StringCommand:
    """Build ``git -C <path> config --local --replace-all <key> <value>``.

    ``--replace-all`` creates the key when it is missing and collapses a
    multi-valued key to exactly ``value``, so this one command covers both
    creation and update.
    """

    validate_config_key(key)
    return git_command(
        "config",
        "--local",
        "--replace-all",
        QuoteString(key),
        QuoteString(value),
        path=path,
    )


def unset_config_entry(*, path: str, key: str) -> StringCommand:
    """Build ``git -C <path> config --local --unset-all <key>``.

    ``--unset-all`` exits nonzero on a key that is already absent, so callers
    must confirm from the fact that the key exists before yielding this.
    """

    validate_config_key(key)
    return git_command(
        "config",
        "--local",
        "--unset-all",
        QuoteString(key),
        path=path,
    )


def config_commands(*, path: str, changes: Mapping[str, str | None]) -> list[StringCommand]:
    """Build the commands applying ``changes`` (from :func:`config_changes`)."""

    return [
        unset_config_entry(path=path, key=key)
        if value is None
        else set_config_entry(path=path, key=key, value=value)
        for key, value in changes.items()
    ]
