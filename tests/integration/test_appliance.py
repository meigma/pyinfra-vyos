from __future__ import annotations

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
    system_basics,
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
