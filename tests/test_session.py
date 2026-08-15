from __future__ import annotations

import inspect
import re
import shlex

import pytest

from pyinfra_vyos._session import (
    SENTINEL_CHANGED,
    SENTINEL_NOOP,
    PlannedCommand,
    build_commands_script,
    build_load_script,
    build_save_script,
    staging_dir,
)

_STAGING = "/tmp/pyinfra-vyos-" + "ab" * 16
_SOURCE = "source /opt/vyatta/etc/functions/script-template"
_SESSION_CHANGED = "/bin/cli-shell-api sessionChanged"
_NEEDS_SAVE = (
    "/bin/cli-shell-api --show-cfg1 @ACTIVE "
    "--show-cfg2 /config/config.boot --show-commands showConfig"
)
_BARE_EXIT_OR_DISCARD = re.compile(r"(?<!builtin )\b(exit|discard)\b")
_STAGING_PATTERN = re.compile(r"^/tmp/pyinfra-vyos-[0-9a-f]{32}$")
_COMMIT_GATE = 'if [ "$did_commit" -ne 0 ]; then'
_SUPPRESSED_OUTPUT = "device output suppressed (sensitive command)"
_COMMANDS = [
    PlannedCommand(["delete", "service", "ntp", "server", "old.test"]),
    PlannedCommand(["set", "service", "ntp", "server", "new.test"]),
]
_BUILDERS = ["load", "commands", "save"]
_COMMIT_BUILDERS = ["load", "commands"]


def _script(*, save: bool, kind: str = "load") -> str:
    if kind == "load":
        return build_load_script(_STAGING, save=save)
    if kind == "commands":
        return build_commands_script(_STAGING, _COMMANDS, save=save)
    return build_save_script(_STAGING)


def _tri_state_blocks(script: str) -> list[str]:
    blocks: list[str] = []
    start = 0
    while True:
        idx = script.find(_SESSION_CHANGED, start)
        if idx == -1:
            break
        after = script[idx + len(_SESSION_CHANGED) :]
        fi = after.find("\nfi")
        assert fi != -1
        blocks.append(after[: fi + len("\nfi")])
        start = idx + len(_SESSION_CHANGED)
    return blocks


def _failure_branch(script: str, index: int) -> str:
    marker = f"config command {index} ("
    start = script.index(marker)
    end = script.index("builtin exit 1", start)
    return script[start:end]


@pytest.mark.parametrize("save", [False, True])
@pytest.mark.parametrize("kind", _BUILDERS)
def test_pager_export_precedes_source_line(save: bool, kind: str) -> None:
    script = _script(save=save, kind=kind)

    assert script.index("export VYATTA_PAGER=cat") < script.index(_SOURCE)


@pytest.mark.parametrize("save", [False, True])
@pytest.mark.parametrize("kind", _BUILDERS)
def test_trap_disarms_exit_inside_the_trap_body(save: bool, kind: str) -> None:
    script = _script(save=save, kind=kind)
    body_start = script.index("_pyinfra_vyos_cleanup() {")
    body_end = script.index("\n}", body_start)
    body = script[body_start:body_end]

    assert "trap - EXIT" in body
    assert body.index("rc=$?") < body.index("trap - EXIT")
    assert body.index("trap - EXIT") < body.index("teardownSession")
    assert body.index("trap - EXIT") < body.index('builtin exit "$rc"')

    disarm_lines = [line for line in script.splitlines() if line.strip() == "trap - EXIT"]
    assert disarm_lines
    assert all(line.startswith(" ") for line in disarm_lines)


@pytest.mark.parametrize("save", [False, True])
@pytest.mark.parametrize("kind", _BUILDERS)
def test_every_termination_is_builtin_exit_never_the_aliases(save: bool, kind: str) -> None:
    script = _script(save=save, kind=kind)

    assert "builtin exit" in script
    assert _BARE_EXIT_OR_DISCARD.search(script) is None
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        command = stripped.split(None, 1)[0]
        assert command not in {"exit", "discard"}


@pytest.mark.parametrize("save", [False, True])
@pytest.mark.parametrize("kind", _COMMIT_BUILDERS)
def test_session_changed_tri_state_is_present_on_both_calls(save: bool, kind: str) -> None:
    script = _script(save=save, kind=kind)

    assert script.count(_SESSION_CHANGED) == 2
    blocks = _tri_state_blocks(script)
    assert len(blocks) == 2
    for block in blocks:
        assert "-eq 0" in block
        assert "-eq 1" in block
        assert "else" in block
        assert "builtin exit 1" in block


