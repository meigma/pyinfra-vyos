from __future__ import annotations

import contextlib
import ipaddress
import os
import re
import secrets
from pathlib import Path
from typing import Any

import pytest
from pyinfra.api import Inventory, StringCommand
from pyinfra.api.exceptions import OperationValueError, PyinfraError
from pyinfra.api.operation import OperationMeta

from pyinfra_vyos import (
    Configuration,
    ConfigurationCommands,
    PendingSave,
    Version,
    config,
    config_load,
    config_save,
    interface,
    static_route,
    system_basics,
    user,
)
from pyinfra_vyos._cli import vyos_op_command
from pyinfra_vyos._parse import OUTPUT_MARKER, parse_config_json, strip_marker
from pyinfra_vyos._session import SENTINEL_CHANGED, SENTINEL_NOOP

from ._helpers import appliance_inventory, apply, fact_value, new_state

pytestmark = pytest.mark.appliance

_BOOT_PATH = "/config/config.boot"
_VERSION_FOOTER = "// vyos-config-version"
_TEST_INET = "192.0.2.1"
_SEARCH_DOMAINS = ("pyinfra-a.test", "pyinfra-b.test")
_Q3_DOMAIN_NAME = "pyinfra-q3.test"
_DUMMY_IFACE = "dum0"
_DUMMY_TYPE = "dummy"
_DUMMY_ADDRESS = "192.0.2.65/32"
_DUMMY_ADDRESSES_REPLACED = ("192.0.2.66/32", "192.0.2.67/32")
_DUMMY_DESCRIPTION = "pyinfra phase3"
_DUMMY_MTU = 1400
_ROUTE_V4 = "192.0.2.0/24"
_ROUTE_V4_HOPS = ("203.0.113.1", "203.0.113.2")
_ROUTE_V4_DISTANCE = "50"
_ROUTE_V6 = "2001:0DB8:0000:0001:0000:0000:0000:0000/64"
_ROUTE_V6_HOP = "2001:0DB8:0000:0000:0000:0000:0000:0001"
_LOOPBACK_IFACE = "lo"
_TEST_USER = "pyinfra-test"
_CONNECTING_USER = "vyos"
_TEST_FULL_NAME = "pyinfra phase5"
_TEST_SSH_KEY_ID = "phase5@lab"
_TEST_SSH_KEY_TYPE = "ssh-ed25519"
# OpenSSH wire-format ed25519 blob (type prefix + 32-byte key).
_TEST_SSH_KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIE1H2cTUD/FoeMur8M6Roz/VI/+KE1p7d3SmKBv53/Wo"
_TEST_SSH_KEYS = {_TEST_SSH_KEY_ID: {"type": _TEST_SSH_KEY_TYPE, "key": _TEST_SSH_KEY}}
_TEST_PASSWORD_HASH = (
    "$6$pyinfra5$"
    "xh1D6pXeohRbJKsbqbS/maiR.WAB7eXU8t8BjL4YG50QMQplvQs0l5mx9XY3nHvTINjoHeLNHgsr0QlngUmMc/"
)
_TEST_PASSWORD_HASH_ROTATED = (
    "$6$pyinfra5b$"
    "oo3qYKoWAGgJqR5W.tz1zvbUUQoZwt2paoIWQxy33TwnNeB/HGSRd1a4FMUd5rA3yMygkppWi95x2H/8w.Jki1"
)
_SENSITIVE_SUPPRESSION = "device output suppressed (sensitive command)"
_SYSTEM_OPEN_LINE = re.compile(r"(?m)^system\s*\{\n")
_DEFAULT_CAPTURE_DIR = Path(__file__).resolve().parent / "_captures"


@pytest.fixture
def inventory() -> Inventory:
    return appliance_inventory()


def _op_mode_text(inventory: Inventory, *argv: str) -> str:
    state = new_state(inventory)
    host = next(iter(state.inventory))
    status, output = host.run_shell_command(vyos_op_command(*argv, marker=OUTPUT_MARKER))
    assert status, output.stderr
    return "\n".join(strip_marker(list(output.stdout_lines))) + "\n"


def _read_boot_config(inventory: Inventory) -> str:
    """Return ``/config/config.boot``, which carries the ``vyos-config-version`` footer.

    ``show configuration`` renders the tree only; ``load`` treats a footer-less
    file as version 0 and runs the full migration chain.
    """

    state = new_state(inventory)
    host = next(iter(state.inventory))
    status, output = host.run_shell_command(StringCommand("cat", _BOOT_PATH))
    assert status, output.stderr
    text = "\n".join(output.stdout_lines) + "\n"
    assert _VERSION_FOOTER in text, (
        f"{_BOOT_PATH} has no {_VERSION_FOOTER!r} footer; load would run full migrations"
    )
    return text


def _with_static_host_mapping(config: str, hostname: str) -> str:
    """Insert a complete ``static-host-mapping`` block after the top-level ``system {``.

    The hostname is library-generated (hex + ``.invalid``) so the inserted
    block has no quotes or braces that would need a quote-aware splice.
    """

    match = _SYSTEM_OPEN_LINE.search(config)
    if match is None:
        raise AssertionError("captured config has no top-level 'system {' line")
    block = (
        "    static-host-mapping {\n"
        f"        host-name {hostname} {{\n"
        f"            inet {_TEST_INET}\n"
        "        }\n"
        "    }\n"
    )
    return config[: match.end()] + block + config[match.end() :]


def _instance_child(node: Any, key: str) -> Any:
    """Return the child at ``key`` from a dict or list-of-single-key-dicts node.

    Multi-instance JSON nodes render either as a dict keyed by instance name or
    as a list of single-key dicts (see :func:`_assert_host_mapping`).
    """

    if isinstance(node, dict):
        return node.get(key)
    if isinstance(node, list):
        return next(
            (item[key] for item in node if isinstance(item, dict) and key in item),
            None,
        )
    return None


