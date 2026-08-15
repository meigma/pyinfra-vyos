"""VyOS facts and operations packaged as a reusable pyinfra plugin.

The primitives exported here are the wave-1 substrate: a whole-config load
operation and three op-mode facts, all over ``sg vyattacfg`` + ``/bin/vbash``
+ script-template. Callers compose SOPS, templating, backup, and verification
on top; this package does not.

Layer map:

- ``facts.py`` — public :class:`~pyinfra.api.FactBase` classes only:
  :class:`Version`, :class:`Configuration`, :class:`ConfigurationCommands`.
- ``operations.py`` — public ``@operation`` functions only: :func:`config_load`.
- ``_session.py`` — the pure domain for the session half: script text,
  sentinels, and the high-entropy staging path. No I/O and no pyinfra state.
- ``_parse.py`` — the pure domain for the parse half: ``show version`` /
  config-JSON / command-line parsers, marker strip, streaming non-empty
  check. No I/O and no pyinfra state.
- ``_cli.py`` — the one place target commands are assembled
  (``vyos_op_command``, ``sg_probe``, ``sg_vbash_run``).

Facts and operations are ordinary importable modules: pyinfra discovers only
connectors through entry points, so nothing here needs registration. Deploys
import them directly::

    from pyinfra_vyos import Version, Configuration, ConfigurationCommands, config_load
"""

from pyinfra_vyos.facts import Configuration, ConfigurationCommands, Version
from pyinfra_vyos.operations import config_load

__all__ = [
    "Version",
    "Configuration",
    "ConfigurationCommands",
    "config_load",
]
