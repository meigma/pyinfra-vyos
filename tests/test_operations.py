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
from pyinfra_vyos._render import (
    Absent,
    Exact,
    Merge,
    Scope,
    render_firewall_group,
    render_static_route,
    render_user,
)
from pyinfra_vyos._session import SENTINEL_CHANGED, PlannedCommand
from pyinfra_vyos.facts import Configuration
from pyinfra_vyos.operations import (
    _guarded,
    _plan_scopes,
    _require_deletable_identity,
    _SourceError,
    config,
    config_load,
    config_save,
    firewall_group,
    interface,
    static_route,
    system_basics,
    user,
)

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


def test_config_save_signature_has_zero_parameters() -> None:
    assert list(inspect.signature(config_save).parameters) == []


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
        "PendingSave",
        "Version",
        "config",
        "config_load",
        "config_save",
        "firewall_group",
        "interface",
        "static_route",
        "system_basics",
        "user",
    ]
    for exported in pyinfra_vyos.__all__:
        assert getattr(pyinfra_vyos, exported) is not None


def test_system_basics_signature_is_keyword_only() -> None:
    parameters = inspect.signature(system_basics).parameters

    assert list(parameters) == [
        "hostname",
        "domain_name",
        "name_servers",
        "search_domains",
        "time_zone",
        "save",
    ]
    for parameter in parameters.values():
        assert parameter.kind is parameter.KEYWORD_ONLY
    assert parameters["hostname"].default is None
    assert parameters["domain_name"].default is None
    assert parameters["name_servers"].default is None
    assert parameters["search_domains"].default is None
    assert parameters["time_zone"].default is None
    assert parameters["save"].default is False


def test_interface_signature_is_positional_name_then_keyword_only() -> None:
    parameters = inspect.signature(interface).parameters

    assert list(parameters) == [
        "interface",
        "interface_type",
        "addresses",
        "description",
        "mtu",
        "disabled",
        "values",
        "present",
        "save",
    ]
    assert parameters["interface"].kind is parameters["interface"].POSITIONAL_OR_KEYWORD
    for name in (
        "interface_type",
        "addresses",
        "description",
        "mtu",
        "disabled",
        "values",
        "present",
        "save",
    ):
        assert parameters[name].kind is parameters[name].KEYWORD_ONLY
    assert parameters["interface"].default is inspect.Parameter.empty
    assert parameters["interface_type"].default is inspect.Parameter.empty
    assert parameters["addresses"].default is None
    assert parameters["description"].default is None
    assert parameters["mtu"].default is None
    assert parameters["disabled"].default is None
    assert parameters["values"].default is None
    assert parameters["present"].default is True
    assert parameters["save"].default is False


def test_system_basics_all_none_rejects_before_any_host_access() -> None:
    """Validation raises OperationValueError before host.get_fact is reached."""

    with pytest.raises(OperationValueError):
        list(system_basics._inner())


def test_interface_present_false_rejects_desired_args_before_any_host_access() -> None:
    """Validation raises OperationValueError before host.get_fact is reached.

    These run without pyinfra host context: reaching the fact lookup would
    raise a context error instead, so passing proves validation comes first.
    """

    with pytest.raises(OperationValueError):
        list(
            interface._inner(
                "dum0",
                interface_type="dummy",
                present=False,
                addresses=["192.0.2.1/32"],
            )
        )


def test_static_route_signature_is_positional_destination_then_keyword_only() -> None:
    parameters = inspect.signature(static_route).parameters

    assert list(parameters) == [
        "destination",
        "next_hops",
        "values",
        "present",
        "save",
    ]
    assert parameters["destination"].kind is parameters["destination"].POSITIONAL_OR_KEYWORD
    for name in ("next_hops", "values", "present", "save"):
        assert parameters[name].kind is parameters[name].KEYWORD_ONLY
    assert parameters["destination"].default is inspect.Parameter.empty
    assert parameters["next_hops"].default is None
    assert parameters["values"].default is None
    assert parameters["present"].default is True
    assert parameters["save"].default is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"destination": "203.0.113.0/24", "present": False, "next_hops": ["192.0.2.1"]},
        {"destination": "garbage", "next_hops": ["192.0.2.1"]},
        {"destination": "192.0.2.1/24", "next_hops": ["192.0.2.1"]},
        {"destination": "192.0.2.5", "next_hops": ["192.0.2.1"]},
    ],
)
def test_static_route_schema_independent_rejections_surface_before_any_host_access(
    kwargs: dict[str, Any],
) -> None:
    """Validation raises OperationValueError before host.get_fact is reached.

    These run without pyinfra host context: reaching the fact lookup would
    raise a context error instead, so passing proves validation comes first.
    """

    with pytest.raises(OperationValueError):
        list(static_route._inner(**kwargs))