def _assert_host_mapping(tree: dict[str, Any], hostname: str) -> None:
    mapping = tree["system"]["static-host-mapping"]["host-name"]
    if isinstance(mapping, dict):
        entry = mapping[hostname]
    elif isinstance(mapping, list):
        entry = next(item[hostname] for item in mapping if hostname in item)
    else:
        raise AssertionError(f"unexpected static-host-mapping host-name node: {mapping!r}")
    inet = entry["inet"]
    values = inet if isinstance(inet, list) else [inet]
    assert _TEST_INET in values


def _assert_sentinel(meta: OperationMeta, sentinel: str) -> None:
    assert sentinel in meta.stdout, (
        f"expected {sentinel!r} in operation stdout, got {meta.stdout!r}"
    )


def _capture_dir() -> Path:
    override = os.environ.get("PYINFRA_VYOS_TEST_CAPTURE_DIR", "").strip()
    path = Path(override).expanduser() if override else _DEFAULT_CAPTURE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _leaf_values(node: Any) -> list[str] | None:
    """Return a config leaf as ``list[str]``, or ``None`` when the node is absent."""

    if node is None:
        return None
    if isinstance(node, list):
        return [str(item) for item in node]
    if isinstance(node, str):
        return [node]
    raise AssertionError(f"unexpected leaf node shape: {node!r}")


def _leaf_scalar(node: Any) -> str | None:
    """Return a single-value leaf, or ``None`` when the node is absent."""

    values = _leaf_values(node)
    if values is None:
        return None
    if len(values) != 1:
        raise AssertionError(f"expected a single leaf value, got {values!r}")
    return values[0]


def _op_mode_tree(inventory: Inventory) -> dict[str, Any]:
    """Independent T2 read: parse ``show configuration json``."""

    return parse_config_json(_op_mode_text(inventory, "show", "configuration", "json"))


def _op_mode_system(inventory: Inventory) -> dict[str, Any]:
    """Independent T2 read: parse ``show configuration json`` and return ``system``."""

    system = _op_mode_tree(inventory).get("system")
    assert isinstance(system, dict), f"unexpected system node: {system!r}"
    return system


def _op_mode_dummy(inventory: Inventory, name: str = _DUMMY_IFACE) -> dict[str, Any] | None:
    """Independent T2 read of ``interfaces dummy <name>``, or ``None`` if absent."""

    interfaces = _op_mode_tree(inventory).get("interfaces")
    if not isinstance(interfaces, dict):
        return None
    dummy = _instance_child(interfaces, "dummy")
    if dummy is None:
        return None
    node = _instance_child(dummy, name)
    if node is None:
        return None
    if isinstance(node, dict):
        return node
    raise AssertionError(f"unexpected dummy {name} node: {node!r}")


def _instance_items(node: Any) -> list[tuple[str, Any]]:
    """Return ``(key, value)`` pairs from a dict or list-of-single-key-dicts node."""

    if node is None:
        return []
    if isinstance(node, dict):
        return list(node.items())
    if isinstance(node, list):
        items: list[tuple[str, Any]] = []
        for item in node:
            if isinstance(item, dict):
                items.extend(item.items())
        return items
    raise AssertionError(f"unexpected multi-instance node: {node!r}")


def _route_family(destination: str) -> str:
    network = ipaddress.ip_network(destination, strict=False)
    return "route6" if network.version == 6 else "route"


def _op_mode_route(inventory: Inventory, destination: str) -> tuple[str, dict[str, Any]] | None:
    """Independent T2 read of one ``protocols static route[6]`` entry.

    Matches the destination by prefix equality so IPv6 compression on the
    device key still resolves. Returns ``(device_key, body)`` or ``None``.
    """

    network = ipaddress.ip_network(destination, strict=False)
    family = "route6" if network.version == 6 else "route"
    protocols = _op_mode_tree(inventory).get("protocols")
    if not isinstance(protocols, dict):
        return None
    static = _instance_child(protocols, "static")
    if static is None:
        return None
    routes = _instance_child(static, family)
    for key, body in _instance_items(routes):
        try:
            echoed = ipaddress.ip_network(key, strict=False)
        except ValueError:
            continue
        if echoed != network:
            continue
        if not isinstance(body, dict):
            raise AssertionError(f"unexpected {family} {key} node: {body!r}")
        return key, body
    return None


def _route_next_hops(node: dict[str, Any]) -> dict[str, Any]:
    """Return the ``next-hop`` map under a static-route body."""

    hops = _instance_child(node, "next-hop")
    if hops is None:
        return {}
    return dict(_instance_items(hops))


def _next_hop_addresses(node: dict[str, Any]) -> set[str]:
    return set(_route_next_hops(node))


def _hop_subtree(hops: dict[str, Any], address: str) -> dict[str, Any]:
    """Return the body of one next-hop, matching the address by IP equality."""

    want = ipaddress.ip_address(address)
    for key, body in hops.items():
        try:
            matched = ipaddress.ip_address(key) == want
        except ValueError:
            continue
        if matched:
            return body if isinstance(body, dict) else {}
    raise AssertionError(f"next-hop {address!r} missing from {set(hops)!r}")


def _delete_static_route(inventory: Inventory, destination: str) -> None:
    """Best-effort ``present=False``; absence and commit errors are tolerated."""

    with contextlib.suppress(PyinfraError):
        apply(
            static_route,
            inventory=inventory,
            destination=destination,
            present=False,
            save=False,
        )
    found = _op_mode_route(inventory, destination)
    if found is None:
        return
    key, _ = found
    family = _route_family(destination)
    with contextlib.suppress(PyinfraError):
        apply(
            config,
            inventory=inventory,
            path=["protocols", "static", family, key],
            present=False,
            save=False,
        )


