from __future__ import annotations

import inspect
import os
from io import StringIO
from pathlib import Path
from typing import IO, Any

import pytest
from pyinfra.api import FileUploadCommand, StringCommand
from pyinfra.api.exceptions import OperationValueError
from pyinfra.api.state import State
from pyinfra.context import ctx_state

import pyinfra_vyos
from pyinfra_vyos._cli import sg_probe, sg_vbash_run
from pyinfra_vyos._session import SENTINEL_CHANGED
from pyinfra_vyos.operations import _guarded, _SourceError, config, config_load

_VALID_CONFIG = "set system host-name pyinfra-vyos\n"
_WHITESPACE_ONLY = "  \n\t\n  "
_GREP_GUARD = "LC_ALL=C grep -q '[^[:space:]]'"


def _yielded(src: str | IO[Any], *, save: bool = False) -> list[Any]:
    return list(config_load._inner(src, save=save))


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


def test_guarded_returns_the_wrapped_result() -> None:
    assert _guarded(lambda: "ok") == "ok"


def test_guarded_converts_domain_errors_to_operation_value_error() -> None:
    def reject() -> None:
        raise _SourceError("bad src")

    with pytest.raises(OperationValueError, match="bad src"):
        _guarded(reject)


def test_guarded_does_not_swallow_unrelated_errors() -> None:
    def explode() -> None:
        raise ZeroDivisionError("boom")

    with pytest.raises(ZeroDivisionError):
        _guarded(explode)


def test_config_load_signature_keeps_save_keyword_only() -> None:
    parameters = inspect.signature(config_load).parameters

    assert [name for name, p in parameters.items() if p.kind is p.KEYWORD_ONLY] == ["save"]
    assert parameters["save"].default is False


def test_config_load_is_marked_not_idempotent() -> None:
    assert config_load.is_idempotent is False
    assert config_load.idempotent_notice == (
        "device mutation is compare-gated on the target; "
        "pyinfra always reports this operation as changed"
    )


def test_config_signature_keeps_flags_keyword_only() -> None:
    parameters = inspect.signature(config).parameters

    keyword_only = [name for name, p in parameters.items() if p.kind is p.KEYWORD_ONLY]
    assert keyword_only == ["replace", "present", "save"]
    assert parameters["values"].default is None
    assert parameters["replace"].default is False
    assert parameters["present"].default is True
    assert parameters["save"].default is False


def test_config_is_idempotent_via_controller_diff() -> None:
    assert config.is_idempotent is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"path": []},
        {"path": "system"},
        {"path": ["system", "-x"]},
        {"path": ["system"], "values": {"key": 7}},
        {"path": ["system"], "values": {"-dash": "v"}},
        {"path": ["system"], "values": "not-a-dict"},
    ],
)
def test_config_input_rejections_surface_before_any_host_access(kwargs: dict[str, Any]) -> None:
    """Validation raises OperationValueError before host.get_fact is reached.

    These run without pyinfra host context: reaching the fact lookup would
    raise a context error instead, so passing proves validation comes first.
    """

    with pytest.raises(OperationValueError):
        list(config._inner(**kwargs))


def test_config_present_false_rejects_values_and_replace() -> None:
    with pytest.raises(OperationValueError, match="values"):
        list(config._inner(path=["system"], values={}, present=False))
    with pytest.raises(OperationValueError, match="replace"):
        list(config._inner(path=["system"], replace=True, present=False))


def test_package_exports_the_public_primitives() -> None:
    assert pyinfra_vyos.__all__ == [
        "Configuration",
        "ConfigurationCommands",
        "Version",
        "config",
        "config_load",
    ]
    for exported in pyinfra_vyos.__all__:
        assert getattr(pyinfra_vyos, exported) is not None


def test_nonexistent_path_is_rejected(tmp_path: Path) -> None:
    missing = tmp_path / "missing.conf"

    with pytest.raises(OperationValueError, match="cannot read src"):
        _yielded(str(missing))