@pytest.mark.parametrize("kind", _COMMIT_BUILDERS)
def test_save_block_present_iff_save_true(kind: str) -> None:
    with_save = _script(save=True, kind=kind)
    without_save = _script(save=False, kind=kind)

    assert _NEEDS_SAVE in with_save
    assert with_save.count(_NEEDS_SAVE) == 2
    assert "$(save)" in with_save

    assert _NEEDS_SAVE not in without_save
    assert "$(save)" not in without_save


def test_save_block_is_reachable_on_the_noop_path() -> None:
    script = _script(save=True)
    first = script.index(_SESSION_CHANGED)
    second = script.index(_SESSION_CHANGED, first + 1)
    save_at = script.index(_NEEDS_SAVE)

    assert save_at > second
    save_line = next(line for line in script.splitlines() if _NEEDS_SAVE in line)
    assert not save_line.startswith(" ")

    noop_gate = script[first:save_at]
    assert 'elif [ "$_changed_rc" -eq 1 ]; then' in noop_gate
    assert "builtin exit 0" not in noop_gate


@pytest.mark.parametrize("save", [False, True])
@pytest.mark.parametrize("kind", _BUILDERS)
def test_sentinel_strings_equal_the_constants(save: bool, kind: str) -> None:
    script = _script(save=save, kind=kind)

    assert SENTINEL_CHANGED == "PYINFRA_VYOS changed"
    assert SENTINEL_NOOP == "PYINFRA_VYOS noop"
    assert SENTINEL_CHANGED in script
    assert SENTINEL_NOOP in script


@pytest.mark.parametrize("save", [False, True])
def test_staging_path_is_interpolated(save: bool) -> None:
    script = _script(save=save)

    assert _STAGING in script
    assert f"{_STAGING}/config" in script
    assert f"rm -rf {_STAGING}" in script
    assert f"load {_STAGING}/config" in script


def test_staging_dir_returns_unique_matching_paths() -> None:
    paths = [staging_dir() for _ in range(32)]

    assert len(set(paths)) == 32
    assert all(_STAGING_PATTERN.fullmatch(path) for path in paths)


def test_staging_dir_takes_no_content_argument() -> None:
    assert inspect.signature(staging_dir).parameters == {}
    assert staging_dir.__code__.co_argcount == 0


# --- build_commands_script ---------------------------------------------------


def test_commands_script_rejects_empty_and_unknown_verbs() -> None:
    with pytest.raises(ValueError, match="nonempty"):
        build_commands_script(_STAGING, [], save=False)
    with pytest.raises(ValueError, match="unsupported"):
        build_commands_script(_STAGING, [PlannedCommand(["run", "reboot"])], save=False)
    with pytest.raises(ValueError, match="unsupported"):
        build_commands_script(_STAGING, [PlannedCommand([])], save=False)


def test_commands_are_rendered_in_order_between_session_entry_and_commit_gate() -> None:
    script = _script(save=False, kind="commands")
    in_session = script.index("if ! /bin/cli-shell-api inSession")
    first_gate = script.index(_SESSION_CHANGED)
    delete_at = script.index("delete service ntp server old.test")
    set_at = script.index("set service ntp server new.test")

    assert in_session < delete_at < set_at < first_gate


def test_command_tokens_are_shell_quoted() -> None:
    tricky = "hi there 'quoted'"
    script = build_commands_script(
        _STAGING,
        [PlannedCommand(["set", "system", "login", "banner", "pre-login", tricky])],
        save=False,
    )

    expected = " ".join(
        ["set", *(shlex.quote(t) for t in ["system", "login", "banner", "pre-login", tricky])]
    )
    assert f"_cmd_out=$({expected})" in script
    assert tricky not in script.replace(shlex.quote(tricky), "")


def test_every_command_rc_is_checked() -> None:
    script = _script(save=False, kind="commands")

    assert script.count("_cmd_rc=$?") == len(_COMMANDS)
    assert script.count('if [ "$_cmd_rc" -ne 0 ]; then') == len(_COMMANDS)