def _op_mode_login_user(inventory: Inventory, name: str) -> dict[str, Any] | None:
    """Independent T2 read of ``system login user <name>``, or ``None`` if absent."""

    system = _op_mode_system(inventory)
    login = system.get("login")
    if not isinstance(login, dict):
        return None
    node = _instance_child(login.get("user"), name)
    if node is None:
        return None
    if isinstance(node, dict):
        return node
    raise AssertionError(f"unexpected login user {name} node: {node!r}")


def _delete_login_user(inventory: Inventory, name: str) -> None:
    """Best-effort ``present=False``; absence and commit errors are tolerated.

    Never called on the connecting user: the typed op's deletion guard would
    refuse, and a ``config`` fallback would bypass that guard.
    """

    if name == _CONNECTING_USER:
        raise AssertionError(f"cleanup must not touch connecting user {name!r}")
    with contextlib.suppress(PyinfraError):
        apply(
            user,
            inventory=inventory,
            user=name,
            present=False,
            save=False,
        )
    if _op_mode_login_user(inventory, name) is None:
        return
    with contextlib.suppress(PyinfraError):
        apply(
            config,
            inventory=inventory,
            path=["system", "login", "user", name],
            present=False,
            save=False,
        )


def test_version_returns_a_version_key(inventory: Inventory) -> None:
    raw = _op_mode_text(inventory, "show", "version")
    artifact = _capture_dir() / "show-version.txt"
    artifact.write_text(raw)
    print(f"pyinfra-vyos show version fixture: {artifact}")

    version = fact_value(Version, inventory=inventory)
    assert "version" in version
    assert version["version"]


def test_configuration_returns_a_dict_with_system(inventory: Inventory) -> None:
    tree = fact_value(Configuration, inventory=inventory)
    assert isinstance(tree, dict)
    assert "system" in tree
    assert isinstance(tree["system"], dict)


def test_configuration_commands_are_nonempty_and_strip_private_runs(
    inventory: Inventory,
) -> None:
    commands = fact_value(ConfigurationCommands, inventory=inventory)
    assert isinstance(commands, list)
    assert commands

    redacted = fact_value(ConfigurationCommands, inventory=inventory, strip_private=True)
    assert isinstance(redacted, list)
    assert redacted


def test_config_load_commit_noop_and_save_cycle(
    inventory: Inventory,
    tmp_path: Path,
) -> None:
    original = _read_boot_config(inventory)
    hostname = f"pyinfra-vyos-{secrets.token_hex(8)}.invalid"
    mutated = _with_static_host_mapping(original, hostname)
    assert mutated != original
    assert hostname in mutated
    assert hostname not in original

    original_path = tmp_path / "original.conf"
    mutated_path = tmp_path / "mutated.conf"
    original_path.write_text(original)
    mutated_path.write_text(mutated)

    try:
        first = apply(config_load, inventory=inventory, src=str(mutated_path), save=False)
        _assert_sentinel(first, SENTINEL_CHANGED)
        _assert_host_mapping(fact_value(Configuration, inventory=inventory), hostname)

        second = apply(config_load, inventory=inventory, src=str(mutated_path), save=False)
        _assert_sentinel(second, SENTINEL_NOOP)
        assert SENTINEL_CHANGED not in second.stdout

        saved = apply(config_load, inventory=inventory, src=str(mutated_path), save=True)
        _assert_sentinel(saved, SENTINEL_CHANGED)
        persisted = apply(config_load, inventory=inventory, src=str(mutated_path), save=True)
        _assert_sentinel(persisted, SENTINEL_NOOP)
        _assert_host_mapping(fact_value(Configuration, inventory=inventory), hostname)
    finally:
        try:
            apply(config_load, inventory=inventory, src=str(original_path), save=True)
        finally:
            original_path.unlink(missing_ok=True)
            mutated_path.unlink(missing_ok=True)


def test_config_scoped_merge_replace_delete_cycle(inventory: Inventory) -> None:
    """Exercise the scoped op end to end: merge-create, controller noop,
    replace-swap, and present=False delete, verified through an independent
    op-mode read (T2) with a second-apply noop at each mutation (T3).

    save=False throughout: commits touch only the active config, so the boot
    config never changes and a crashed run cannot survive a reboot. The save
    epilogue is shared, load-cycle-verified code.
    """

    hostname = f"pyinfra-config-{secrets.token_hex(8)}.invalid"
    path = ["system", "static-host-mapping", "host-name", hostname]

    def active_mapping() -> dict[str, Any] | None:
        tree = fact_value(Configuration, inventory=inventory)
        return (
            tree.get("system", {}).get("static-host-mapping", {}).get("host-name", {}).get(hostname)
        )

    try:
        first = apply(config, inventory=inventory, path=path, values={"inet": "192.0.2.53"})
        assert first.did_change()
        _assert_sentinel(first, SENTINEL_CHANGED)
        assert active_mapping() == {"inet": ["192.0.2.53"]}

        second = apply(config, inventory=inventory, path=path, values={"inet": "192.0.2.53"})
        assert not second.did_change()

        merged = apply(config, inventory=inventory, path=path, values={"inet": "192.0.2.54"})
        assert merged.did_change()
        _assert_sentinel(merged, SENTINEL_CHANGED)
        assert active_mapping() == {"inet": ["192.0.2.53", "192.0.2.54"]}

        replaced = apply(
            config, inventory=inventory, path=path, values={"inet": "192.0.2.54"}, replace=True
        )
        assert replaced.did_change()
        _assert_sentinel(replaced, SENTINEL_CHANGED)
        assert active_mapping() == {"inet": ["192.0.2.54"]}

        replaced_again = apply(
            config, inventory=inventory, path=path, values={"inet": "192.0.2.54"}, replace=True
        )
        assert not replaced_again.did_change()

        deleted = apply(config, inventory=inventory, path=path, present=False)
        assert deleted.did_change()
        _assert_sentinel(deleted, SENTINEL_CHANGED)
        assert active_mapping() is None

        deleted_again = apply(config, inventory=inventory, path=path, present=False)
        assert not deleted_again.did_change()
    finally:
        apply(config, inventory=inventory, path=path, present=False)


