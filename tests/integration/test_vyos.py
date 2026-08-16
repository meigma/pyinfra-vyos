from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from pyinfra.api import FileUploadCommand, StringCommand
from pyinfra.api.exceptions import OperationValueError

from pyinfra_vyos import (
    Configuration,
    ConfigurationCommands,
    Version,
    config,
    config_load,
    config_save,
    interface,
    static_route,
    system_basics,
)
from pyinfra_vyos._cli import sg_probe, sg_vbash_run
from pyinfra_vyos._session import SENTINEL_CHANGED

from ._helpers import fact_value, operation_commands, prepare

pytestmark = pytest.mark.integration

_VALID_CONFIG = "set system host-name pyinfra-vyos\n"
_WHITESPACE_ONLY = "  \n\t\n  "
_GREP_GUARD = "LC_ALL=C grep -q '[^[:space:]]'"


@pytest.fixture
def sample_config(tmp_path: Path) -> str:
    path = tmp_path / "vyos.conf"
    path.write_text(_VALID_CONFIG)
    return str(path)


def _staging_of(commands: list[Any]) -> str:
    upload = commands[2]
    assert isinstance(upload, FileUploadCommand)
    assert upload.dest.endswith("/config")
    return upload.dest[: -len("/config")]


def _script_text(commands: list[Any]) -> str:
    upload = commands[4]
    assert isinstance(upload, FileUploadCommand)
    assert isinstance(upload.src, StringIO)
    return upload.src.getvalue()


def test_config_load_prepare_renders_the_seven_command_sequence(sample_config: str) -> None:
    state, _meta = prepare(config_load, src=sample_config)
    commands = operation_commands(state)
    staging = _staging_of(commands)

    assert len(commands) == 7
    probe, mkdir, config_upload, grep_guard, script_upload, chmod_script, vbash = commands

    assert isinstance(probe, StringCommand)
    assert probe.get_raw_value() == sg_probe().get_raw_value()

    assert isinstance(mkdir, StringCommand)
    assert mkdir.bits[0] == "mkdir"
    assert mkdir.bits[1] == "-m"
    assert mkdir.bits[2] == "700"
    assert "-p" not in mkdir.bits
    assert mkdir.get_raw_value().startswith("mkdir -m 700 ")

    assert isinstance(config_upload, FileUploadCommand)
    assert config_upload.src == sample_config
    assert config_upload.dest == f"{staging}/config"

    assert isinstance(grep_guard, StringCommand)
    assert grep_guard.get_raw_value().startswith("chmod 600 ")
    assert _GREP_GUARD in grep_guard.get_raw_value()

    assert isinstance(script_upload, FileUploadCommand)
    assert isinstance(script_upload.src, StringIO)
    assert script_upload.dest == f"{staging}/session.sh"

    assert isinstance(chmod_script, StringCommand)
    assert chmod_script.get_raw_value().startswith("chmod 600 ")
    assert chmod_script.get_raw_value().endswith(f"{staging}/session.sh")

    assert isinstance(vbash, StringCommand)
    rendered = vbash.get_raw_value()
    assert rendered == sg_vbash_run(f"{staging}/session.sh", staging).get_raw_value()
    assert rendered.index("/bin/vbash") < rendered.index("rm -rf")


def test_config_load_src_rejections_surface_as_operation_value_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.conf"
    with pytest.raises(OperationValueError, match="cannot read src"):
        prepare(config_load, src=str(missing))

    empty = tmp_path / "empty.conf"
    empty.write_text(_WHITESPACE_ONLY)
    with pytest.raises(OperationValueError, match="empty or whitespace-only"):
        prepare(config_load, src=str(empty))

    class _NonSeekable:
        def read(self, size: int = -1) -> str:
            return _VALID_CONFIG

        def seekable(self) -> bool:
            return False

    with pytest.raises(OperationValueError, match="seekable"):
        prepare(config_load, src=_NonSeekable())  # type: ignore[arg-type]


