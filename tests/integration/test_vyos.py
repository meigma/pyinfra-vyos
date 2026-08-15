from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from pyinfra.api import FileUploadCommand, StringCommand
from pyinfra.api.exceptions import OperationValueError

from pyinfra_vyos import Configuration, ConfigurationCommands, Version, config_load
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
