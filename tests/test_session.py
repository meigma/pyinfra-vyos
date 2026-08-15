from __future__ import annotations

import inspect
import re

import pytest

from pyinfra_vyos._session import (
    SENTINEL_CHANGED,
    SENTINEL_NOOP,
    build_load_script,
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


def _script(*, save: bool) -> str:
    return build_load_script(_STAGING, save=save)


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


@pytest.mark.parametrize("save", [False, True])
def test_pager_export_precedes_source_line(save: bool) -> None:
    script = _script(save=save)

    assert script.index("export VYATTA_PAGER=cat") < script.index(_SOURCE)


@pytest.mark.parametrize("save", [False, True])
def test_trap_disarms_exit_inside_the_trap_body(save: bool) -> None:
    script = _script(save=save)
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
def test_every_termination_is_builtin_exit_never_the_aliases(save: bool) -> None:
    script = _script(save=save)

    assert "builtin exit" in script
    assert _BARE_EXIT_OR_DISCARD.search(script) is None
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        command = stripped.split(None, 1)[0]
        assert command not in {"exit", "discard"}


@pytest.mark.parametrize("save", [False, True])
def test_session_changed_tri_state_is_present_on_both_calls(save: bool) -> None:
    script = _script(save=save)

    assert script.count(_SESSION_CHANGED) == 2
    blocks = _tri_state_blocks(script)
    assert len(blocks) == 2
    for block in blocks:
        assert "-eq 0" in block
        assert "-eq 1" in block
        assert "else" in block
        assert "builtin exit 1" in block


def test_save_block_present_iff_save_true() -> None:
    with_save = _script(save=True)
    without_save = _script(save=False)

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
def test_sentinel_strings_equal_the_constants(save: bool) -> None:
    script = _script(save=save)

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