def test_facts_on_local_return_default_when_vbash_is_absent() -> None:
    assert fact_value(Version) == Version.default()
    assert fact_value(Configuration) == Configuration.default()
    assert fact_value(ConfigurationCommands) == ConfigurationCommands.default()


def test_facts_on_local_parse_shimmed_vbash_output(vbash_shim: Path) -> None:
    """``fact_value`` builds its own State; PATH still reaches the @local subprocess."""

    assert fact_value(Version)["version"] == "VyOS 2026.03"
    assert fact_value(Configuration) == {}


def test_two_prepare_evaluations_produce_different_staging_tokens(sample_config: str) -> None:
    first = _staging_of(operation_commands(prepare(config_load, src=sample_config)[0]))
    second = _staging_of(operation_commands(prepare(config_load, src=sample_config)[0]))

    assert first != second
    assert first.startswith("/tmp/pyinfra-vyos-")
    assert second.startswith("/tmp/pyinfra-vyos-")


def test_save_true_propagates_into_the_uploaded_script(sample_config: str) -> None:
    without_save = _script_text(
        operation_commands(prepare(config_load, src=sample_config, save=False)[0]),
    )
    with_save = _script_text(
        operation_commands(prepare(config_load, src=sample_config, save=True)[0]),
    )

    assert SENTINEL_CHANGED in with_save
    assert "_save_out=$(save)" in with_save
    assert "did_save=1" in with_save

    assert SENTINEL_CHANGED in without_save
    assert "_save_out=$(save)" not in without_save
    assert "did_save=1" not in without_save


def test_config_prepare_renders_the_five_command_sequence() -> None:
    """@local has no vbash, so Configuration returns default() ({}): every
    desired value is missing and the prepared delta is pure sets."""

    state, meta = prepare(
        config,
        path=["system", "static-host-mapping", "host-name", "a.test"],
        values={"inet": "192.0.2.1"},
    )
    commands = operation_commands(state)

    assert meta.will_change
    assert len(commands) == 5
    probe, mkdir, upload, chmod, run = commands
    assert isinstance(probe, StringCommand)
    assert probe.get_raw_value() == sg_probe().get_raw_value()
    assert mkdir.get_raw_value().startswith("mkdir -m 700 ")
    assert isinstance(upload, FileUploadCommand)
    assert upload.dest.endswith("/session.sh")
    assert isinstance(upload.src, StringIO)
    script = upload.src.getvalue()
    assert "set system static-host-mapping host-name a.test inet 192.0.2.1" in script
    assert "delete" not in script.replace("_cmd", "")
    assert chmod.get_raw_value().startswith("chmod 600 ")
    rendered = run.get_raw_value()
    assert rendered == sg_vbash_run(upload.dest, upload.dest[: -len("/session.sh")]).get_raw_value()


def test_config_replace_orders_deletes_before_sets() -> None:
    """With an empty active tree, replace of a fresh path emits only sets;
    the delete-before-set ordering is proven at the _tree tier. Here we pin
    that the rendered script applies commands before the commit gate."""

    state, _meta = prepare(
        config,
        path=["service", "ntp"],
        values={"server": {"time1.test": {}}},
        replace=True,
    )
    upload = operation_commands(state)[2]
    script = upload.src.getvalue()

    assert script.index("set service ntp server time1.test") < script.index("sessionChanged")


def test_config_absent_path_with_present_false_noops() -> None:
    _state, meta = prepare(config, path=["service", "ntp"], present=False)

    assert not meta.will_change


def test_config_bare_path_creation_sets_the_node() -> None:
    state, meta = prepare(config, path=["service", "mdns", "repeater"])
    upload = operation_commands(state)[2]

    assert meta.will_change
    assert "set service mdns repeater" in upload.src.getvalue()


def test_config_rejections_surface_as_operation_value_error() -> None:
    with pytest.raises(OperationValueError):
        prepare(config, path=[])
    with pytest.raises(OperationValueError):
        prepare(config, path=["system"], values={"key": 7})
    with pytest.raises(OperationValueError):
        prepare(config, path=["system"], values={}, present=False)