def test_config_save_dirty_then_noop_cycle(inventory: Inventory) -> None:
    """Dirty the active config, persist with config_save, then noop on a second save.

    save=False on the scratch mutation so only config_save writes boot. Cleanup
    deletes the scratch path and saves so a crashed run cannot survive reboot.
    """

    hostname = f"pyinfra-save-{secrets.token_hex(8)}.invalid"
    path = ["system", "static-host-mapping", "host-name", hostname]

    try:
        apply(
            config,
            inventory=inventory,
            path=path,
            values={"inet": "192.0.2.60"},
            save=False,
        )
        assert fact_value(PendingSave, inventory=inventory) is True
        assert hostname not in _read_boot_config(inventory)

        saved = apply(config_save, inventory=inventory)
        assert saved.did_change()
        _assert_sentinel(saved, SENTINEL_CHANGED)
        assert hostname in _read_boot_config(inventory)
        assert fact_value(PendingSave, inventory=inventory) is False

        again = apply(config_save, inventory=inventory)
        assert not again.did_change()
    finally:
        apply(config, inventory=inventory, path=path, present=False, save=True)


def test_rejected_commit_partial_application_is_a_lab_observation(
    inventory: Inventory,
) -> None:
    """Rejected-commit probe (§12 Q2).

    One ``config`` session whose commit the device refuses because the rule
    references a nonexistent address-group. The apply must fail. Whether any
    subtree element then persists (chain node present? rule present?) is
    observed independently and accepted as either fully absent or partially
    present. That observation is recorded as a lab-release data point, not a
    guarantee; docstring language elsewhere stays conservative regardless of
    outcome.
    """

    path = ["firewall", "ipv4", "name", "PYINFRA-Q2"]
    values = {
        "default-action": "drop",
        "rule": {
            "10": {
                "action": "accept",
                "source": {"group": {"address-group": "pyinfra-q2-missing"}},
            }
        },
    }

    try:
        with pytest.raises(PyinfraError):
            apply(config, inventory=inventory, path=path, values=values)

        tree = fact_value(Configuration, inventory=inventory)
        names = tree.get("firewall", {}).get("ipv4", {}).get("name")
        chain = _instance_child(names, "PYINFRA-Q2")
        chain_present = isinstance(chain, dict)
        rule = _instance_child(chain.get("rule") if chain_present else None, "10")
        rule_present = rule is not None
        fully_absent = not chain_present and not rule_present
        outcome = "fully absent" if fully_absent else "partially present"
        artifact = _capture_dir() / "q2-rejected-commit.txt"
        artifact.write_text(
            f"pyinfra-vyos Q2 lab-release data point: {outcome} "
            f"(chain_present={chain_present}, rule_present={rule_present})\n"
        )
        print(f"pyinfra-vyos Q2 lab-release data point: {artifact}")
    finally:
        apply(config, inventory=inventory, path=path, present=False)


def test_system_basics_cycle_and_q3_domain_probe(inventory: Inventory) -> None:
    """system_basics identity cycle (plan 2.6) plus the §12 Q3 probe.

    Hostname is applied as its current value so per-field Exact noops on an
    equal scalar while time-zone and domain-search change. Independent
    op-mode JSON (``show configuration json``) verifies the desired time-zone
    and the two ``.test`` search domains (set-equality; observed
    ``domain-search`` ordering is a canonicalization hotspot, as is time-zone
    string form). A second identical apply is a controller-side noop (T3).
    Shrinking ``search_domains`` to ``pyinfra-a.test`` proves Exact prunes
    ``pyinfra-b.test``.

    Global ``system name-server`` mutation is excluded from the appliance
    tier because committing blackholed TEST-NET resolvers caused
    deterministic SSH auth timeouts for subsequent sessions on the lab
    release (recovered by reboot; boot config untouched). Name-server
    Exact-list semantics remain covered by unit and @local tiers.

    Q3 (``domain-name`` and ``domain-search`` together) is answered by
    observation, never encoded as controller validation: VyOS may reject
    that coexistence, and that is the Q3 answer. An accepted commit or a
    commit-rejected pyinfra error is written to
    ``q3-domain-interaction.txt`` under the capture dir. The test does not
    fail on either outcome. ``save=False`` throughout: commits touch only
    the active config, so boot is untouched.
    """

    tree = fact_value(Configuration, inventory=inventory)
    system = tree.get("system")
    assert isinstance(system, dict)

    original_hostname = _leaf_scalar(system.get("host-name"))
    original_time_zone = _leaf_scalar(system.get("time-zone"))
    original_search_domains = _leaf_values(system.get("domain-search"))
    assert original_hostname, "system host-name must be present so Exact can noop on the scalar"

    time_zone = "US/Pacific" if original_time_zone in (None, "UTC") else "UTC"
    search_domains = list(_SEARCH_DOMAINS)

    try:
        first = apply(
            system_basics,
            inventory=inventory,
            hostname=original_hostname,
            time_zone=time_zone,
            search_domains=search_domains,
            save=False,
        )
        assert first.did_change()
        _assert_sentinel(first, SENTINEL_CHANGED)

        observed_system = _op_mode_system(inventory)
        observed_search = _leaf_values(observed_system.get("domain-search"))
        assert observed_search is not None
        assert set(observed_search) == set(search_domains)
        observed_time_zone = _leaf_scalar(observed_system.get("time-zone"))
        assert observed_time_zone == time_zone
        assert _leaf_scalar(observed_system.get("host-name")) == original_hostname

        second = apply(
            system_basics,
            inventory=inventory,
            hostname=original_hostname,
            time_zone=time_zone,
            search_domains=search_domains,
            save=False,
        )
        assert not second.did_change()

        pruned = apply(
            system_basics,
            inventory=inventory,
            search_domains=[search_domains[0]],
            save=False,
        )
        assert pruned.did_change()
        _assert_sentinel(pruned, SENTINEL_CHANGED)
        pruned_search = _leaf_values(_op_mode_system(inventory).get("domain-search"))
        assert pruned_search is not None
        assert set(pruned_search) == {search_domains[0]}
        assert search_domains[1] not in pruned_search

        try:
            q3 = apply(
                system_basics,
                inventory=inventory,
                domain_name=_Q3_DOMAIN_NAME,
                search_domains=[search_domains[0]],
                save=False,
            )
        except OperationValueError as error:
            raise AssertionError(
                f"Q3 must not be encoded as controller validation; got OperationValueError: {error}"
            ) from error
        except PyinfraError as error:
            q3_verdict = "commit-rejected"
            q3_detail = f"{type(error).__name__}: {error}"
        else:
            q3_verdict = "accepted commit"
            q3_detail = f"did_change={q3.did_change()}"
            if q3.did_change():
                _assert_sentinel(q3, SENTINEL_CHANGED)

        artifact = _capture_dir() / "q3-domain-interaction.txt"
        artifact.write_text(
            f"pyinfra-vyos Q3 lab-release data point: {q3_verdict} ({q3_detail})\n"
            "domain-search device order (canonicalization hotspot): "
            f"{observed_search!r}\n"
            "time-zone device form (canonicalization hotspot): "
            f"{observed_time_zone!r}\n"
        )
        print(f"pyinfra-vyos Q3 lab-release data point: {artifact}")
    finally:
        apply(
            config,
            inventory=inventory,
            path=["system", "domain-search"],
            present=False,
            save=False,
        )
        apply(
            config,
            inventory=inventory,
            path=["system", "domain-name"],
            present=False,
            save=False,
        )
        if original_time_zone is None:
            apply(
                config,
                inventory=inventory,
                path=["system", "time-zone"],
                present=False,
                save=False,
            )
        else:
            apply(
                system_basics,
                inventory=inventory,
                time_zone=original_time_zone,
                save=False,
            )
        if original_search_domains:
            apply(
                system_basics,
                inventory=inventory,
                search_domains=original_search_domains,
                save=False,
            )


