from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any

import pytest
from pyinfra.api import Inventory, StringCommand
from pyinfra.api.operation import OperationMeta

from pyinfra_vyos import Configuration, ConfigurationCommands, Version, config_load
from pyinfra_vyos._cli import vyos_op_command
from pyinfra_vyos._parse import OUTPUT_MARKER, strip_marker
from pyinfra_vyos._session import SENTINEL_CHANGED, SENTINEL_NOOP

from ._helpers import appliance_inventory, apply, fact_value, new_state

pytestmark = pytest.mark.appliance

_BOOT_PATH = "/config/config.boot"
_VERSION_FOOTER = "// vyos-config-version"
_TEST_INET = "192.0.2.1"
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
    return config[: match.end()] + block + config[match.end()]


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
