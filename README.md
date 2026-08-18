# pyinfra-vyos

`pyinfra-vyos` is a [pyinfra](https://pyinfra.com) plugin with nine
configuration operations and four facts for VyOS over SSH. The operation
surface includes a whole-config load, a scoped generic operation, a separate
persist phase, and six typed operations. Store a complete device config in
git and load it as one unit, or declare just the state you own, rather than
issuing incremental `set` commands from the controller.

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

The four facts report the device version, active configuration, device-rendered
configuration commands, and whether the active configuration has unsaved
changes. `config_load` uploads a controller-local file and runs one configure /
load / commit session.

```python
# facts.py
from pyinfra import host

from pyinfra_vyos import (
    Configuration,
    ConfigurationCommands,
    PendingSave,
    Version,
)

version = host.get_fact(Version)
tree = host.get_fact(Configuration)
commands = host.get_fact(ConfigurationCommands)
redacted = host.get_fact(ConfigurationCommands, strip_private=True)
pending_save = host.get_fact(PendingSave)
```

```sh
pyinfra inventory.py facts.py
```

`Configuration` and unredacted `ConfigurationCommands` are secret-bearing.
`strip_private=True` is a VyOS op-pipe redaction; that output is not
restore-faithful and must not be used as a backup.

### Commit, verify, then persist

A successful commit changes the active configuration immediately and can sever
SSH. `save=False` limits reboot persistence only. It is not a dry run and does
not protect against lockout.

For risky changes, commit without saving, verify reachability and facts, then
run `config_save()` as a separate persist phase:

```python
# deploy_commit.py
from pyinfra_vyos import config_load

config_load("configs/edge.conf")  # save=False
```

```python
# deploy_save.py
from pyinfra_vyos import config_save

config_save()
```

```sh
pyinfra inventory.py deploy_commit.py
# verify SSH reachability and re-gather facts
pyinfra inventory.py facts.py
pyinfra inventory.py deploy_save.py
```

The same persist phase follows a typed operation or `config` run with
`save=False`. An identical second typed call noops and cannot persist the
earlier commit, regardless of its `save` argument. `config_save` reads the
tri-state `PendingSave` fact: it noops on `False`, saves on `True`, and fails
closed on `None` because the active-to-boot comparison could not run.

Save is device-global. It writes the complete active configuration to
`/config/config.boot`, including unrelated unsaved changes. Typed ownership
does not scope persistence.

`src` should be a footer-bearing config (`// vyos-config-version`). Use a
`/config/config.boot`-style source (or `save <file>` output). Bare
`show configuration` output has no footer; VyOS `load` treats that as
version 0 and runs the full migration chain.

`save` is keyword-only. `config_load(src, True)` is not a valid call.

### Typed field management

Typed operations own only the fields supplied by the caller. This deploy owns
the hostname on `system_basics`, then the addresses and description on `dum0`.
Omitted DNS, timezone, MTU, and disabled-state arguments remain unmanaged.

```python
# deploy_typed.py
from pyinfra_vyos import interface, system_basics

system_basics(
    hostname="edge-01",
    save=False,
)

interface(
    "dum0",
    interface_type="dummy",
    addresses=["192.0.2.1/32"],
    description="Managed dummy interface",
    save=False,
)
```

```sh
pyinfra inventory.py deploy_typed.py
```

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

## Operations

| Operation | Purpose | Ownership model |
|---|---|---|
| `config_load` | Load a controller-local whole configuration. | Whole configuration supplied by `src`. |
| `config` | Manage an arbitrary configuration path. | Merge by default; `replace=True` makes the owned subtree total. |
| `config_save` | Persist the active configuration. | No configuration ownership; persistence is device-global. |
| `system_basics` | Manage hostname, domain, DNS, and timezone fields. | Per-field; omitted arguments are unmanaged. |
| `interface` | Manage one ethernet, loopback, or dummy interface. | Per-field, with an open `values` body; omitted arguments are unmanaged. |
| `static_route` | Manage one IPv4 or IPv6 static route. | Whole-object total body. |
| `user` | Manage one login user. | Per-field; `ssh_keys` is an exact set when supplied. |
| `firewall_group` | Manage one static firewall group. | Whole-object total body. |
| `firewall_ruleset` | Manage one IPv4 or IPv6 firewall chain. | Per-field and per-rule; each declared rule body is total, and `replace_rules=True` owns the complete rule set. |

NAT users should use `config` for the numbered-key whole-object replace;
`nat_rule`, scoped facts, typed convenience facts, commit-confirm, composite
interfaces, and dynamic or remote firewall groups are deferred.

## Contracts

These are user-facing, not internals:

1. **Serialize mutations per host.** The caller must run at most one mutation
   session at a time against a device, including sessions from concurrent
   controllers. This applies to whole-config, generic, typed, and persist
   operations. VyOS has no documented session lock; this package does not
   invent one.
2. **Treat controller logs as sensitive.** `Configuration` and unredacted
   `ConfigurationCommands` can reach returned fact values, verbose fact
   output, failed-fact combined output, and operation failure diagnostics.
   A failing `encrypted_password` command logs its ordinal and verb plus a
   fixed suppression notice instead of device output. Commit failure output
   remains forwarded and is residual secret exposure.
3. **Supply password hashes, never plaintext.** `encrypted_password` accepts
   a pre-hashed crypt string or the `!` / `*` lock markers. Hash passwords on
   the controller with `mkpasswd` or passlib. Rejected password values are not
   echoed in errors.
4. **Use typed operations only on recognized versions.** The six typed
   operations read `Version` and fail closed when the version is missing,
   unqualified, or unrecognized. `config` and `config_load` are the
   version-agnostic escape hatches.
5. **Choose the ownership model deliberately.** `system_basics`, `interface`,
   and `user` use per-field ownership. `firewall_ruleset` owns its leaf fields
   independently and owns each declared rule body totally. Unlisted rules stay
   unmanaged unless `replace_rules=True`, which owns the complete rule set.
   `static_route` and `firewall_group` own whole-object total bodies. A total
   body prunes undeclared state at every depth. Where accepted, an empty
   collection owns its field or body and makes it empty; it does not mean
   unmanaged.
6. **Use `values` for open device grammar.** `config`, `interface`,
   `static_route`, and `firewall_ruleset` pass `values` through for device
   validation. The typed operations reject `values` keys that collide with a
   typed field.
7. **Assume out-of-band recovery for lockout changes.** The lockout classes
   are `interface` address changes and `disabled=True`, `static_route`
   changes, base-chain `firewall_ruleset` `default_action` changes,
   `firewall_ruleset(..., replace_rules=True)`, and `user` deletion. These
   changes take effect at commit; `save=False` does not delay them. Console or
   other out-of-band recovery is assumed if the controller session is severed.
8. **Supply a footer-bearing config to `config_load`.** Callers should ship a
   file that includes `// vyos-config-version` — the same footer
   `/config/config.boot` carries. The library does not detect or inject it.
9. **Read change reporting by operation.** `config_load` alone is marked
   `is_idempotent=False`: pyinfra's change flag is pessimistic because its
   executed command list is nonempty. Device stdout sentinels
   (`PYINFRA_VYOS changed` or `PYINFRA_VYOS noop`) report the device result. A
   save-only `config_load` run (no candidate diff, boot file still written)
   reports `changed` via the sentinel. `config` and typed operations diff
   against `Configuration` on the controller and call `host.noop` for an
   empty delta. When they send a delta, the device's `sessionChanged` gate
   remains authoritative; supply device-canonical values if canonicalization
   otherwise causes the controller to resend a truthful device noop.

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

**Appliance** is opt-in and not wired into CI. It covers every operation
against a live VyOS host (load / commit / save, then restore from
`/config/config.boot`). Run it only against a **dedicated lab device**, never
production. Both `--appliance` and `PYINFRA_VYOS_TEST_HOST` are required:

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
