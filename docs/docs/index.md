---
title: pyinfra-vyos
slug: /
description: pyinfra facts and typed configuration operations for VyOS over SSH.
---

# pyinfra-vyos

`pyinfra-vyos` is a [pyinfra](https://pyinfra.com) plugin for managing VyOS
over SSH. It exports four facts and nine operations: whole-config loading,
generic scoped configuration, explicit persistence, and typed operations for
system settings, interfaces, static routes, users, firewall groups, and
firewall rulesets.

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

The four facts wrap op-mode commands. `config_load` uploads a
controller-local file and runs one configure / load / commit session.

```python
# facts.py
from pyinfra import host

from pyinfra_vyos import Configuration, ConfigurationCommands, PendingSave, Version

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

A successful commit changes the active configuration immediately and can
sever SSH. `save=False` limits reboot persistence only; it is not a dry run.
Apply a risky change without saving, verify reachability and facts, and then
persist the active configuration with `config_save()`.

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
# deploy_persist.py
from pyinfra_vyos import config_save

config_save()
```

```sh
pyinfra inventory.py deploy_commit.py
# verify SSH reachability and re-gather facts
pyinfra inventory.py facts.py
pyinfra inventory.py deploy_persist.py
```

`config_save()` writes the complete active configuration, including unrelated
unsaved changes. Serialize mutation sessions per host and account for every
active change before running the persist phase. `save` is keyword-only on
operations that accept it; for example, `config_load(src, True)` is invalid.

### Scoped subtree management

`config` owns one config path and manages the subtree beneath it. `values`
mirrors `show configuration json` shapes: a nested dictionary for a subtree,
`{}` for a valueless node, a string for a single-value leaf, and a list for a
multi-value leaf.

```python
# deploy_ntp.py
from pyinfra_vyos import config

config(
    name="Manage NTP servers",
    path=["service", "ntp"],
    values={"server": {"time1.example.net": {}, "time2.example.net": {}}},
    replace=True,
)
```

By default, omitted state is unmanaged: only missing or differing desired
values are set. With `replace=True`, the subtree becomes exactly `values`.
Extra active keys and leaf values are deleted, so choose the owned `path`
carefully. `present=False` deletes the whole path.

The controller diffs the desired subtree against the active `Configuration`
fact. An empty delta calls `host.noop` without touching the device. Applied
deltas are staged in one configure session and committed once behind the
device's `sessionChanged` gate. Every path token, key, and value must be a
nonempty string that does not begin with `-`. Multi-value ordering is not
managed.

## Contracts

These contracts apply to the public operations and facts:

1. **Serialize mutations per host.** The caller must run at most one mutation
   session at a time against a device, including concurrent runs from the same
   controller. VyOS has no documented session lock, and this package does not
   add one.
2. **Treat controller logs as sensitive.** `Configuration` and unredacted
   `ConfigurationCommands` can expose secrets through fact values, verbose
   output, failed-fact output, and operation diagnostics. A failing sensitive
   password command logs its ordinal and verb with a fixed suppression notice
   instead of device output. Commit failure output remains forwarded and is a
   residual exposure.
3. **Supply a footer-bearing config to `config_load`.** Callers should provide
   a file with `// vyos-config-version`, as found in `/config/config.boot` or
   `save <file>` output. The library does not detect or inject the footer.
4. **Distinguish controller and device idempotency.** `config_load` is
   `is_idempotent=False`, so pyinfra reports it changed whenever it runs. The
   other configuration operations diff against `Configuration` and call
   `host.noop` for an empty delta. When a delta is sent, the device's
   `sessionChanged` sentinel remains authoritative.
5. **Treat save as device-global persistence.** A command script saves only
   when that script committed. An empty controller delta cannot be used to
   persist earlier active changes. Use `config_save()` as the explicit persist
   phase after verification.
6. **Use the generic operation for deferred surfaces.** `nat_rule` is deferred;
   use `config` with replacement at the numbered rule path for its numbered-key
   whole-object model. Scoped facts, typed convenience facts, commit-confirm,
   composite interfaces, and dynamic or remote firewall groups are also
   deferred.

## Fact reference

All four facts run through `vbash` and script-template `run`.
`requires_command` returns `"vbash"` as a binary-presence gate only. Hosts
without `vbash` yield `default()` instead of failing. The gate does not
establish that the host is a VyOS appliance or that op-mode commands are
compatible.

| Fact | Type | Op-mode or device probe | Arguments | `default()` |
| --- | --- | --- | --- | --- |
| `Version` | `FactBase[dict]` | `show version` | none | `{}` |
| `Configuration` | `FactBase[dict]` | `show configuration json` | none | `{}` |
| `ConfigurationCommands` | `FactBase[list]` | `show configuration commands` | `strip_private: bool = False` | `[]` |
| `PendingSave` | `FactBase[bool \| None]` | active configuration compared with `/config/config.boot` | none | `None` |

- **`Version`** — label-to-value mapping from `show version`. Labels are
  lowercased with spaces turned into underscores. The `version` field is
  required; unknown labels and missing optional labels are kept or omitted as
  the parser produces them.
- **`Configuration`** — the active configuration as the raw JSON tree. No key
  or value normalization. Secret-bearing.
- **`ConfigurationCommands`** — device-rendered set-form lines, with nonempty
  lines kept as-is. When `strip_private` is true, the VyOS op-pipe tokens `\|`
  and `strip-private` are appended as ordinary argv. This is a VyOS op pipe,
  not a shell pipeline. Unredacted output is secret-bearing. Redacted output is
  not restore-faithful and must not be used as a backup.
- **`PendingSave`** — `True` means the active configuration differs from the
  boot configuration, `False` means the comparison ran and found no
  difference, and `None` means the comparison could not run or could not be
  parsed. `config_save()` fails closed on `None`. The device pipeline reduces
  the comparison to a byte count before stdout, so configuration text does not
  reach fact logs. Its `requires_command` value is the same `vbash` presence
  gate as the other facts.

```python
from pyinfra_vyos.facts import (
    Configuration,
    ConfigurationCommands,
    PendingSave,
    Version,
)
```

The same names are re-exported from `pyinfra_vyos`.

## Operation reference

The signatures below are the public operation arguments. pyinfra global
arguments, such as the operation label `name`, remain available through
pyinfra.

### Ownership models

Per-field operations own only the fields whose arguments are not `None`.
Omitted per-field arguments remain unmanaged. An empty collection is not an
omission: it owns the field and makes that field empty. `system_basics`,
`interface`, and `user` use this model. `firewall_ruleset` uses it for chain
leaves and for the set of rule numbers when `replace_rules=False`.

Whole-object operations treat a declared body as total. The controller prunes
undeclared active state at every depth. For a `static_route`, this includes
undeclared next-hops and undeclared attributes under a declared next-hop. For a
declared firewall rule, this includes undeclared leaves inside that rule.
`static_route`, `firewall_group`, and each declared `firewall_ruleset` rule use
total bodies. `replace_rules=True` extends total ownership to the chain's
complete rule collection.

`config` in its default merge mode is the shared-ownership alternative: it sets
declared state and leaves omitted state unmanaged. The `values` arguments on
`interface`, `static_route`, and `firewall_ruleset` are the corresponding
open-body escape hatches. Their leaves pass through to device commit for
validation. A `values` top-level key that collides with a typed key is rejected
instead of creating overlapping ownership.

### Version gate

The seven typed configuration operations resolve a `1.4` or `1.5` schema key
from the `Version` fact. An unrecognized, unqualified, or missing version fails
closed before configuration planning. `config` and `config_load` are
version-agnostic escape hatches; `config_save` consumes `PendingSave` and does
not render versioned configuration grammar.

Library-emitted grammar is fixture-asserted for both schema keys. Appliance
verification covers only the VyOS 2026.03 lab release. Pass-through grammar in
`values` and open firewall chain tokens remains device-validated.

### Save and persist

`save=False` does not defer a commit. A successful operation changes the active
configuration immediately; the flag controls only whether that run also writes
`/config/config.boot`. Under the D13 gate, a configuration command script saves
only when its own command set committed. `config_load` retains its load-specific
save behavior.

A second identical typed operation noops at the controller and therefore cannot
persist an earlier active change. Use this workflow instead:

1. Run `op(..., save=False)`.
2. Verify SSH reachability and gather the relevant facts.
3. Run `config_save()`.

`config_save()` reads the tri-state `PendingSave` fact. It noops on `False`,
fails closed on `None`, and performs an idempotent device-side recheck and save
on `True`. Saving is device-global: it writes the whole active configuration,
including unrelated unsaved changes.

### `config`

```python
config(path: list[str] | tuple[str, ...], values: dict | None = None, *, replace: bool = False, present: bool = True, save: bool = False)
```

Owns the caller-supplied `path`. With `replace=False`, `values` is merged and
omitted state is unmanaged. With `replace=True`, the body is total and extra
active state is pruned recursively. `values=None` and `values={}` both ensure
the bare path node exists. `present=False` deletes the path and requires
`values=None` and `replace=False`.

### `config_save`

```python
config_save()
```

Owns no configuration path. It persists the complete active configuration as
described in [Save and persist](#save-and-persist). It accepts no operation
arguments beyond pyinfra's global arguments.

### `system_basics`

```python
system_basics(*, hostname=None, domain_name=None, name_servers=None, search_domains=None, time_zone=None, save=False)
```

Owns the independently declared leaves `system host-name`, `system
domain-name`, `system name-server`, `system domain-search`, and `system
time-zone`. `None` leaves a field unmanaged. `name_servers=[]` and
`search_domains=[]` own and empty their leaves. An all-`None` call is rejected.
Scalar removal is outside this operation; use `config(..., present=False)` at
the scalar path.

### `interface`

```python
interface(interface: str, *, interface_type: str, addresses=None, description=None, mtu=None, disabled=None, values=None, present=True, save=False)
```

Owns fields under `interfaces <interface_type> <interface>`. `addresses`,
`description`, `mtu`, and `disabled` are per-field: `None` leaves them
unmanaged, `addresses=[]` owns and empties the address leaf, `disabled=True`
creates `disable`, and `disabled=False` removes it. `values` merges other keys
at the interface path; `address`, `description`, `mtu`, and `disable` collide
with typed fields and are rejected. If all typed fields and `values` are
omitted, the operation still ensures the bare interface exists.

`present=False` deletes the whole interface and requires every desired
argument to be unset. `interface_type` must be `ethernet`, `loopback`, or
`dummy`; it is explicit by design rather than inferred from the interface name.

### `static_route`

```python
static_route(destination: str, *, next_hops: list[str] | dict[str, Any] | None = None, values=None, present=True, save=False)
```

Owns the total route body at `protocols static route <destination>` for IPv4 or
`protocols static route6 <destination>` for IPv6. `destination` must include an
explicit prefix length. Bare hosts and prefixes with host bits set are
rejected.

`next_hops` may be a list of addresses or a mapping from address to a per-hop
subtree. `values` supplies other route-body keys. A `next-hop` key in `values`
collides with typed `next_hops` when both are provided. `present=True` requires
a nonempty body; `None` or empty collections contribute no body and are
rejected when no other body key is present. `present=False` deletes the route
and requires `next_hops` and `values` to be unset.

### `user`

```python
user(user: str, *, full_name=None, encrypted_password=None, ssh_keys=None, present=True, save=False)
```

Owns per-field state under `system login user <user>`. `None` leaves
`full_name`, `encrypted_password`, or `ssh_keys` unmanaged. `ssh_keys={}` owns
and empties the public-key set. If every field is `None`, the operation ensures
the bare user exists. `present=False` deletes the user, requires every desired
argument to be unset, and refuses deletion when the connected identity is the
same user or cannot be established.

`encrypted_password` is hash-only. It accepts a pre-hashed crypt string
starting with `$`, or the `!` and `*` lock markers. Callers must hash passwords
on the controller with a tool such as `mkpasswd` or passlib; plaintext is
rejected and the rejected value is never echoed.

### `firewall_group`

```python
firewall_group(group: str, group_type: str, *, members=None, description=None, present=True, save=False)
```

Owns the total group body at `firewall group <group_type>-group <group>`.
Supported group types are `address`, `ipv6-address`, `network`,
`ipv6-network`, `port`, `interface`, `mac`, and `domain`. When `present=True`,
`members` is required; `members=[]` owns and empties the group. Because the
body is total, `description=None` means desired-absent and prunes an active
description. `present=False` deletes the group and requires `members` and
`description` to be `None`.

### `firewall_ruleset`

```python
firewall_ruleset(af: str, chain: list[str], *, default_action=None, description=None, rules=None, replace_rules=False, values=None, present=True, save=False)
```

Owns selected state under `firewall <af> <*chain>`, where `af` is `ipv4` or
`ipv6` and `chain` is an open token list. `default_action` and `description`
are per-field; `None` leaves them unmanaged. Each body in `rules` is total, so
undeclared leaves in a declared rule are pruned. With `replace_rules=False`,
unlisted rule numbers remain unmanaged and `{n: None}` deletes only rule `n`.
Rule numbers are coerced to strings; duplicate numbers across integer and
string keys, such as `10` and `"10"`, are rejected.

`replace_rules=True` requires `rules` and makes the complete `rule` node total.
Only under that flag does `rules={}` prune every rule; `None` rule entries are
rejected. `values` merges other chain keys. `default-action`, `description`,
and `rule` collide with typed arguments and are rejected in `values`. An
owns-nothing call is rejected. `present=False` deletes the chain and requires
all desired arguments to be unset and `replace_rules=False`.

## Operator risks

- **Immediate lockout.** The lockout classes are management-address changes
  and `disabled=True` in `interface`; any `static_route` change; user deletion;
  a base-chain `default_action` change in `firewall_ruleset`; and
  `replace_rules=True`. `config_load` and broad replacement through `config`
  can remove the same access paths. All of these operations assume console or
  other out-of-band recovery if the commit severs the controller session. This
  package does not implement commit-confirm.
- **Device canonicalization.** The controller compares submitted text with the
  `Configuration` fact. If the device stores a different form, the controller
  re-emits the delta on each run while the device `sessionChanged` gate
  truthfully reports `noop`. Use the device-stored form to restore
  controller-side noops.
- **VyOS 2026.03 lab observations.** These forms were observed on the lab
  appliance and are not cross-version guarantees:
  - `interface` echoed address `192.0.2.65/32`, the submitted description, and
    MTU `1400` verbatim.
  - `static_route` stored route tag-node keys verbatim. An expanded,
    uppercase IPv6 destination round-tripped unchanged. Two textual forms of
    one prefix therefore create two distinct route nodes; standardize on
    compressed lowercase IPv6.
  - `user` echoed the SSH public-key body verbatim, and an
    `encrypted_password` hash round-tripped as stable text.
  - `firewall_group` echoed port members `8080` and `8000-9000` verbatim.
  - `firewall_ruleset` echoed integer rule numbers as strings. The renderer
    performs the same coercion, so comparison remains idempotent. Action,
    protocol, and port values were verbatim.
  - `system_basics` preserved `domain-search` set order and the `time-zone`
    string. The device accepted `domain_name` and `search_domains` together.
- **Rejected commits.** A rejected multi-command commit was observed to leave
  no partial active state on the VyOS 2026.03 lab appliance. This is one data
  point, not a rollback or cross-version atomicity guarantee. The session EXIT
  trap removes candidate state; it cannot roll back active state after a
  partial commit.
- **Stranded staging residual.** Each mutation run uses a high-entropy
  directory `/tmp/pyinfra-vyos-<token>/` with mode 0700 and files with mode
  0600. If a yielded command fails before the session runs, or if the SSH
  connector is lost, that directory remains. Paths that reach session
  execution are cleaned by the EXIT trap and the trailing removal command.
  `/tmp` may be tmpfs; a large config can exhaust it.
- **`config_load` change reporting.** pyinfra always reports `config_load` as
  changed because its executed command list is nonempty. Read
  `PYINFRA_VYOS changed` or `PYINFRA_VYOS noop` in operation stdout for the
  device result. A save-only load run reports `changed` through the sentinel.

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

## Source

- [Source on GitHub](https://github.com/meigma/pyinfra-vyos)
- [pyinfra documentation](https://docs.pyinfra.com) — the API these
  primitives extend