def test_config_save_propagates_into_the_uploaded_script() -> None:
    def script(save: bool) -> str:
        state, _meta = prepare(
            config, path=["service", "ntp"], values={"server": {"t.test": {}}}, save=save
        )
        return operation_commands(state)[2].src.getvalue()

    with_save = script(True)
    without_save = script(False)
    gate = 'if [ "$did_commit" -ne 0 ]; then'

    assert "_save_out=$(save)" in with_save
    assert gate in with_save
    assert with_save.index(gate) < with_save.index("_save_out=$(save)")
    assert "_save_out=$(save)" not in without_save


def test_config_save_fails_closed_when_pending_save_is_unknown() -> None:
    """@local has no vbash, so PendingSave is None and config_save fails closed."""

    with pytest.raises(OperationValueError, match="saved-state could not be established"):
        prepare(config_save)


def test_system_basics_prepare_renders_the_five_command_sequence(vbash_shim: Path) -> None:
    """Fixture Configuration is {}; every provided field plans as a set."""

    state, meta = prepare(
        system_basics,
        hostname="gw",
        domain_name="example.net",
        name_servers=["8.8.8.8"],
        search_domains=["example.net"],
        time_zone="UTC",
    )
    commands = operation_commands(state)

    assert meta.will_change
    assert len(commands) == 5
    probe, mkdir, upload, chmod, run = commands
    assert isinstance(probe, StringCommand)
    assert probe.get_raw_value() == sg_probe().get_raw_value()
    assert mkdir.get_raw_value().startswith("mkdir -m 700 ")
    assert isinstance(upload, FileUploadCommand)
    assert upload.dest.endswith("/session.sh")
    assert isinstance(upload.src, StringIO)
    script = upload.src.getvalue()
    assert "set system host-name gw" in script
    assert "set system domain-name example.net" in script
    assert "set system name-server 8.8.8.8" in script
    assert "set system domain-search example.net" in script
    assert "set system time-zone UTC" in script
    assert "delete" not in script.replace("_cmd", "")
    assert chmod.get_raw_value().startswith("chmod 600 ")
    rendered = run.get_raw_value()
    assert rendered == sg_vbash_run(upload.dest, upload.dest[: -len("/session.sh")]).get_raw_value()
    assert vbash_shim.is_file()


def test_system_basics_all_none_raises_operation_value_error() -> None:
    with pytest.raises(OperationValueError):
        prepare(system_basics)


def test_system_basics_without_vbash_fails_closed_on_unknown_version() -> None:
    """@local has no vbash, so Version is default/empty and the gate fails closed."""

    with pytest.raises(OperationValueError) as caught:
        prepare(system_basics, hostname="gw")

    message = str(caught.value)
    assert "config" in message
    assert "config_load" in message


def test_system_basics_noops_when_the_delta_is_empty(vbash_shim: Path) -> None:
    """Fixture Configuration is {}; own-and-empty list leaves plan nothing."""

    _state, meta = prepare(system_basics, name_servers=[], search_domains=[])

    assert not meta.will_change


def test_interface_prepare_renders_the_five_command_sequence(vbash_shim: Path) -> None:
    """Fixture Configuration is {}; a new dummy address plans as a set."""

    state, meta = prepare(
        interface,
        interface="dum0",
        interface_type="dummy",
        addresses=["192.0.2.1/32"],
    )
    commands = operation_commands(state)

    assert meta.will_change
    assert len(commands) == 5
    probe, mkdir, upload, chmod, run = commands
    assert isinstance(probe, StringCommand)
    assert probe.get_raw_value() == sg_probe().get_raw_value()
    assert mkdir.get_raw_value().startswith("mkdir -m 700 ")
    assert isinstance(upload, FileUploadCommand)
    assert upload.dest.endswith("/session.sh")
    assert isinstance(upload.src, StringIO)
    script = upload.src.getvalue()
    assert "set interfaces dummy dum0 address 192.0.2.1/32" in script
    assert chmod.get_raw_value().startswith("chmod 600 ")
    rendered = run.get_raw_value()
    assert rendered == sg_vbash_run(upload.dest, upload.dest[: -len("/session.sh")]).get_raw_value()
    assert vbash_shim.is_file()