def test_interface_dummy_full_cycle(inventory: Inventory) -> None:
    """interface dummy cycle (plan 3.3).

    ``dum0`` only — no cable, no management-path lockout. ethernet / eth0 is
    never touched. First apply sets address, description, and mtu; independent
    ``show configuration json`` verifies those leaves under ``interfaces dummy
    dum0``. Address CIDR form is a canonicalization hotspot: the device is
    asserted to echo ``192.0.2.65/32`` verbatim (``address`` list
    ``["192.0.2.65/32"]``). Observed address / description / mtu node shapes
    are written to ``phase3-interface-canon.txt``.

    A second identical apply is a controller-side noop (T3). Replacing the
    address set with two new TEST-NET-2 /32s proves Exact prunes the old
    address. ``disabled=True`` / ``disabled=False`` with every other field
    unmanaged proves per-field independence of the ``disable`` node.
    ``present=False`` removes the interface; a second delete is a noop.

    ``save=False`` throughout: commits touch only the active config, so boot
    is untouched. Cleanup always deletes ``dum0`` (already-absent is a noop).
    """

    try:
        first = apply(
            interface,
            inventory=inventory,
            interface=_DUMMY_IFACE,
            interface_type=_DUMMY_TYPE,
            addresses=[_DUMMY_ADDRESS],
            description=_DUMMY_DESCRIPTION,
            mtu=_DUMMY_MTU,
            save=False,
        )
        assert first.did_change()
        _assert_sentinel(first, SENTINEL_CHANGED)

        node = _op_mode_dummy(inventory)
        assert node is not None, "dummy dum0 missing after create"
        observed_addresses = _leaf_values(node.get("address"))
        assert observed_addresses == [_DUMMY_ADDRESS]
        assert _leaf_scalar(node.get("description")) == _DUMMY_DESCRIPTION
        assert _leaf_scalar(node.get("mtu")) == str(_DUMMY_MTU)

        artifact = _capture_dir() / "phase3-interface-canon.txt"
        artifact.write_text(
            "pyinfra-vyos phase3 interface canonicalization hotspot "
            f"(dummy {_DUMMY_IFACE}):\n"
            f"address node: {node.get('address')!r}\n"
            f"address list: {observed_addresses!r}\n"
            f"description node: {node.get('description')!r}\n"
            f"mtu node: {node.get('mtu')!r}\n"
        )
        print(f"pyinfra-vyos phase3 interface canon: {artifact}")

        second = apply(
            interface,
            inventory=inventory,
            interface=_DUMMY_IFACE,
            interface_type=_DUMMY_TYPE,
            addresses=[_DUMMY_ADDRESS],
            description=_DUMMY_DESCRIPTION,
            mtu=_DUMMY_MTU,
            save=False,
        )
        assert not second.did_change()

        replaced = apply(
            interface,
            inventory=inventory,
            interface=_DUMMY_IFACE,
            interface_type=_DUMMY_TYPE,
            addresses=list(_DUMMY_ADDRESSES_REPLACED),
            save=False,
        )
        assert replaced.did_change()
        _assert_sentinel(replaced, SENTINEL_CHANGED)
        replaced_node = _op_mode_dummy(inventory)
        assert replaced_node is not None
        replaced_addresses = _leaf_values(replaced_node.get("address"))
        assert replaced_addresses is not None
        assert set(replaced_addresses) == set(_DUMMY_ADDRESSES_REPLACED)
        assert _DUMMY_ADDRESS not in replaced_addresses

        disabled = apply(
            interface,
            inventory=inventory,
            interface=_DUMMY_IFACE,
            interface_type=_DUMMY_TYPE,
            disabled=True,
            save=False,
        )
        assert disabled.did_change()
        _assert_sentinel(disabled, SENTINEL_CHANGED)
        disabled_node = _op_mode_dummy(inventory)
        assert disabled_node is not None
        assert "disable" in disabled_node

        enabled = apply(
            interface,
            inventory=inventory,
            interface=_DUMMY_IFACE,
            interface_type=_DUMMY_TYPE,
            disabled=False,
            save=False,
        )
        assert enabled.did_change()
        _assert_sentinel(enabled, SENTINEL_CHANGED)
        enabled_node = _op_mode_dummy(inventory)
        assert enabled_node is not None
        assert "disable" not in enabled_node

        deleted = apply(
            interface,
            inventory=inventory,
            interface=_DUMMY_IFACE,
            interface_type=_DUMMY_TYPE,
            present=False,
            save=False,
        )
        assert deleted.did_change()
        _assert_sentinel(deleted, SENTINEL_CHANGED)
        assert _op_mode_dummy(inventory) is None

        deleted_again = apply(
            interface,
            inventory=inventory,
            interface=_DUMMY_IFACE,
            interface_type=_DUMMY_TYPE,
            present=False,
            save=False,
        )
        assert not deleted_again.did_change()
        assert _op_mode_dummy(inventory) is None
    finally:
        apply(
            interface,
            inventory=inventory,
            interface=_DUMMY_IFACE,
            interface_type=_DUMMY_TYPE,
            present=False,
            save=False,
        )


