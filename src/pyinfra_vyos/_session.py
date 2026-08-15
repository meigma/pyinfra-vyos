"""Session-script builder for a single VyOS configure/load/commit cycle.

This is the pure domain module for the session half of ``config_load``: it
emits script text, sentinel strings, and a high-entropy staging path. There
is no I/O and no pyinfra state, which is what lets the unit tests assert the
script contract without mocks. ``operations.py`` uploads the returned text
and runs it under ``sg vyattacfg``.
"""

from __future__ import annotations

import secrets
import shlex

__all__ = [
    "SENTINEL_CHANGED",
    "SENTINEL_NOOP",
    "build_load_script",
    "staging_dir",
]

SENTINEL_CHANGED = "PYINFRA_VYOS changed"
SENTINEL_NOOP = "PYINFRA_VYOS noop"

_SCRIPT_TEMPLATE = "/opt/vyatta/etc/functions/script-template"
_NEEDS_SAVE = (
    "/bin/cli-shell-api --show-cfg1 @ACTIVE "
    "--show-cfg2 /config/config.boot --show-commands showConfig"
)


def staging_dir() -> str:
    """Return a fresh per-invocation staging path under ``/tmp``.

    The token is 128 bits of ``secrets`` entropy, never derived from config
    content, so identical-content runs cannot collide and the path does not
    leak the payload.
    """

    return f"/tmp/pyinfra-vyos-{secrets.token_hex(16)}"


def build_load_script(staging_dir: str, *, save: bool) -> str:
    """Build the vbash script that loads ``<staging_dir>/config`` in one session."""

    quoted_dir = shlex.quote(staging_dir)
    quoted_config = shlex.quote(f"{staging_dir}/config")
    lines = [
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
        f"_load_out=$(load {quoted_config})",
        "_load_rc=$?",
        'if [ "$_load_rc" -ne 0 ]; then',
        '  printf \'%s\\n\' "$_load_out" >&2',
        "  builtin exit 1",
        "fi",
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
        "  _commit_rc=$?",
        "  /bin/cli-shell-api sessionChanged",
        "  _post_rc=$?",
        '  if [ "$_post_rc" -eq 0 ]; then',
        '    printf \'%s\\n\' "$_commit_out" >&2',
        "    builtin exit 1",
        '  elif [ "$_post_rc" -eq 1 ]; then',
        "    did_commit=1",
        "  else",
        "    builtin exit 1",
        "  fi",
        "fi",
    ]
    if save:
        lines.extend(
            [
                f"_need_out=$({_NEEDS_SAVE})",
                "_need_rc=$?",
                'if [ "$_need_rc" -ne 0 ] || [ -n "$_need_out" ]; then',
                "  _save_out=$(save)",
                "  _save_rc=$?",
                f"  _after_out=$({_NEEDS_SAVE})",
                "  _after_rc=$?",
                '  if [ "$_after_rc" -ne 0 ] || [ -n "$_after_out" ]; then',
                '    printf \'%s\\n\' "$_save_out" >&2',
                "    builtin exit 1",
                "  fi",
                "  did_save=1",
                "fi",
            ]
        )
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
    return "\n".join(lines) + "\n"