def test_interface_absent_on_empty_tree_noops(vbash_shim: Path) -> None:
    """Fixture Configuration is {}; present=False of a missing node noops."""

    _state, meta = prepare(interface, interface="dum0", interface_type="dummy", present=False)

    assert not meta.will_change
    assert vbash_shim.is_file()


def test_interface_invalid_type_surfaces_as_operation_value_error(vbash_shim: Path) -> None:
    with pytest.raises(OperationValueError):
        prepare(interface, interface="dum0", interface_type="bridge")
    assert vbash_shim.is_file()


def test_interface_typed_key_collision_surfaces_as_operation_value_error(vbash_shim: Path) -> None:
    with pytest.raises(OperationValueError):
        prepare(
            interface,
            interface="dum0",
            interface_type="dummy",
            values={"address": ["192.0.2.1/32"]},
        )
    assert vbash_shim.is_file()


def test_interface_without_vbash_fails_closed_on_unknown_version() -> None:
    """@local has no vbash, so Version is default/empty and the gate fails closed."""

    with pytest.raises(OperationValueError) as caught:
        prepare(interface, interface="dum0", interface_type="dummy")

    message = str(caught.value)
    assert "config" in message
    assert "config_load" in message


def test_static_route_prepare_renders_the_five_command_sequence(vbash_shim: Path) -> None:
    """Fixture Configuration is {}; a new IPv4 next-hop plans as a set."""

    state, meta = prepare(
        static_route,
        destination="203.0.113.0/24",
        next_hops=["192.0.2.1"],
    )
    commands = operation_commands(state)

    assert meta.will_change
    assert len(commands) == 5
    probe, mkdir, upload, chmod, run = commands
    assert isinstance(probe, StringCommand)
    assert probe.get_raw_value() == sg_probe().get_raw_value()
    assert mkdir.get_raw_value().startswith("mkdir -m 700 ")
    assert isinstance(upload, FileUploadCommand)
    assert upload.dest.endswith("/session.sh")
    assert isinstance(upload.src, StringIO)
    script = upload.src.getvalue()
    assert "set protocols static route 203.0.113.0/24 next-hop 192.0.2.1" in script
    assert chmod.get_raw_value().startswith("chmod 600 ")
    rendered = run.get_raw_value()
    assert rendered == sg_vbash_run(upload.dest, upload.dest[: -len("/session.sh")]).get_raw_value()
    assert vbash_shim.is_file()


def test_static_route_v6_prepare_renders_route6(vbash_shim: Path) -> None:
    """Fixture Configuration is {}; a new IPv6 next-hop plans as a route6 set."""

    state, meta = prepare(
        static_route,
        destination="2001:db8::/64",
        next_hops=["2001:db8::1"],
    )
    commands = operation_commands(state)

    assert meta.will_change
    assert len(commands) == 5
    upload = commands[2]
    assert isinstance(upload, FileUploadCommand)
    assert isinstance(upload.src, StringIO)
    assert "set protocols static route6 2001:db8::/64 next-hop 2001:db8::1" in upload.src.getvalue()
    assert vbash_shim.is_file()


def test_static_route_absent_on_empty_tree_noops(vbash_shim: Path) -> None:
    """Fixture Configuration is {}; present=False of a missing node noops."""

    _state, meta = prepare(static_route, destination="203.0.113.0/24", present=False)

    assert not meta.will_change
    assert vbash_shim.is_file()


def test_static_route_empty_body_surfaces_as_operation_value_error(vbash_shim: Path) -> None:
    with pytest.raises(OperationValueError):
        prepare(static_route, destination="203.0.113.0/24")
    assert vbash_shim.is_file()


def test_static_route_without_vbash_fails_closed_on_unknown_version() -> None:
    """@local has no vbash, so Version is default/empty and the gate fails closed."""

    with pytest.raises(OperationValueError) as caught:
        prepare(static_route, destination="203.0.113.0/24", next_hops=["192.0.2.1"])

    message = str(caught.value)
    assert "config" in message
    assert "config_load" in message
