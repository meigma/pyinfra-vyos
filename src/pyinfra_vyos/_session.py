"""Session-script builders for single VyOS configure/commit cycles.

This is the pure domain module for the session half of the mutation
operations: it emits script text, sentinel strings, and a high-entropy
staging path. There is no I/O and no pyinfra state, which is what lets the
unit tests assert the script contract without mocks. ``operations.py``
uploads the returned text and runs it under ``sg vyattacfg``.

The load and commands builders share one contract: pager disabled before
sourcing, the script-template sourced (after which ``exit`` is an alias and
every terminating path must use ``builtin exit``), an EXIT trap that tears
down the session and removes staging without masking a prior failure, an
``inSession`` assertion, a ``sessionChanged`` tri-state commit gate, an
optional idempotent save block, and a truthful changed/noop sentinel.

``build_save_script`` shares the pager, source, trap, ``inSession``, and
sentinel pieces but has no commit gate and no config commands. ``save`` is a
config-mode word, so the script still opens a configure session; the EXIT
trap owns teardown and staging removal. Its save block is ungated:
``did_save`` drives ``SENTINEL_CHANGED``, else ``SENTINEL_NOOP``.
``build_load_script`` also leaves save ungated, so a save-only load can
persist the boot file. ``build_commands_script`` wraps the same save block
in ``did_commit``, so a no-op commands session never writes config.boot.

Secrets stay off argv (C3): ``build_commands_script`` embeds ``set`` /
``delete`` commands in the script text itself, which travels as an uploaded
0600 file, never as process argv.
"""

from __future__ import annotations

import secrets
import shlex
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "NEEDS_SAVE_COMMAND",
    "SENTINEL_CHANGED",
    "SENTINEL_NOOP",
    "PlannedCommand",
    "build_commands_script",
    "build_load_script",
    "build_save_script",
    "staging_dir",
]

SENTINEL_CHANGED = "PYINFRA_VYOS changed"
SENTINEL_NOOP = "PYINFRA_VYOS noop"

_SCRIPT_TEMPLATE = "/opt/vyatta/etc/functions/script-template"
NEEDS_SAVE_COMMAND = (
    "/bin/cli-shell-api --show-cfg1 @ACTIVE "
    "--show-cfg2 /config/config.boot --show-commands showConfig"
)

_SUPPRESSED_OUTPUT = "device output suppressed (sensitive command)"


@dataclass(frozen=True)
class PlannedCommand:
    """One config-mode command to embed in a session script.

    ``argv`` is a ``set`` / ``delete`` token sequence. ``sensitive`` replaces
    captured device output with a fixed suppression line on failure (D11).
    """

    argv: list[str]
    sensitive: bool = False


def staging_dir() -> str:
    """Return a fresh per-invocation staging path under ``/tmp``.

    The token is 128 bits of ``secrets`` entropy, never derived from config
    content, so identical-content runs cannot collide and the path does not
    leak the payload.
    """

    return f"/tmp/pyinfra-vyos-{secrets.token_hex(16)}"


def _prologue(quoted_dir: str) -> list[str]:
    """Pager, template, cleanup trap, and session-entry assertion."""

    return [
        "export VYATTA_PAGER=cat",
        f"source {_SCRIPT_TEMPLATE}",
        "did_commit=0",
        "did_save=0",
        "_pyinfra_vyos_cleanup() {",
        "  rc=$?",
        "  trap - EXIT",
        "  _cleanup_failed=0",
        "  if /bin/cli-shell-api inSession; then",
        "    /bin/cli-shell-api teardownSession || _cleanup_failed=1",
        "  fi",
        f"  rm -rf {quoted_dir} || _cleanup_failed=1",
        '  if [ "$_cleanup_failed" -ne 0 ] && [ "$rc" -eq 0 ]; then',
        "    rc=1",
        "  fi",
        '  builtin exit "$rc"',
        "}",
        "trap _pyinfra_vyos_cleanup EXIT",
        "configure",
        "if ! /bin/cli-shell-api inSession; then",
        "  builtin exit 1",
        "fi",
    ]


def _commit_gate() -> list[str]:
    """sessionChanged tri-state gate, commit, and commit postcondition."""

    return [
        "/bin/cli-shell-api sessionChanged",
        "_changed_rc=$?",
        'if [ "$_changed_rc" -eq 0 ]; then',
        "  :",
        'elif [ "$_changed_rc" -eq 1 ]; then',
        "  :",
        "else",
        "  builtin exit 1",
        "fi",
        'if [ "$_changed_rc" -eq 0 ]; then',
        "  _commit_out=$(commit)",
        "  /bin/cli-shell-api sessionChanged",
        "  _post_rc=$?",
        '  if [ "$_post_rc" -eq 0 ]; then',
        "    printf '%s\\n' \"$_commit_out\" >&2",
        "    builtin exit 1",
        '  elif [ "$_post_rc" -eq 1 ]; then',
        "    did_commit=1",
        "  else",
        "    builtin exit 1",
        "  fi",
        "fi",
    ]


