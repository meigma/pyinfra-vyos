from __future__ import annotations

import pytest

from pyinfra_vyos._gitconfig import (
    GitConfigError,
    config_changes,
    config_commands,
    parse_config_list,
    set_config_entry,
    unset_config_entry,
    validate_config_key,
    validate_repository_path,
)


def test_parse_config_list_reads_null_delimited_records() -> None:
    payload = "core.bare\nfalse\0user.email\nada@example.com\0"

    assert parse_config_list(payload) == {
        "core.bare": "false",
        "user.email": "ada@example.com",
    }


def test_parse_config_list_preserves_newlines_inside_values() -> None:
    assert parse_config_list("demo.multi\nline1\nline2\0") == {"demo.multi": "line1\nline2"}


def test_parse_config_list_accepts_empty_values_and_empty_output() -> None:
    assert parse_config_list("user.name\n\0") == {"user.name": ""}
    assert parse_config_list("") == {}


def test_parse_config_list_rejects_valueless_keys() -> None:
    with pytest.raises(GitConfigError, match="has no value"):
        parse_config_list("core.bare\0", source="/srv/repo")


@pytest.mark.parametrize(
    "key",
    ["user", "user.", ".name", "user.1name", "user name"],
)
def test_validate_config_key_rejects_malformed_keys(key: str) -> None:
    with pytest.raises(GitConfigError, match="invalid git config key"):
        validate_config_key(key)


@pytest.mark.parametrize(
    "key",
    ["user.name", "remote.origin.url", "branch.my feature.remote", "url.https://x/.insteadOf"],
)
def test_validate_config_key_accepts_sections_and_subsections(key: str) -> None:
    validate_config_key(key)


def test_validate_repository_path_rejects_empty_and_option_lookalikes() -> None:
    """Rejected here so the operation fails before fact collection reaches git."""

    validate_repository_path("/srv/repo")
    with pytest.raises(GitConfigError, match="must not be empty"):
        validate_repository_path("")
    with pytest.raises(GitConfigError, match="invalid repository path"):
        validate_repository_path("--global")


def test_config_changes_reports_only_real_differences() -> None:
    current = {"user.name": "Ada", "core.bare": "false"}

    assert config_changes(current, {"user.name": "Ada"}) == {}
    assert config_changes(current, {"user.name": "Grace"}) == {"user.name": "Grace"}
    assert config_changes(current, {"user.email": "ada@example.com"}) == {
        "user.email": "ada@example.com",
    }


def test_config_changes_unsets_only_present_keys() -> None:
    current = {"user.name": "Ada"}

    assert config_changes(current, {"user.name": None}) == {"user.name": None}
    assert config_changes(current, {"user.email": None}) == {}


def test_config_changes_are_ordered_deterministically() -> None:
    changes = config_changes({}, {"user.name": "Ada", "core.bare": "true"})

    assert list(changes) == ["core.bare", "user.name"]


def test_set_config_entry_replaces_all_values() -> None:
    command = set_config_entry(path="/srv/repo", key="user.name", value="Ada Lovelace")

    assert command.get_raw_value() == (
        "git -C /srv/repo config --local --replace-all user.name 'Ada Lovelace'"
    )


def test_unset_config_entry_removes_all_values() -> None:
    command = unset_config_entry(path="/srv/repo", key="user.name")

    assert command.get_raw_value() == "git -C /srv/repo config --local --unset-all user.name"


def test_command_builders_validate_the_key() -> None:
    with pytest.raises(GitConfigError, match="invalid git config key"):
        set_config_entry(path="/srv/repo", key="user", value="Ada")
    with pytest.raises(GitConfigError, match="invalid git config key"):
        unset_config_entry(path="/srv/repo", key="user")


def test_config_commands_renders_sets_and_unsets_together() -> None:
    commands = config_commands(
        path="/srv/repo",
        changes={"user.email": None, "user.name": "Ada"},
    )

    assert [command.get_raw_value() for command in commands] == [
        "git -C /srv/repo config --local --unset-all user.email",
        "git -C /srv/repo config --local --replace-all user.name Ada",
    ]


def test_config_commands_is_empty_without_changes() -> None:
    assert config_commands(path="/srv/repo", changes={}) == []
