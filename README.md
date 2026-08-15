# pyinfra-vyos

`pyinfra-vyos` is a [pyinfra](https://pyinfra.com) plugin: facts plus two
configuration operations for VyOS over SSH — a whole-config `config_load`
and a scoped `config` for owned subtrees. Store a complete device config in
git and load it as one unit, or declare just the subtrees you own, rather
than issuing incremental `set` commands from the controller.

Requires Python 3.11+ and pyinfra 3.9.2+. Targets need `vbash` and the VyOS
script-template substrate; the mutation operations additionally need the
connecting user to be able to `sg` into the `vyattacfg` group. Nothing is
installed on the appliance.

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

### Scoped subtree management

`config` owns one config path and manages the subtree beneath it. `values`
mirrors `show configuration json` shapes: nested dict for a subtree, `{}`
for a valueless node, a string for a single-value leaf, a list for a
multi-value leaf.

```python
# deploy_ntp.py
from pyinfra_vyos import config

config(
    name="Manage NTP servers",
    path=["service", "ntp"],
    values={"server": {"time1.example.net": {}, "time2.example.net": {}}},
    replace=True,
    save=True,
)
```

By default omitted state is unmanaged: only missing or differing desired
values are `set` (merge). With `replace=True` the subtree becomes exactly
`values` — extra active keys and leaf values are deleted, so choose the
owned `path` carefully: a broad path with `replace=True` can remove
management access. `present=False` deletes the whole path.

The desired subtree is diffed against the active tree on the controller;
an empty delta noops without touching the device, so pyinfra's change
reporting is honest for this operation. Applied deltas are staged in one
configure session and committed once behind the same `sessionChanged` gate
as `config_load`. Every path token, key, and value must be a nonempty
string that does not begin with `-`. Multi-value ordering is not managed.

## Contracts

These are user-facing, not internals:

1. **Serialize mutations per host.** The caller must run at most one
   mutation session (`config_load` or `config`) at a time against a given
   device, including concurrent runs from the same controller. Overlapping
   mutations are out of contract. VyOS has no documented session lock;
   this package does not invent one.
2. **Treat controller logs as sensitive.** Config output and facts
   (`Configuration`, unredacted `ConfigurationCommands`) can reach returned
   fact values, verbose fact output, failed-fact combined output, and
   operation failure diagnostics. The library cannot enforce "never log".
3. **Supply a footer-bearing config.** Callers should ship a file that
   includes `// vyos-config-version` — the same footer `/config/config.boot`
   carries. The library does not detect or inject it.
4. **pyinfra always reports `config_load` changed.** It is marked
   `is_idempotent=False`: the executed command list is nonempty, so
   pyinfra's change flag is pessimistic. Device sentinels in operation
   stdout (`PYINFRA_VYOS changed` or `PYINFRA_VYOS noop`) are the truthful
   answer. A save-only run (no candidate diff, boot file still written)
   reports `changed` via the sentinel, never `noop`. `config` noops
   honestly via its controller-side diff; when it does send a delta the
   device may still canonicalize it to a truthful `noop` sentinel — in
   that case supply the device-canonical value form to stop the re-sends.

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

On a Mac with [Lima](https://lima-vm.io) installed, the bundled lab brings
up a disposable local appliance and runs the tier against it (see
[tests/appliance/README.md](tests/appliance/README.md)):

```sh
tests/appliance/vyos-lab test
tests/appliance/vyos-lab down
```

`root:check` runs the unit tier only. The `@local` integration tier has
its own CI workflow.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup (mise, uv, moon)
and pull request workflow.

## Security

See [SECURITY.md](SECURITY.md) for supported versions and the private
vulnerability reporting path.
## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT license ([LICENSE-MIT](LICENSE-MIT))

at your option.

Unless you explicitly state otherwise, any contribution intentionally
submitted for inclusion in this project by you, as defined in the
Apache-2.0 license, shall be dual licensed as above, without any
additional terms or conditions.