def test_user_signature_is_positional_user_then_keyword_only() -> None:
    parameters = inspect.signature(user).parameters

    assert list(parameters) == [
        "user",
        "full_name",
        "encrypted_password",
        "ssh_keys",
        "present",
        "save",
    ]
    assert parameters["user"].kind is parameters["user"].POSITIONAL_OR_KEYWORD
    for name in ("full_name", "encrypted_password", "ssh_keys", "present", "save"):
        assert parameters[name].kind is parameters[name].KEYWORD_ONLY
    assert parameters["user"].default is inspect.Parameter.empty
    assert parameters["full_name"].default is None
    assert parameters["encrypted_password"].default is None
    assert parameters["ssh_keys"].default is None
    assert parameters["present"].default is True
    assert parameters["save"].default is False


def test_user_present_false_rejects_desired_args_before_any_host_access() -> None:
    """require_absent_args_unset runs before any fact read.

    These run without pyinfra host context: reaching the fact lookup would
    raise a context error instead, so passing proves validation comes first.

    Plaintext ``encrypted_password`` is renderer-layer (after Version), so it
    cannot be proven host-free here. Sibling ``tests/test_render.py`` covers
    hash-only rejection without echoing the value.
    """

    with pytest.raises(OperationValueError, match="full_name"):
        list(user._inner("alice", present=False, full_name="Alice"))


def test_firewall_group_signature_is_positional_group_and_type_then_keyword_only() -> None:
    parameters = inspect.signature(firewall_group).parameters

    assert list(parameters) == [
        "group",
        "group_type",
        "members",
        "description",
        "present",
        "save",
    ]
    assert parameters["group"].kind is parameters["group"].POSITIONAL_OR_KEYWORD
    assert parameters["group_type"].kind is parameters["group_type"].POSITIONAL_OR_KEYWORD
    for name in ("members", "description", "present", "save"):
        assert parameters[name].kind is parameters[name].KEYWORD_ONLY
    assert parameters["group"].default is inspect.Parameter.empty
    assert parameters["group_type"].default is inspect.Parameter.empty
    assert parameters["members"].default is None
    assert parameters["description"].default is None
    assert parameters["present"].default is True
    assert parameters["save"].default is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"group": "pyfw", "group_type": "address", "present": False, "members": ["192.0.2.10"]},
        {"group": "pyfw", "group_type": "address"},
    ],
)
def test_firewall_group_schema_independent_rejections_surface_before_any_host_access(
    kwargs: dict[str, Any],
) -> None:
    """Both hoists raise OperationValueError before host.get_fact is reached.

    present=False+members hits require_absent_args_unset; present=True with
    members omitted hits require_firewall_group_members. These run without
    pyinfra host context: reaching the fact lookup would raise a context
    error instead, so passing proves both checks precede Version.
    """

    with pytest.raises(OperationValueError):
        list(firewall_group._inner(**kwargs))


@pytest.mark.parametrize("identity", [None, "", "   ", "\n"])
def test_require_deletable_identity_fails_closed_when_unestablished(
    identity: object,
) -> None:
    with pytest.raises(OperationValueError, match="could not be established") as caught:
        _require_deletable_identity(identity, "alice")
    assert "config" in str(caught.value)


def test_require_deletable_identity_refuses_self_deletion() -> None:
    with pytest.raises(OperationValueError, match="self-deletion") as caught:
        _require_deletable_identity("alice", "alice")
    message = str(caught.value)
    assert "alice" in message
    assert "config" in message


def test_require_deletable_identity_strips_reported_identity() -> None:
    with pytest.raises(OperationValueError, match="self-deletion"):
        _require_deletable_identity("alice\n", "alice")


def test_require_deletable_identity_allows_a_different_user() -> None:
    _require_deletable_identity("alice", "bob")


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


# --- _plan_scopes ------------------------------------------------------------
#
# `_plan_scopes` is unit-tested against a stub host: @local cannot reach
# planner branches without a `vbash` Configuration fact (the plan allows a
# stub only for that gap).


_PLAN_TREE: dict[str, Any] = {
    "system": {
        "host-name": "r1",
        "time-zone": "UTC",
        "name-server": ["192.0.2.1"],
    },
    "service": {
        "ntp": {"server": {"time1.example.net": {}}},
    },
    "interfaces": {
        "dummy": {"dum0": {"address": ["192.0.2.1/32"]}},
    },
}