def test_failure_diagnostic_never_contains_command_values() -> None:
    secret = "s3cret-value"
    script = build_commands_script(
        _STAGING,
        [
            PlannedCommand(
                [
                    "set",
                    "system",
                    "login",
                    "user",
                    "x",
                    "authentication",
                    "plaintext-password",
                    secret,
                ]
            )
        ],
        save=False,
    )

    failure_lines = [line for line in script.splitlines() if ">&2" in line]
    assert failure_lines
    assert all(secret not in line for line in failure_lines)
    assert any("config command 1 (set) failed" in line for line in failure_lines)


def test_commands_script_save_block_is_gated_on_did_commit() -> None:
    with_save = _script(save=True, kind="commands")
    without_save = _script(save=False, kind="commands")

    assert _COMMIT_GATE in with_save
    assert with_save.index(_COMMIT_GATE) < with_save.index(_NEEDS_SAVE)
    assert with_save.index(_COMMIT_GATE) < with_save.index("$(save)")
    assert _NEEDS_SAVE not in without_save
    assert "$(save)" not in without_save


def test_load_script_save_block_is_ungated() -> None:
    script = build_load_script(_STAGING, save=True)

    assert _COMMIT_GATE not in script
    save_line = next(line for line in script.splitlines() if _NEEDS_SAVE in line)
    assert not save_line.startswith(" ")
    assert _NEEDS_SAVE in script
    assert "$(save)" in script


def test_sensitive_failure_suppresses_captured_output() -> None:
    secret = "s3cret-value"
    script = build_commands_script(
        _STAGING,
        [
            PlannedCommand(
                [
                    "set",
                    "system",
                    "login",
                    "user",
                    "x",
                    "authentication",
                    "plaintext-password",
                    secret,
                ],
                sensitive=True,
            ),
            PlannedCommand(["set", "service", "ntp", "server", "new.test"]),
        ],
        save=False,
    )

    branch = _failure_branch(script, 1)
    assert _SUPPRESSED_OUTPUT in branch
    assert '"$_cmd_out"' not in branch
    assert any("config command 1 (set) failed" in line for line in branch.splitlines())

    capture_lines = [line for line in script.splitlines() if line.startswith("_cmd_out=$(")]
    sensitive_captures = [line for line in capture_lines if secret in line]
    nonsensitive_captures = [line for line in capture_lines if secret not in line]
    assert len(sensitive_captures) == 1
    assert nonsensitive_captures
    assert "2>&1" in sensitive_captures[0]
    assert all("2>&1" not in line for line in nonsensitive_captures)

    stderr_bound = [line for line in script.splitlines() if ">&2" in line]
    assert stderr_bound
    assert all(secret not in line for line in stderr_bound)

    stderr_or_printf = [line for line in script.splitlines() if ">&2" in line or "printf" in line]
    assert all(secret not in line for line in stderr_or_printf)


def test_nonsensitive_failure_prints_captured_output() -> None:
    script = build_commands_script(
        _STAGING,
        [PlannedCommand(["set", "service", "ntp", "server", "new.test"])],
        save=False,
    )

    branch = _failure_branch(script, 1)
    assert '"$_cmd_out"' in branch
    assert _SUPPRESSED_OUTPUT not in branch
    assert any("config command 1 (set) failed" in line for line in branch.splitlines())


# --- build_save_script -------------------------------------------------------


def test_save_script_load_bearing_lines() -> None:
    script = build_save_script(_STAGING)

    assert script.index("export VYATTA_PAGER=cat") < script.index(_SOURCE)
    assert "trap _pyinfra_vyos_cleanup EXIT" in script
    assert "if ! /bin/cli-shell-api inSession; then" in script
    assert _NEEDS_SAVE in script
    assert "$(save)" in script
    assert "$(commit)" not in script
    assert _SESSION_CHANGED not in script
    assert "_cmd_out=" not in script
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        command = stripped.split(None, 1)[0]
        assert command not in {"set", "delete", "commit"}
    assert SENTINEL_CHANGED in script
    assert SENTINEL_NOOP in script
    assert SENTINEL_CHANGED == "PYINFRA_VYOS changed"
    assert SENTINEL_NOOP == "PYINFRA_VYOS noop"
    assert "builtin exit" in script
    assert _BARE_EXIT_OR_DISCARD.search(script) is None
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        command = stripped.split(None, 1)[0]
        assert command not in {"exit", "discard"}
    assert f"rm -rf {_STAGING}" in script
