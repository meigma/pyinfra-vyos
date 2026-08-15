from __future__ import annotations

import re
import secrets
from pathlib import Path

import pytest
from pyinfra.api import Inventory
from pyinfra.api.operation import OperationMeta

from pyinfra_vyos import Configuration, ConfigurationCommands, Version, config_load
from pyinfra_vyos._cli import vyos_op_command
from pyinfra_vyos._parse import OUTPUT_MARKER, strip_marker
from pyinfra_vyos._session import SENTINEL_CHANGED, SENTINEL_NOOP

from ._helpers import appliance_inventory, apply, fact_value, new_state

pytestmark = pytest.mark.appliance

_SYSTEM_OPEN = re.compile(r"system\s*\{")
_SYSTEM_DESCRIPTION = re.compile(r"\n(\t| {4})description\s+(\"[^\"]*\"|\S+)")


@pytest.fixture
def inventory() -> Inventory:
    return appliance_inventory()


def _op_mode_text(inventory: Inventory, *argv: str) -> str:
    state = new_state(inventory)
    host = next(iter(state.inventory))
    status, output = host.run_shell_command(vyos_op_command(*argv, marker=OUTPUT_MARKER))
    assert status, output.stderr
    return "\n".join(strip_marker(list(output.stdout_lines))) + "\n"


def _system_block_span(config: str) -> tuple[int, int]:
    match = _SYSTEM_OPEN.search(config)
    if match is None:
        raise AssertionError("running config has no top-level system block")
    index = match.end()
    depth = 1
    while index < len(config) and depth:
        char = config[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return match.start(), index


def _with_system_description(config: str, value: str) -> str:
    quoted = f'"{value}"'
    start, end = _system_block_span(config)
    block = config[start:end]
    replaced, count = _SYSTEM_DESCRIPTION.subn(
        rf"\n\1description {quoted}",
        block,
        count=1,
    )
    if count:
        return config[:start] + replaced + config[end:]
    inserted = re.sub(
        r"(system\s*\{)",
        rf"\1\n    description {quoted}",
        block,
        count=1,
    )
    return config[:start] + inserted + config[end:]


def _assert_sentinel(meta: OperationMeta, sentinel: str) -> None:
    assert sentinel in meta.stdout, (
        f"expected {sentinel!r} in operation stdout, got {meta.stdout!r}"
    )


def test_version_returns_a_version_key(inventory: Inventory, tmp_path: Path) -> None:
    raw = _op_mode_text(inventory, "show", "version")
    artifact = tmp_path / "show-version.txt"
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
    original = _op_mode_text(inventory, "show", "configuration")
    token = f"pyinfra-vyos-{secrets.token_hex(8)}"
    mutated = _with_system_description(original, token)
    assert mutated != original

    original_path = tmp_path / "original.conf"
    mutated_path = tmp_path / "mutated.conf"
    original_path.write_text(original)
    mutated_path.write_text(mutated)

    try:
        first = apply(config_load, inventory=inventory, src=str(mutated_path), save=False)
        _assert_sentinel(first, SENTINEL_CHANGED)
        tree = fact_value(Configuration, inventory=inventory)
        assert tree["system"]["description"] == token

        second = apply(config_load, inventory=inventory, src=str(mutated_path), save=False)
        _assert_sentinel(second, SENTINEL_NOOP)
        assert SENTINEL_CHANGED not in second.stdout

        saved = apply(config_load, inventory=inventory, src=str(mutated_path), save=True)
        _assert_sentinel(saved, SENTINEL_CHANGED)
        persisted = apply(config_load, inventory=inventory, src=str(mutated_path), save=True)
        _assert_sentinel(persisted, SENTINEL_NOOP)
        assert fact_value(Configuration, inventory=inventory)["system"]["description"] == token
    finally:
        apply(config_load, inventory=inventory, src=str(original_path), save=True)