def test_whitespace_only_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.conf"
    path.write_text(_WHITESPACE_ONLY)

    with pytest.raises(OperationValueError, match="empty or whitespace-only"):
        _yielded(str(path))


def test_non_seekable_file_like_is_rejected() -> None:
    class _NonSeekable:
        def read(self, size: int = -1) -> str:
            return _VALID_CONFIG

        def seekable(self) -> bool:
            return False

    with pytest.raises(OperationValueError, match="seekable"):
        _yielded(_NonSeekable())  # type: ignore[arg-type]


def test_str_src_joins_state_cwd_like_files_put(tmp_path: Path) -> None:
    """Pin files.put 3.10.0 resolution: ``os.path.join(state.cwd, src)``.

    ``files.put`` (default ``add_deploy_dir=True``) does *not* use
    ``get_file_path``'s ``./``-relative-to-exec-file rule. An absolute ``src``
    is kept as-is because ``os.path.join`` discards the cwd prefix.
    """

    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    (deploy_dir / "vyos.conf").write_text(_VALID_CONFIG)
    other_dir = tmp_path / "other"
    other_dir.mkdir()

    op_state = State()
    op_state.cwd = str(deploy_dir)
    with ctx_state.use(op_state):
        relative = _yielded("./vyos.conf")
        absolute = _yielded(str(deploy_dir / "vyos.conf"))

    relative_upload = relative[2]
    absolute_upload = absolute[2]
    assert isinstance(relative_upload, FileUploadCommand)
    assert isinstance(absolute_upload, FileUploadCommand)
    assert relative_upload.src == os.path.join(str(deploy_dir), "./vyos.conf")
    assert absolute_upload.src == str(deploy_dir / "vyos.conf")

    op_state.cwd = str(other_dir)
    abs_src = str(deploy_dir / "vyos.conf")
    with ctx_state.use(op_state):
        joined_absolute = _yielded(abs_src)

    joined_upload = joined_absolute[2]
    assert isinstance(joined_upload, FileUploadCommand)
    assert joined_upload.src == os.path.join(str(other_dir), abs_src)
    assert joined_upload.src == abs_src


def test_yielded_command_sequence(tmp_path: Path) -> None:
    path = tmp_path / "vyos.conf"
    path.write_text(_VALID_CONFIG)

    commands = _yielded(str(path))
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
    assert config_upload.src == str(path)
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


def test_chmod_follows_each_upload(tmp_path: Path) -> None:
    path = tmp_path / "vyos.conf"
    path.write_text(_VALID_CONFIG)
    commands = _yielded(str(path))

    assert isinstance(commands[2], FileUploadCommand)
    assert isinstance(commands[3], StringCommand)
    assert "chmod 600" in commands[3].get_raw_value()

    assert isinstance(commands[4], FileUploadCommand)
    assert isinstance(commands[5], StringCommand)
    assert commands[5].get_raw_value().startswith("chmod 600 ")


def test_staging_token_is_fresh_across_generator_evaluations() -> None:
    src = StringIO(_VALID_CONFIG)
    first = _staging_of(_yielded(src))
    second = _staging_of(_yielded(src))

    assert first != second
    assert first.startswith("/tmp/pyinfra-vyos-")
    assert second.startswith("/tmp/pyinfra-vyos-")


def test_save_flag_propagates_into_uploaded_script() -> None:
    without_save = _script_text(_yielded(StringIO(_VALID_CONFIG), save=False))
    with_save = _script_text(_yielded(StringIO(_VALID_CONFIG), save=True))

    assert SENTINEL_CHANGED in with_save
    assert "_save_out=$(save)" in with_save
    assert "did_save=1" in with_save

    assert SENTINEL_CHANGED in without_save
    assert "_save_out=$(save)" not in without_save
    assert "did_save=1" not in without_save
