from __future__ import annotations

import pytest
from pyinfra.api.exceptions import FactProcessError

from pyinfra_vyos.facts import GitConfig, GitVersion


def test_git_version_fact_runs_git_version() -> None:
    command = GitVersion().command()

    assert command.get_raw_value() == "git --version"
    assert command.get_masked_value() == command.get_raw_value()


def test_git_version_fact_requires_the_git_binary() -> None:
    """Hosts without git yield ``default()`` rather than failing the run."""

    assert GitVersion().requires_command() == "git"
    assert GitConfig().requires_command() == "git"


def test_git_version_fact_strips_the_banner() -> None:
    assert GitVersion().process(["git version 2.51.2"]) == "2.51.2"


def test_git_version_fact_ignores_vendor_suffixes() -> None:
    """Apple's git appends a build tag: ``git version 2.39.5 (Apple Git-154)``."""

    assert GitVersion().process(["git version 2.39.5 (Apple Git-154)"]) == "2.39.5"


def test_git_config_fact_lists_only_repository_local_configuration() -> None:
    command = GitConfig().command(path="/srv/my repo")

    assert command.get_raw_value() == "git -C '/srv/my repo' config --local --list --null"


def test_git_config_fact_defaults_to_the_working_directory() -> None:
    assert GitConfig().command().get_raw_value() == "git -C . config --local --list --null"


def test_git_config_fact_parses_null_delimited_entries() -> None:
    output = ["core.bare\nfalse\0user.name\nAda Lovelace\0"]

    assert GitConfig().process(output) == {"core.bare": "false", "user.name": "Ada Lovelace"}


def test_git_config_fact_rejoins_multi_line_output() -> None:
    """pyinfra splits stdout on newlines; config values may contain newlines.

    The raw bytes ``demo.multi\\nline1\\nline2\\0`` reach ``process()`` as three
    list entries, and only rejoining them recovers the real value.
    """

    output = ["user.name\nAda", "Lovelace\0demo.multi\nline1", "line2\0"]

    assert GitConfig().process(output) == {
        "user.name": "Ada\nLovelace",
        "demo.multi": "line1\nline2",
    }


def test_git_config_fact_keeps_the_last_value_of_a_multi_valued_key() -> None:
    output = ["remote.origin.url\nfirst\0remote.origin.url\nsecond\0"]

    assert GitConfig().process(output) == {"remote.origin.url": "second"}


def test_git_config_fact_defaults_are_empty() -> None:
    assert GitVersion().default() == ""
    assert GitConfig().default() == {}
    assert GitConfig().process([""]) == {}


def test_fact_processing_failures_raise_fact_process_error() -> None:
    """Processing failures must fail only the affected host, not the run.

    pyinfra contains only ``FactProcessError`` around ``fact.process()``;
    anything else escaping would abort the entire multi-host deploy.
    """

    with pytest.raises(FactProcessError):
        GitVersion().process(["gti version 2.51.2"])
    with pytest.raises(FactProcessError, match="has no value"):
        GitConfig().process(["core.bare\0"])
    with pytest.raises(FactProcessError, match="has no key"):
        GitConfig().process(["\nfalse\0"])


def test_git_config_parse_errors_name_the_repository() -> None:
    fact = GitConfig()
    fact.command(path="/srv/repo")

    with pytest.raises(FactProcessError, match="/srv/repo"):
        fact.process(["core.bare\0"])
