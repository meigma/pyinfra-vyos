"""Shared construction helpers for target CLI commands.

Every fact and operation in this package runs commands on the target host
through pyinfra's connector. This module is the single place where those
commands are assembled; domain modules build commands with these helpers and
never execute anything themselves.

Wave-1 contract — architecture-sanctioned collapse of the template's argv
machinery. This wave has no user-supplied argv: callers pass only
library-generated tokens (op-mode words, package markers, staging paths).
There is therefore no ``_stdin`` plumbing and no leading-dash rejection.
``QuoteString`` is applied here to interpolated staging and script paths;
everything else is a trusted literal. pyinfra still executes the rendered
string via ``sh -c``, so ``vbash -c`` / ``sg … -c`` payloads are
shell-quoted in this module.
"""

from __future__ import annotations

from pyinfra.api import QuoteString, StringCommand

__all__ = [
    "QuoteString",
    "StringCommand",
    "sg_probe",
    "sg_vbash_run",
    "vyos_op_command",
]

_SCRIPT_TEMPLATE = "/opt/vyatta/etc/functions/script-template"
_VBASH = "/bin/vbash"


def _single_quote(value: str) -> str:
    """Wrap *value* in POSIX single quotes for an outer ``sh -c``.

    An embedded ``'`` becomes ``'\\''`` (end quote, escaped quote, resume).
    """

    return "'" + value.replace("'", "'\\''") + "'"


def vyos_op_command(*argv: str, marker: str) -> StringCommand:
    """Wrap bare op-mode *argv* as a ``vbash -c`` script with a trailing marker.

    pyinfra executes via ``sh -c``, where VyOS op-mode commands do not exist.
    This helper is the only place ``run`` is added: the inner script sources
    script-template, invokes ``run <argv…>`` once, and on success emits
    *marker* on its own line via ``printf`` chained with ``&&`` so the real
    command's exit status propagates. Callers pass VyOS op-pipe tokens
    (``\\|``, ``strip-private``) as ordinary argv; they are rendered
    literally. The inner script as a whole is POSIX-single-quote-escaped for
    the outer shell.
    """

    inner = StringCommand(
        f"source {_SCRIPT_TEMPLATE};",
        "run",
        *argv,
        "&&",
        "printf",
        "'\\n%s\\n'",
        marker,
    )
    return StringCommand("vbash", "-c", _single_quote(inner.get_raw_value()))


def sg_probe() -> StringCommand:
    """Build the preflight ``sg vyattacfg`` probe, with stdin closed.

    Confirms the connecting user can ``sg`` into ``vyattacfg`` and that
    ``/bin/vbash`` and script-template are present, before any secret is
    staged. ``</dev/null`` also prevents a group-password prompt from hanging.
    """

    inner = f"test -x {_VBASH} && test -r {_SCRIPT_TEMPLATE}"
    return StringCommand("sg", "vyattacfg", "-c", _single_quote(inner), "</dev/null")


def sg_vbash_run(script_path: str, staging_dir: str) -> StringCommand:
    """Run an uploaded session script as ``vyattacfg``, then remove *staging_dir*.

    Renders as ``sg vyattacfg -c "/bin/vbash <script_path>" </dev/null``
    followed by ``rc=$?; rm -rf <staging_dir>; exit $rc`` so cleanup runs
    even when the script fails. *script_path* and *staging_dir* are
    :class:`QuoteString`-wrapped; they are library-generated staging paths.
    """

    return StringCommand(
        f'sg vyattacfg -c "{_VBASH} ',
        QuoteString(script_path),
        '" </dev/null; rc=$?; rm -rf ',
        QuoteString(staging_dir),
        "; exit $rc",
        _separator="",
    )
