# pyinfra-vyos

`pyinfra-vyos` is a [pyinfra](https://pyinfra.com) plugin: facts plus a
whole-config `config_load` operation for VyOS over SSH. Store a complete
device config in git and load it as one unit, rather than issuing
incremental `set` commands from the controller.

Requires Python 3.11+ and pyinfra 3.9.2+. Targets need `vbash` and the VyOS
script-template substrate; `config_load` additionally needs the connecting
user to be able to `sg` into the `vyattacfg` group. Nothing is installed
on the appliance.

## Install

The first PyPI release is pending. Until then, install from the repository:

```sh
pip install git+https://github.com/meigma/pyinfra-vyos
```

After the first release:

```sh
pip install pyinfra-vyos
```

With [uv](https://docs.astral.sh/uv/):

```sh
uv add pyinfra-vyos
```

## Quickstart

Inventory is ordinary pyinfra SSH:

```python
# inventory.py
hosts = [("vyos.example.net", {"ssh_user": "vyos"})]
```

The three facts wrap op-mode `show` commands. `config_load` uploads a
controller-local file and runs one configure / load / commit session.

```python
# facts.py
from pyinfra import host

from pyinfra_vyos import Configuration, ConfigurationCommands, Version

version = host.get_fact(Version)
tree = host.get_fact(Configuration)
commands = host.get_fact(ConfigurationCommands)
redacted = host.get_fact(ConfigurationCommands, strip_private=True)
```

```sh
pyinfra inventory.py facts.py
```

`Configuration` and unredacted `ConfigurationCommands` are secret-bearing.
`strip_private=True` is a VyOS op-pipe redaction; that output is not
restore-faithful and must not be used as a backup.

### Commit, verify, then save

A bad full config can sever SSH. The canonical pattern is two deploys with
an external check in between: commit without writing `/config/config.boot`,
verify reachability and facts, then load again with `save=True`.

`src` should be a footer-bearing config (`// vyos-config-version`). Use a
`/config/config.boot`-style source (or `save <file>` output). Bare
`show configuration` output has no footer; VyOS `load` treats that as
version 0 and runs the full migration chain.

```python
# deploy_commit.py
from pyinfra_vyos import config_load

config_load("configs/edge.conf")  # save=False
```

```python
# deploy_save.py
from pyinfra_vyos import config_load

config_load("configs/edge.conf", save=True)
```

```sh
pyinfra inventory.py deploy_commit.py
# verify SSH reachability and re-gather facts
pyinfra inventory.py facts.py
pyinfra inventory.py deploy_save.py
```

`save` is keyword-only. `config_load(src, True)` is not a valid call.

## Contracts

These are user-facing, not internals:

1. **Serialize `config_load` per host.** The caller must run at most one
   mutation session at a time against a given device, including concurrent
   runs from the same controller. Overlapping mutations are out of contract.
   VyOS has no documented session lock; this package does not invent one.
2. **Treat controller logs as sensitive.** Config output and facts
   (`Configuration`, unredacted `ConfigurationCommands`) can reach returned
   fact values, verbose fact output, failed-fact combined output, and
   operation failure diagnostics. The library cannot enforce "never log".
3. **Supply a footer-bearing config.** Callers should ship a file that
   includes `// vyos-config-version` — the same footer `/config/config.boot`
   carries. The library does not detect or inject it.
4. **pyinfra always reports the op changed.** `config_load` is marked
   `is_idempotent=False`: the executed command list is nonempty, so
   pyinfra's change flag is pessimistic. Device sentinels in operation
   stdout (`PYINFRA_VYOS changed` or `PYINFRA_VYOS noop`) are the truthful
   answer. A save-only run (no candidate diff, boot file still written)
   reports `changed` via the sentinel, never `noop`.

If a yielded command fails before the session runs — upload, chmod, the
remote non-whitespace guard — or the connector drops, the 0600/0700
staging directory under `/tmp` is left behind. Paths that reach session
execution are cleaned up.

## Testing

**Unit** is the default, mock-free tier: rendered commands, fact
`process()` over literal output, and pure domain functions.

```sh
moon run root:test
```

**Integration** (`--integration`) drives the real pyinfra API against
`@local`. It covers prepare-phase rendering and graceful degradation
(facts without `vbash` return `default()`). It does not talk to a VyOS
device.

```sh
moon run root:test-integration
```

**Appliance** is opt-in and not wired into CI. It mutates a live VyOS
host (load / commit / save, then restore from `/config/config.boot`).
Run it only against a **dedicated lab device**, never production. Both
`--appliance` and `PYINFRA_VYOS_TEST_HOST` are required:

```sh
export PYINFRA_VYOS_TEST_HOST=vyos-lab.example.net
# optional: PYINFRA_VYOS_TEST_USER, PYINFRA_VYOS_TEST_PORT, PYINFRA_VYOS_TEST_KEY
# optional: PYINFRA_VYOS_TEST_CAPTURE_DIR (captured `show version` fixture)
uv run --locked pytest --appliance tests/integration
```

`root:check` runs the unit tier only. The `@local` integration tier has
its own CI workflow.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup (mise, uv, moon)
and pull request workflow.

## Security

See [SECURITY.md](SECURITY.md) for supported versions and the private
vulnerability reporting path.