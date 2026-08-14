from __future__ import annotations

import inspect

import pytest
from pyinfra.api.arguments import all_argument_meta

import pyinfra_vyos
from pyinfra_vyos import operations
from pyinfra_vyos._cli import CommandError, QuoteString, git_command


def test_git_command_quotes_user_values() -> None:
    command = git_command("config", "--local", "--replace-all", QuoteString("user.name"))

    assert str(command) == "git config --local --replace-all user.name"


def test_git_command_quotes_values_containing_spaces() -> None:
    command = git_command(
        "config",
        "--local",
        "--replace-all",
        QuoteString("user.name"),
        QuoteString("Ada Lovelace"),
    )

    assert command.get_raw_value() == "git config --local --replace-all user.name 'Ada Lovelace'"


def test_git_command_scopes_to_a_repository_before_the_subcommand() -> None:
    """``-C`` is a top-level git option and is ignored after the subcommand."""

    command = git_command("config", "--local", "--list", "--null", path="/srv/my repo")

    assert command.get_raw_value() == "git -C '/srv/my repo' config --local --list --null"


@pytest.mark.parametrize("value", ["--global", "--system", "-e", "-"])
def test_git_command_rejects_option_lookalike_values(value: str) -> None:
    """Shell quoting does not stop git from parsing a quoted token as an option.

    ``git config --local user.name '--global'`` re-scopes the command rather
    than setting a value, so every user-supplied argv value beginning with
    ``-`` is rejected before a command is built.
    """

    with pytest.raises(CommandError, match="command-line option"):
        git_command("config", "--local", QuoteString(value))
    with pytest.raises(CommandError, match="command-line option"):
        git_command("config", "--local", "--list", path=value)


def test_git_command_keeps_package_chosen_flags() -> None:
    command = git_command("config", "--local", "--unset-all", QuoteString("user.name"))

    assert command.get_raw_value() == "git config --local --unset-all user.name"


def test_git_command_passes_stdin_through_to_pyinfra() -> None:
    """Values that must stay off the process table travel on standard input."""

    command = git_command("hash-object", "-w", "--stdin", stdin="payload")

    assert command.get_raw_value() == "git hash-object -w --stdin"
    assert command.connector_arguments["_stdin"] == "payload"


def test_git_command_masks_nothing_by_default() -> None:
    command = git_command("--version")

    assert command.get_masked_value() == command.get_raw_value() == "git --version"


def test_no_operation_parameter_collides_with_pyinfra_reserved_arguments() -> None:
    """pyinfra consumes its global arguments before an operation runs.

    A parameter sharing a reserved keyword (``name``, ``_sudo``, ...) would
    silently never receive caller values, so the entry key of
    ``config_entry`` is ``key`` rather than the more natural ``name``. This
    test guards every operation the package exports, not just that one.
    """

    reserved = set(all_argument_meta)
    assert "name" in reserved
    checked = 0
    for exported in pyinfra_vyos.__all__:
        function = getattr(operations, exported, None)
        if function is None or not callable(function):
            continue
        checked += 1
        collisions = set(inspect.signature(function).parameters) & reserved
        assert not collisions, f"{exported} parameters shadow pyinfra arguments: {collisions}"
    assert checked >= 1
