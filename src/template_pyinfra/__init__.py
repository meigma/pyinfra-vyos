"""Custom pyinfra facts and operations packaged as a reusable plugin.

The primitives exported here are a **sample domain** — repository-local
``git config`` management — chosen because ``git`` exists everywhere, needs
no daemon, and has natural idempotency (read config, diff, set or unset).
They exist to be molded into a real package, not kept.

Layer map, in the order to replace them:

- ``facts.py`` — public :class:`~pyinfra.api.FactBase` classes only.
- ``operations.py`` — public ``@operation`` functions only.
- ``_gitconfig.py`` — the pure domain: parse, diff, build commands. No I/O
  and no pyinfra state, which is what keeps the unit tests mock-free.
- ``_cli.py`` — the one place commands are assembled, and the one place the
  quoting and option-lookalike rules live. Keep this layer when the domain
  changes; only the binary name and its flags should move.

Facts and operations are ordinary importable modules: pyinfra discovers only
connectors through entry points, so nothing here needs registration. Deploys
import them directly::

    from template_pyinfra import GitConfig, config_entry
"""

from template_pyinfra.facts import GitConfig, GitVersion
from template_pyinfra.operations import config_entry

__all__ = [
    "GitConfig",
    "GitVersion",
    "config_entry",
]
