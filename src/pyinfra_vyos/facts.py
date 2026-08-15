"""VyOS facts collected by running op-mode commands on each host.

Every fact runs a VyOS op-mode command through the host's pyinfra connector
— ``@local``, SSH, or any other — via :func:`vyos_op_command`, and parses
what it prints. Targets need the ``vbash`` binary; no Python and no package
install on the appliance.

:meth:`~pyinfra.api.facts.FactBase.requires_command` is a binary-presence
gate only. Hosts without ``vbash`` yield :meth:`~pyinfra.api.facts.FactBase.default`
instead of failing. The gate does not establish that the host is a VyOS
appliance or that op-mode commands are compatible.

The facts here demonstrate the two canonical shapes a pyinfra fact takes:
:class:`Version` and :class:`Configuration` are argument-less with a
straightforward ``process()``, while :class:`ConfigurationCommands` is
parameterized and must carry its arguments from ``command()`` to
``process()`` when ``process()`` needs them. :class:`PendingSave` is
argument-less with a tri-state ``bool | None`` result.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from pyinfra.api import FactBase, StringCommand
from pyinfra.api.exceptions import FactProcessError

from pyinfra_vyos._cli import pending_save_probe, vyos_op_command
from pyinfra_vyos._parse import (
    OUTPUT_MARKER,
    config_command_lines,
    parse_config_json,
    parse_pending_save,
    parse_version,
    strip_marker,
)

__all__ = ["Configuration", "ConfigurationCommands", "PendingSave", "Version"]

_P = ParamSpec("_P")
_T = TypeVar("_T")


def _fact_process(process: Callable[_P, _T]) -> Callable[_P, _T]:
    """Surface processing failures through pyinfra's per-host fact-failure path.

    pyinfra contains only :class:`FactProcessError` around ``fact.process()``;
    any other exception escaping ``process()`` aborts the entire multi-host
    run. Malformed CLI output (a missing package marker, a ``show version``
    payload without a version field, invalid configuration JSON) must instead
    fail only the affected host — logged as a fact failure, honoring
    ``_ignore_errors`` and ``default()``.
    """

    @wraps(process)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        try:
            return process(*args, **kwargs)
        except FactProcessError:
            raise
        except Exception as error:
            raise FactProcessError(f"invalid VyOS fact output: {error}") from error

    return wrapper


class Version(FactBase[dict]):
    """Return the target's ``show version`` fields as a label-to-value mapping.

    Runs ``show version`` through :func:`vyos_op_command`, which appends the
    package output marker. :meth:`process` requires and strips that marker
    before parsing. Labels are normalized (lowercased, spaces to
    underscores). The ``version`` field is required; unknown labels and
    missing optionals are kept or omitted as the parser produces them.

    Hosts without ``vbash`` yield :meth:`default` rather than failing.
    :meth:`requires_command` is a binary-presence gate only — it does not
    establish that the host is a VyOS appliance or that op-mode commands
    are compatible.
    """

    @staticmethod
    def default() -> dict:
        return {}

    def requires_command(self, *args: object, **kwargs: object) -> str:
        """Return ``vbash`` as a binary-presence gate, not a VyOS-ness check."""
        return "vbash"

    def command(self) -> StringCommand:
        return vyos_op_command("show", "version", marker=OUTPUT_MARKER)

    @_fact_process
    def process(self, output: list[str]) -> dict:
        return parse_version(strip_marker(output))


class Configuration(FactBase[dict]):
    """Return the target's running configuration as the raw JSON tree.

    Runs ``show configuration json`` through :func:`vyos_op_command`, which
    appends the package output marker. :meth:`process` requires and strips
    that marker, rejoins the payload (pyinfra splits stdout on newlines),
    and returns the tree as loaded — no key or value normalization.

    This fact is secret-bearing. Returned fact values, verbose fact output,
    failed-fact combined output, and operation failure diagnostics can all
    reach controller logs. The library cannot enforce "never log"; callers
    must treat controller logs as sensitive. Failure output is kept minimal
    and this package never prints config diffs.

    Hosts without ``vbash`` yield :meth:`default` rather than failing.
    :meth:`requires_command` is a binary-presence gate only — it does not
    establish that the host is a VyOS appliance or that op-mode commands
    are compatible.
    """

    @staticmethod
    def default() -> dict:
        return {}

    def requires_command(self, *args: object, **kwargs: object) -> str:
        """Return ``vbash`` as a binary-presence gate, not a VyOS-ness check."""
        return "vbash"

    def command(self) -> StringCommand:
        return vyos_op_command("show", "configuration", "json", marker=OUTPUT_MARKER)

    @_fact_process
    def process(self, output: list[str]) -> dict:
        return parse_config_json("\n".join(strip_marker(output)))


class ConfigurationCommands(FactBase[list]):
    """Return the target's configuration as device-rendered set-form lines.

    Runs ``show configuration commands`` through :func:`vyos_op_command`,
    which appends the package output marker. When ``strip_private`` is true,
    output is piped through the target's ``strip-private`` filter script (a
    real shell pipeline; the interactive op pipe is not available to
    non-interactive ``run``). :meth:`process` requires and strips the
    marker, then keeps nonempty device-rendered lines as-is.

    Unredacted output is secret-bearing. Returned fact values, verbose fact
    output, failed-fact combined output, and operation failure diagnostics
    can all reach controller logs. The library cannot enforce "never log";
    callers must treat controller logs as sensitive. ``strip_private``
    output is not restore-faithful and must not be used as a backup.

    Hosts without ``vbash`` yield :meth:`default` rather than failing.
    :meth:`requires_command` is a binary-presence gate only — it does not
    establish that the host is a VyOS appliance or that op-mode commands
    are compatible.
    """

    @staticmethod
    def default() -> list:
        return []

    def requires_command(self, *args: object, **kwargs: object) -> str:
        """Return ``vbash`` as a binary-presence gate, not a VyOS-ness check."""
        return "vbash"

    def command(self, strip_private: bool = False) -> StringCommand:
        return vyos_op_command(
            "show",
            "configuration",
            "commands",
            marker=OUTPUT_MARKER,
            strip_private=strip_private,
        )

    @_fact_process
    def process(self, output: list[str]) -> list:
        return config_command_lines(strip_marker(output))


class PendingSave(FactBase[bool | None]):
    """Whether the active config differs from the saved boot config.

    Tri-state contract:

    - ``None`` — comparison unavailable (missing ``vbash``, command failure,
      unparseable output). ``config_save`` fails closed on this value.
    - ``False`` — comparison ran and there is no active-vs-boot diff.
    - ``True`` — unsaved active changes.

    Any failure path must end in ``None``, never ``False``. Hosts without
    ``vbash`` yield :meth:`default` rather than failing.
    :meth:`requires_command` is a binary-presence gate only — it does not
    establish that the host is a VyOS appliance or that op-mode commands
    are compatible.
    """

    @staticmethod
    def default() -> None:
        return None

    def requires_command(self, *args: object, **kwargs: object) -> str:
        """Return ``vbash`` as a binary-presence gate, not a VyOS-ness check."""
        return "vbash"

    def command(self) -> StringCommand:
        return pending_save_probe(OUTPUT_MARKER)

    @_fact_process
    def process(self, output: list[str]) -> bool:
        return parse_pending_save("\n".join(strip_marker(output)))
