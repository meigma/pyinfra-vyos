"""VyOS facts and operations packaged as a reusable pyinfra plugin.

The primitives exported here are the wave-2 surface: a whole-config load,
a scoped subtree operation, a persist phase, seven typed operations, and
four op-mode facts, all over ``sg vyattacfg`` + ``/bin/vbash`` +
script-template. Callers compose SOPS, templating, backup, and
verification on top; this package does not.

Layer map:

- ``facts.py`` — public :class:`~pyinfra.api.FactBase` classes only:
  :class:`Version`, :class:`Configuration`, :class:`ConfigurationCommands`,
  :class:`PendingSave`.
- ``operations.py`` — public ``@operation`` functions only:
  :func:`config`, :func:`config_load`, :func:`config_save`,
  :func:`firewall_group`, :func:`firewall_ruleset`, :func:`interface`,
  :func:`static_route`, :func:`system_basics`, :func:`user`.
- ``_render.py`` — the pure domain for typed-op rendering: ``Scope``
  algebra, schema-key mapping, and per-op renderer functions.
- ``_session.py`` — the pure domain for the session half: script text,
  sentinels, and the high-entropy staging path. No I/O and no pyinfra state.
- ``_parse.py`` — the pure domain for the parse half: ``show version`` /
  config-JSON / command-line parsers, marker strip, streaming non-empty
  check. No I/O and no pyinfra state.
- ``_tree.py`` — the pure domain for scoped config state: desired-tree
  validation, active-subtree selection, and the set/delete diff.
- ``_cli.py`` — the one place target commands are assembled
  (``vyos_op_command``, ``sg_probe``, ``sg_vbash_run``).

Facts and operations are ordinary importable modules: pyinfra discovers only
connectors through entry points, so nothing here needs registration. Deploys
import them directly::

    from pyinfra_vyos import Configuration, config
"""

from pyinfra_vyos.facts import Configuration, ConfigurationCommands, PendingSave, Version
from pyinfra_vyos.operations import (
    config,
    config_load,
    config_save,
    firewall_group,
    firewall_ruleset,
    interface,
    static_route,
    system_basics,
    user,
)

__all__ = [
    "Configuration",
    "ConfigurationCommands",
    "PendingSave",
    "Version",
    "config",
    "config_load",
    "config_save",
    "firewall_group",
    "firewall_ruleset",
    "interface",
    "static_route",
    "system_basics",
    "user",
]