def test_static_route_full_cycle(inventory: Inventory) -> None:
    """static_route cycle (plan 4.3).

    Documentation prefixes only — ``192.0.2.0/24`` (TEST-NET-1) via TEST-NET-3
    next-hops ``203.0.113.1`` / ``203.0.113.2``, plus one ``2001:db8::/32``
    documentation v6 probe submitted in expanded, uppercase form so the
    device's compression is observable rather than trivially equal.
    Next-hops are unreachable; that is fine, no real traffic is sent, and
    default / management-path routes are never touched.

    First apply creates two next-hops; independent ``show configuration json``
    verifies both addresses under ``protocols static route 192.0.2.0/24``. A
    second identical apply is a controller-side noop (T3). Dropping to one hop
    proves whole-object Exact prunes the undeclared hop. Dict-form
    ``next_hops`` then sets ``distance 50`` on the remaining hop.

    The v6 probe submits an expanded, uppercase destination and next-hop and
    records the device's echoed (compressed) forms to
    ``phase4-route-canon.txt``. A second identical v6 apply is recorded as
    noop-or-re-emit (canonicalization mismatch) without failing either way;
    wrong state still fails.

    ``present=False`` removes both routes; a second delete of each is a noop.
    ``save=False`` throughout: commits touch only the active config, so boot
    is untouched. Cleanup always deletes both destinations (already-absent is
    tolerated).
    """

    destinations = (_ROUTE_V4, _ROUTE_V6)
    two_hops: list[str] | dict[str, dict[str, str]] = list(_ROUTE_V4_HOPS)
    one_hop: list[str] | dict[str, dict[str, str]] = [_ROUTE_V4_HOPS[0]]
    distanced_hops: dict[str, dict[str, str]] = {
        _ROUTE_V4_HOPS[0]: {"distance": _ROUTE_V4_DISTANCE}
    }
    v6_hops: list[str] | dict[str, dict[str, str]] = [_ROUTE_V6_HOP]

    try:
        try:
            first = apply(
                static_route,
                inventory=inventory,
                destination=_ROUTE_V4,
                next_hops=two_hops,
                save=False,
            )
        except OperationValueError:
            raise
        except PyinfraError as error:
            artifact = _capture_dir() / "phase4-unreachable-nexthop.txt"
            artifact.write_text(
                "pyinfra-vyos phase4: commit rejected unreachable TEST-NET-3 "
                "next-hops; adapting with an interface-scoped hop via per-hop "
                f"values on loopback {_LOOPBACK_IFACE!r}.\n"
                f"{type(error).__name__}: {error}\n"
            )
            print(f"pyinfra-vyos phase4 unreachable next-hop: {artifact}")
            hop_iface = {"interface": _LOOPBACK_IFACE}
            two_hops = {addr: dict(hop_iface) for addr in _ROUTE_V4_HOPS}
            one_hop = {_ROUTE_V4_HOPS[0]: dict(hop_iface)}
            distanced_hops = {
                _ROUTE_V4_HOPS[0]: {
                    "distance": _ROUTE_V4_DISTANCE,
                    "interface": _LOOPBACK_IFACE,
                }
            }
            v6_hops = {_ROUTE_V6_HOP: dict(hop_iface)}
            first = apply(
                static_route,
                inventory=inventory,
                destination=_ROUTE_V4,
                next_hops=two_hops,
                save=False,
            )
        assert first.did_change()
        _assert_sentinel(first, SENTINEL_CHANGED)

        created = _op_mode_route(inventory, _ROUTE_V4)
        assert created is not None, f"static route {_ROUTE_V4} missing after create"
        created_key, created_node = created
        created_hops = _next_hop_addresses(created_node)
        assert created_hops == set(_ROUTE_V4_HOPS), (
            f"expected next-hops {set(_ROUTE_V4_HOPS)!r} under "
            f"protocols static route {created_key}, got {created_hops!r}"
        )

        second = apply(
            static_route,
            inventory=inventory,
            destination=_ROUTE_V4,
            next_hops=two_hops,
            save=False,
        )
        assert not second.did_change()

        pruned = apply(
            static_route,
            inventory=inventory,
            destination=_ROUTE_V4,
            next_hops=one_hop,
            save=False,
        )
        assert pruned.did_change()
        _assert_sentinel(pruned, SENTINEL_CHANGED)
        pruned_found = _op_mode_route(inventory, _ROUTE_V4)
        assert pruned_found is not None
        pruned_next = _next_hop_addresses(pruned_found[1])
        assert pruned_next == {_ROUTE_V4_HOPS[0]}
        assert _ROUTE_V4_HOPS[1] not in pruned_next

        distanced = apply(
            static_route,
            inventory=inventory,
            destination=_ROUTE_V4,
            next_hops=distanced_hops,
            save=False,
        )
        assert distanced.did_change()
        _assert_sentinel(distanced, SENTINEL_CHANGED)
        distanced_found = _op_mode_route(inventory, _ROUTE_V4)
        assert distanced_found is not None
        observed_hops = _route_next_hops(distanced_found[1])
        assert {ipaddress.ip_address(addr) for addr in observed_hops} == {
            ipaddress.ip_address(_ROUTE_V4_HOPS[0])
        }
        remaining_body = _hop_subtree(observed_hops, _ROUTE_V4_HOPS[0])
        assert _leaf_scalar(remaining_body.get("distance")) == _ROUTE_V4_DISTANCE

        v6_first = apply(
            static_route,
            inventory=inventory,
            destination=_ROUTE_V6,
            next_hops=v6_hops,
            save=False,
        )
        assert v6_first.did_change()
        _assert_sentinel(v6_first, SENTINEL_CHANGED)
        v6_found = _op_mode_route(inventory, _ROUTE_V6)
        assert v6_found is not None, f"static route {_ROUTE_V6} missing after create"
        v6_key, v6_node = v6_found
        v6_next = _route_next_hops(v6_node)
        assert v6_next, f"no next-hop under route6 {v6_key}"
        v6_hop_addrs = {ipaddress.ip_address(addr) for addr in v6_next}
        assert v6_hop_addrs == {ipaddress.ip_address(_ROUTE_V6_HOP)}, (
            f"expected next-hop {_ROUTE_V6_HOP!r} under route6 {v6_key}, got {set(v6_next)!r}"
        )
        assert ipaddress.ip_network(v6_key, strict=False) == ipaddress.ip_network(
            _ROUTE_V6, strict=False
        )

        v6_second = apply(
            static_route,
            inventory=inventory,
            destination=_ROUTE_V6,
            next_hops=v6_hops,
            save=False,
        )
        v6_after = _op_mode_route(inventory, _ROUTE_V6)
        assert v6_after is not None, f"static route {_ROUTE_V6} missing after second apply"
        after_key, after_node = v6_after
        after_next = _route_next_hops(after_node)
        after_addrs = {ipaddress.ip_address(addr) for addr in after_next}
        assert after_addrs == {ipaddress.ip_address(_ROUTE_V6_HOP)}
        assert ipaddress.ip_network(after_key, strict=False) == ipaddress.ip_network(
            _ROUTE_V6, strict=False
        )

        artifact = _capture_dir() / "phase4-route-canon.txt"
        artifact.write_text(
            "pyinfra-vyos phase4 static-route canonicalization hotspot "
            f"(v6 {_ROUTE_V6} via {_ROUTE_V6_HOP}):\n"
            f"submitted destination: {_ROUTE_V6!r}\n"
            f"device destination key: {v6_key!r}\n"
            f"submitted next-hop: {_ROUTE_V6_HOP!r}\n"
            f"device next-hop keys: {sorted(v6_next)!r}\n"
            f"next-hop node: {v6_node.get('next-hop')!r}\n"
            f"second identical apply did_change: {v6_second.did_change()!r}\n"
        )
        print(f"pyinfra-vyos phase4 route canon: {artifact}")

        deleted_v4 = apply(
            static_route,
            inventory=inventory,
            destination=_ROUTE_V4,
            present=False,
            save=False,
        )
        assert deleted_v4.did_change()
        _assert_sentinel(deleted_v4, SENTINEL_CHANGED)
        assert _op_mode_route(inventory, _ROUTE_V4) is None

        deleted_v4_again = apply(
            static_route,
            inventory=inventory,
            destination=_ROUTE_V4,
            present=False,
            save=False,
        )
        assert not deleted_v4_again.did_change()
        assert _op_mode_route(inventory, _ROUTE_V4) is None

        deleted_v6 = apply(
            static_route,
            inventory=inventory,
            destination=v6_key,
            present=False,
            save=False,
        )
        assert deleted_v6.did_change()
        _assert_sentinel(deleted_v6, SENTINEL_CHANGED)
        assert _op_mode_route(inventory, _ROUTE_V6) is None

        deleted_v6_again = apply(
            static_route,
            inventory=inventory,
            destination=v6_key,
            present=False,
            save=False,
        )
        assert not deleted_v6_again.did_change()
        assert _op_mode_route(inventory, _ROUTE_V6) is None
    finally:
        for destination in destinations:
            _delete_static_route(inventory, destination)