class _ConfigurationHost:
    """Minimal host exposing `get_fact` for `_plan_scopes` unit tests."""

    def __init__(self, tree: dict[str, Any]) -> None:
        self.tree = tree
        self.fact_calls = 0

    def get_fact(self, fact: object, *args: object, **kwargs: object) -> dict[str, Any]:
        self.fact_calls += 1
        assert fact is Configuration
        return self.tree


def test_plan_scopes_covers_absent_exact_and_merge() -> None:
    host = _ConfigurationHost(_PLAN_TREE)

    planned = _plan_scopes(
        host,
        [
            Scope(["service", "ntp"], Absent()),
            Scope(["system", "host-name"], Exact(["router"])),
            Scope(["interfaces", "dummy", "dum0"], Merge({"description": ["lab"]})),
        ],
    )

    assert planned == [
        PlannedCommand(["delete", "service", "ntp"]),
        PlannedCommand(["delete", "system", "host-name", "r1"]),
        PlannedCommand(["set", "system", "host-name", "router"]),
        PlannedCommand(["set", "interfaces", "dummy", "dum0", "description", "lab"]),
    ]


def test_static_route_exact_body_prunes_an_undeclared_next_hop() -> None:
    host = _ConfigurationHost(
        {
            "protocols": {
                "static": {
                    "route": {
                        "192.0.2.0/24": {
                            "next-hop": {
                                "203.0.113.1": {"distance": "50"},
                                "203.0.113.2": {},
                            }
                        }
                    }
                }
            }
        }
    )
    scopes = render_static_route("1.4", "192.0.2.0/24", next_hops=["203.0.113.1"])

    planned = _plan_scopes(host, scopes)

    route = ["protocols", "static", "route", "192.0.2.0/24", "next-hop"]
    assert planned == [
        PlannedCommand(["delete", *route, "203.0.113.1", "distance"]),
        PlannedCommand(["delete", *route, "203.0.113.2"]),
    ]


def test_firewall_group_exact_body_prunes_undeclared_member_and_description() -> None:
    """TOTAL-body: an omitted member and description are deleted, not unmanaged."""

    host = _ConfigurationHost(
        {
            "firewall": {
                "group": {
                    "address-group": {
                        "pyfw": {
                            "address": ["192.0.2.10", "192.0.2.11", "192.0.2.12"],
                            "description": "lab",
                        }
                    }
                }
            }
        }
    )
    scopes = render_firewall_group(
        "1.5",
        "pyfw",
        "address",
        members=["192.0.2.10", "192.0.2.11"],
    )

    planned = _plan_scopes(host, scopes)

    path = ["firewall", "group", "address-group", "pyfw"]
    assert planned == [
        PlannedCommand(["delete", *path, "address", "192.0.2.12"]),
        PlannedCommand(["delete", *path, "description"]),
    ]


def test_firewall_group_empty_body_noops_on_a_converged_empty_group() -> None:
    """Exact({}) is a bare presence set when absent and nothing when converged."""

    scopes = render_firewall_group("1.5", "pyfw", "address", members=[])
    converged = _ConfigurationHost({"firewall": {"group": {"address-group": {"pyfw": {}}}}})

    assert _plan_scopes(converged, scopes) is None
    assert _plan_scopes(_ConfigurationHost({}), scopes) == [
        PlannedCommand(["set", "firewall", "group", "address-group", "pyfw"])
    ]


def test_plan_scopes_preserves_scope_order_when_delete_follows_set() -> None:
    host = _ConfigurationHost(_PLAN_TREE)

    planned = _plan_scopes(
        host,
        [
            Scope(["system", "host-name"], Exact(["router"])),
            Scope(["service", "ntp"], Absent()),
        ],
    )

    assert planned is not None
    # A global deletes-then-sets flatten would hoist the Absent delete
    # ahead of Exact's set. Scope order must keep it after.
    assert [command.argv[0] for command in planned] == ["delete", "set", "delete"]
    assert planned[-1] == PlannedCommand(["delete", "service", "ntp"])


def test_plan_scopes_emits_deletes_before_sets_within_one_exact_scope() -> None:
    host = _ConfigurationHost(_PLAN_TREE)

    planned = _plan_scopes(
        host,
        [Scope(["system", "host-name"], Exact(["router"]))],
    )

    assert planned == [
        PlannedCommand(["delete", "system", "host-name", "r1"]),
        PlannedCommand(["set", "system", "host-name", "router"]),
    ]