def _save_block() -> list[str]:
    """Idempotent needs-save check, save, and save postcondition."""

    return [
        f"_need_out=$({NEEDS_SAVE_COMMAND})",
        "_need_rc=$?",
        'if [ "$_need_rc" -ne 0 ] || [ -n "$_need_out" ]; then',
        "  _save_out=$(save)",
        f"  _after_out=$({NEEDS_SAVE_COMMAND})",
        "  _after_rc=$?",
        '  if [ "$_after_rc" -ne 0 ] || [ -n "$_after_out" ]; then',
        "    printf '%s\\n' \"$_save_out\" >&2",
        "    builtin exit 1",
        "  fi",
        "  did_save=1",
        "fi",
    ]


def _epilogue(*, save: bool, gate_save_on_commit: bool = False) -> list[str]:
    """Optional idempotent save block, sentinel emission, and exit.

    When *gate_save_on_commit* is true the save block runs only if this
    session committed; load and save-only scripts leave it ungated.
    """

    lines: list[str] = []
    if save:
        save_lines = _save_block()
        if gate_save_on_commit:
            lines.append('if [ "$did_commit" -ne 0 ]; then')
            lines.extend(f"  {line}" for line in save_lines)
            lines.append("fi")
        else:
            lines.extend(save_lines)
    lines.extend(
        [
            'if [ "$did_commit" -ne 0 ] || [ "$did_save" -ne 0 ]; then',
            f"  printf '%s\\n' '{SENTINEL_CHANGED}'",
            "else",
            f"  printf '%s\\n' '{SENTINEL_NOOP}'",
            "fi",
            "builtin exit 0",
        ]
    )
    return lines


def build_load_script(staging_dir: str, *, save: bool) -> str:
    """Build the vbash script that loads ``<staging_dir>/config`` in one session."""

    quoted_dir = shlex.quote(staging_dir)
    quoted_config = shlex.quote(f"{staging_dir}/config")
    lines = _prologue(quoted_dir)
    lines.extend(
        [
            f"_load_out=$(load {quoted_config})",
            "_load_rc=$?",
            'if [ "$_load_rc" -ne 0 ]; then',
            "  printf '%s\\n' \"$_load_out\" >&2",
            "  builtin exit 1",
            "fi",
        ]
    )
    lines.extend(_commit_gate())
    lines.extend(_epilogue(save=save))
    return "\n".join(lines) + "\n"


def build_commands_script(
    staging_dir: str,
    commands: Sequence[PlannedCommand],
    *,
    save: bool,
) -> str:
    """Build the vbash script applying config-mode *commands* in one session.

    Each command is a :class:`PlannedCommand` whose ``argv`` begins with
    ``set`` or ``delete``; every token is shell-quoted into the script text.
    Command output is captured and each rc is checked directly — a nonzero
    rc fails the session with the captured output on stderr (wrapper rc
    masking makes zero untrustworthy, but nonzero is always a real failure;
    the commit gate's ``sessionChanged`` remains the authoritative change
    signal). Sensitive commands print a fixed suppression line instead of
    captured output (D11). Save, when requested, runs only if this session
    committed.
    """

    if not commands:
        raise ValueError("commands must be nonempty")
    for command in commands:
        if not command.argv or command.argv[0] not in ("set", "delete"):
            raise ValueError(f"unsupported config-mode command: {list(command.argv)!r}")

    quoted_dir = shlex.quote(staging_dir)
    lines = _prologue(quoted_dir)
    for index, command in enumerate(commands, start=1):
        rendered = " ".join([command.argv[0], *(shlex.quote(token) for token in command.argv[1:])])
        # The failure line names only the command's ordinal and verb: the
        # full command can carry secret values (C3) and must not reach
        # pyinfra's failure logs. Captured device output is the diagnostic
        # unless the command is sensitive (D11).
        failed = shlex.quote(f"pyinfra-vyos: config command {index} ({command.argv[0]}) failed")
        if command.sensitive:
            # Merge device stderr into the capture so a failing sensitive
            # command cannot leak secret-bearing diagnostics to pyinfra.
            capture = f"_cmd_out=$({rendered} 2>&1)"
            diagnostic = f"  printf '%s\\n' {shlex.quote(_SUPPRESSED_OUTPUT)} >&2"
        else:
            capture = f"_cmd_out=$({rendered})"
            diagnostic = "  printf '%s\\n' \"$_cmd_out\" >&2"
        lines.extend(
            [
                capture,
                "_cmd_rc=$?",
                'if [ "$_cmd_rc" -ne 0 ]; then',
                f"  printf '%s\\n' {failed} >&2",
                diagnostic,
                "  builtin exit 1",
                "fi",
            ]
        )
    lines.extend(_commit_gate())
    lines.extend(_epilogue(save=save, gate_save_on_commit=True))
    return "\n".join(lines) + "\n"


def build_save_script(staging_dir: str) -> str:
    """Build the vbash script that persists config.boot in one configure session.

    There are no config commands and no commit gate. The needs-save check
    and ``save`` run unconditionally; ``did_save`` selects the changed/noop
    sentinel. The EXIT trap owns teardown and staging removal.
    """

    quoted_dir = shlex.quote(staging_dir)
    lines = _prologue(quoted_dir)
    lines.extend(_epilogue(save=True))
    return "\n".join(lines) + "\n"
