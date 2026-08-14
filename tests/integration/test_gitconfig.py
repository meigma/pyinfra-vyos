from __future__ import annotations

from pathlib import Path

import pytest
from pyinfra.api.exceptions import OperationValueError
from pyinfra.api.operations import run_ops

from pyinfra_vyos import GitConfig, GitVersion, config_entry

from ._helpers import apply, config_value, fact_value, init_repository, prepare, run_git

pytestmark = pytest.mark.integration


@pytest.fixture
def repository(tmp_path: Path) -> str:
    """A throwaway repository, so the runner's own config is never touched."""

    return init_repository(str(tmp_path / "repo"))


def test_git_version_fact_reads_the_installed_git(repository: str) -> None:
    version = fact_value(GitVersion)

    assert version
    assert run_git("--version").startswith(f"git version {version}")


def test_git_config_fact_reads_real_repository_configuration(repository: str) -> None:
    run_git("-C", repository, "config", "--local", "demo.marker", "set by git")

    entries = fact_value(GitConfig, path=repository)

    assert entries["demo.marker"] == "set by git"
    assert entries["core.repositoryformatversion"] == "0"
    assert not any(key.startswith("user.") for key in entries), (
        "the local config of a fresh repository must not inherit global keys"
    )


def test_config_entry_sets_updates_and_unsets_a_key(repository: str) -> None:
    state, meta = prepare(config_entry, key="user.name", value="Ada Lovelace", path=repository)
    assert meta.will_change
    assert config_value(repository, "user.name") is None

    run_ops(state)
    assert meta.did_change()
    assert config_value(repository, "user.name") == "Ada Lovelace"

    noop = apply(config_entry, key="user.name", value="Ada Lovelace", path=repository)
    assert not noop.will_change
    assert not noop.did_change()
    assert config_value(repository, "user.name") == "Ada Lovelace"

    updated = apply(config_entry, key="user.name", value="Grace Hopper", path=repository)
    assert updated.did_change()
    assert config_value(repository, "user.name") == "Grace Hopper"

    removed = apply(config_entry, key="user.name", present=False, path=repository)
    assert removed.did_change()
    assert config_value(repository, "user.name") is None

    already_absent = apply(config_entry, key="user.name", present=False, path=repository)
    assert not already_absent.will_change
    assert not already_absent.did_change()


def test_config_entry_round_trips_values_git_would_mangle(repository: str) -> None:
    """A value with spaces and a newline survives quoting and ``--null`` parsing."""

    value = "line one\nline two"
    assert apply(config_entry, key="demo.multi", value=value, path=repository).did_change()

    assert config_value(repository, "demo.multi") == value
    assert fact_value(GitConfig, path=repository)["demo.multi"] == value
    assert not apply(config_entry, key="demo.multi", value=value, path=repository).will_change


def test_config_entry_rejects_bad_arguments_before_touching_the_repository(
    repository: str,
) -> None:
    with pytest.raises(OperationValueError, match="invalid git config key"):
        prepare(config_entry, key="user", value="Ada", path=repository)
    with pytest.raises(OperationValueError, match="value is required"):
        prepare(config_entry, key="user.name", path=repository)
    with pytest.raises(OperationValueError, match="invalid repository path"):
        prepare(config_entry, key="user.name", value="Ada", path="--global")

    assert config_value(repository, "user.name") is None