def test_plan_scopes_inherits_sensitivity_onto_every_command() -> None:
    host = _ConfigurationHost(_PLAN_TREE)

    planned = _plan_scopes(
        host,
        [Scope(["system", "host-name"], Exact(["router"]), sensitive=True)],
    )

    assert planned == [
        PlannedCommand(["delete", "system", "host-name", "r1"], sensitive=True),
        PlannedCommand(["set", "system", "host-name", "router"], sensitive=True),
    ]


def test_plan_scopes_mixed_scopes_keep_per_scope_sensitivity() -> None:
    host = _ConfigurationHost(_PLAN_TREE)

    planned = _plan_scopes(
        host,
        [
            Scope(["system", "host-name"], Exact(["router"]), sensitive=True),
            Scope(["service", "ntp"], Absent()),
            Scope(
                ["interfaces", "dummy", "dum0"],
                Merge({"description": ["lab"]}),
                sensitive=True,
            ),
        ],
    )

    assert planned == [
        PlannedCommand(["delete", "system", "host-name", "r1"], sensitive=True),
        PlannedCommand(["set", "system", "host-name", "router"], sensitive=True),
        PlannedCommand(["delete", "service", "ntp"]),
        PlannedCommand(
            ["set", "interfaces", "dummy", "dum0", "description", "lab"],
            sensitive=True,
        ),
    ]


_USER_KEYS_TREE: dict[str, Any] = {
    "system": {
        "login": {
            "user": {
                "alice": {
                    "authentication": {
                        "public-keys": {
                            "laptop": {"type": "ssh-ed25519", "key": "AAAA"},
                            "stale": {"type": "ssh-rsa", "key": "BBBB"},
                        }
                    }
                }
            }
        }
    }
}
_USER_KEYS_PATH = ["system", "login", "user", "alice", "authentication", "public-keys"]


def test_user_ssh_keys_exact_set_removes_an_omitted_key() -> None:
    """An active key omitted from ssh_keys is deleted; declared keys stay."""

    host = _ConfigurationHost(_USER_KEYS_TREE)

    planned = _plan_scopes(
        host,
        render_user("1.5", "alice", ssh_keys={"laptop": {"type": "ssh-ed25519", "key": "AAAA"}}),
    )

    assert planned == [PlannedCommand(["delete", *_USER_KEYS_PATH, "stale"])]


def test_user_ssh_keys_empty_mapping_owns_and_empties_the_subtree() -> None:
    """ssh_keys={} clears the key set and noops once it is already empty."""

    planned = _plan_scopes(
        _ConfigurationHost(_USER_KEYS_TREE),
        render_user("1.5", "alice", ssh_keys={}),
    )

    assert planned == [PlannedCommand(["delete", *_USER_KEYS_PATH])]
    assert _plan_scopes(_ConfigurationHost({}), render_user("1.5", "alice", ssh_keys={})) is None


def test_plan_scopes_empty_delta_returns_none() -> None:
    host = _ConfigurationHost(_PLAN_TREE)

    assert (
        _plan_scopes(
            host,
            [
                Scope(["system", "host-name"], Exact(["r1"])),
                Scope(["protocols", "static"], Absent()),
            ],
        )
        is None
    )


def test_plan_scopes_merge_empty_on_existing_node_plans_nothing() -> None:
    host = _ConfigurationHost(_PLAN_TREE)

    assert _plan_scopes(host, [Scope(["system"], Merge({}))]) is None


def test_plan_scopes_merge_empty_on_absent_node_sets_presence() -> None:
    host = _ConfigurationHost(_PLAN_TREE)

    planned = _plan_scopes(host, [Scope(["protocols", "static"], Merge({}))])

    assert planned == [PlannedCommand(["set", "protocols", "static"])]


def test_plan_scopes_exact_empty_desired_on_leaf_never_bare_deletes() -> None:
    host = _ConfigurationHost(_PLAN_TREE)

    planned = _plan_scopes(host, [Scope(["system", "host-name"], Exact({}))])

    assert planned is None


def test_plan_scopes_fetches_configuration_once() -> None:
    host = _ConfigurationHost(_PLAN_TREE)

    _plan_scopes(
        host,
        [
            Scope(["service", "ntp"], Absent()),
            Scope(["system", "host-name"], Exact(["router"])),
            Scope(["interfaces", "dummy", "dum0"], Merge({"description": ["lab"]})),
            Scope(["protocols"], Merge({})),
        ],
    )

    assert host.fact_calls == 1