def test_user_full_cycle_and_deletion_guard(inventory: Inventory) -> None:
    """user cycle (plan 5.3) plus the connecting-user deletion guard.

    Disposable ``pyinfra-test`` only — the connecting ``vyos`` user's config
    is never mutated except by the guard probe, which must fail at planning
    with the device untouched. First apply sets full-name, a sha512-crypt
    hash, and one ed25519 public key; independent ``show configuration json``
    verifies those leaves under ``system login user pyinfra-test``. The
    encrypted-password leaf is asserted as a stable-text round-trip (device
    echo equals the submitted hash). Public-key body whitespace/format is a
    canonicalization hotspot: submitted vs device echo are written to
    ``phase5-user-canon.txt``. The password hash is never written to that
    capture — only ``hash_roundtrip_stable: True/False``.

    A second identical apply is a controller-side noop (T3). Rotating to a
    different ``$6$`` digest reports changed and the new hash is independently
    confirmed. The sensitive-command suppression path is not triggered on a
    successful rotate; correctness of suppression on failure is unit
    territory.

    Guard probe: ``present=False`` on the connecting ``vyos`` user is a
    planning ``OperationValueError`` (self-deletion); a follow-up read
    asserts ``vyos`` still exists with an unchanged authentication subtree.
    ``present=False`` on ``pyinfra-test`` (connected as ``vyos``) removes it;
    a second delete is a noop.

    ``save=False`` throughout: commits touch only the active config, so boot
    is untouched. Cleanup always deletes ``pyinfra-test`` (already-absent is
    tolerated) and never targets ``vyos``.
    """

    try:
        first = apply(
            user,
            inventory=inventory,
            user=_TEST_USER,
            full_name=_TEST_FULL_NAME,
            encrypted_password=_TEST_PASSWORD_HASH,
            ssh_keys=_TEST_SSH_KEYS,
            save=False,
        )
        assert first.did_change()
        _assert_sentinel(first, SENTINEL_CHANGED)

        node = _op_mode_login_user(inventory, _TEST_USER)
        assert node is not None, f"login user {_TEST_USER} missing after create"
        assert _leaf_scalar(node.get("full-name")) == _TEST_FULL_NAME

        auth = node.get("authentication")
        assert isinstance(auth, dict), f"authentication missing under {_TEST_USER}: {node!r}"
        observed_hash = _leaf_scalar(auth.get("encrypted-password"))
        hash_roundtrip_stable = observed_hash == _TEST_PASSWORD_HASH

        public_keys = auth.get("public-keys")
        key_node = _instance_child(public_keys, _TEST_SSH_KEY_ID)
        assert key_node is not None, (
            f"public-keys {_TEST_SSH_KEY_ID!r} missing under {_TEST_USER}: {public_keys!r}"
        )
        assert isinstance(key_node, dict), f"unexpected public-key node: {key_node!r}"
        observed_type = _leaf_scalar(key_node.get("type"))
        observed_key = _leaf_scalar(key_node.get("key"))
        assert observed_type == _TEST_SSH_KEY_TYPE

        artifact = _capture_dir() / "phase5-user-canon.txt"
        artifact.write_text(
            "pyinfra-vyos phase5 user canonicalization hotspot "
            f"(public-keys {_TEST_SSH_KEY_ID}):\n"
            f"submitted type: {_TEST_SSH_KEY_TYPE!r}\n"
            f"submitted key body: {_TEST_SSH_KEY!r}\n"
            f"device public-keys node: {public_keys!r}\n"
            f"device type: {observed_type!r}\n"
            f"device key body: {observed_key!r}\n"
            f"hash_roundtrip_stable: {hash_roundtrip_stable}\n"
        )
        capture_text = artifact.read_text()
        assert "$6$" not in capture_text
        assert _TEST_PASSWORD_HASH not in capture_text
        assert _TEST_PASSWORD_HASH_ROTATED not in capture_text
        print(f"pyinfra-vyos phase5 user canon: {artifact}")

        assert hash_roundtrip_stable, (
            "encrypted-password did not round-trip as stable text "
            f"(submitted length {len(_TEST_PASSWORD_HASH)}, "
            f"device length {len(observed_hash) if observed_hash is not None else 0})"
        )

        second = apply(
            user,
            inventory=inventory,
            user=_TEST_USER,
            full_name=_TEST_FULL_NAME,
            encrypted_password=_TEST_PASSWORD_HASH,
            ssh_keys=_TEST_SSH_KEYS,
            save=False,
        )
        assert not second.did_change()

        rotated = apply(
            user,
            inventory=inventory,
            user=_TEST_USER,
            encrypted_password=_TEST_PASSWORD_HASH_ROTATED,
            save=False,
        )
        assert rotated.did_change()
        _assert_sentinel(rotated, SENTINEL_CHANGED)
        rotated_output = "\n".join(
            part for part in (rotated.stdout, getattr(rotated, "stderr", None)) if part
        )
        assert _SENSITIVE_SUPPRESSION not in rotated_output
        rotated_node = _op_mode_login_user(inventory, _TEST_USER)
        assert rotated_node is not None
        rotated_auth = rotated_node.get("authentication")
        assert isinstance(rotated_auth, dict)
        assert _leaf_scalar(rotated_auth.get("encrypted-password")) == _TEST_PASSWORD_HASH_ROTATED

        vyos_before = _op_mode_login_user(inventory, _CONNECTING_USER)
        assert vyos_before is not None, f"connecting user {_CONNECTING_USER} missing before guard"
        vyos_auth_before = vyos_before.get("authentication")

        with pytest.raises(
            OperationValueError,
            match=r"(?i)(self-delet|connected user|connecting user)",
        ):
            apply(
                user,
                inventory=inventory,
                user=_CONNECTING_USER,
                present=False,
                save=False,
            )

        vyos_after = _op_mode_login_user(inventory, _CONNECTING_USER)
        assert vyos_after is not None, f"connecting user {_CONNECTING_USER} missing after guard"
        assert vyos_after.get("authentication") == vyos_auth_before

        deleted = apply(
            user,
            inventory=inventory,
            user=_TEST_USER,
            present=False,
            save=False,
        )
        assert deleted.did_change()
        _assert_sentinel(deleted, SENTINEL_CHANGED)
        assert _op_mode_login_user(inventory, _TEST_USER) is None

        deleted_again = apply(
            user,
            inventory=inventory,
            user=_TEST_USER,
            present=False,
            save=False,
        )
        assert not deleted_again.did_change()
        assert _op_mode_login_user(inventory, _TEST_USER) is None
    finally:
        _delete_login_user(inventory, _TEST_USER)
        assert _op_mode_login_user(inventory, _TEST_USER) is None, (
            f"cleanup left login user {_TEST_USER} on the device"
        )
