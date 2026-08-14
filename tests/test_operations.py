from __future__ import annotations

import inspect

import pytest
from pyinfra.api.exceptions import OperationValueError

import pyinfra_vyos
from pyinfra_vyos._cli import CommandError, QuoteString, git_command
from pyinfra_vyos._gitconfig import GitConfigError, validate_config_key
from pyinfra_vyos.operations import _guarded, config_entry


def test_guarded_returns_the_wrapped_result() -> None:
    command = _guarded(git_command, "config", "--local", QuoteString("user.name"))

    assert command.get_raw_value() == "git config --local user.name"


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (validate_config_key, ("user",)),
        (git_command, ("config", QuoteString("--global"))),
    ],
)
def test_guarded_converts_domain_errors_to_operation_value_error(
    function: object,
    args: tuple[object, ...],
) -> None:
    """pyinfra only presents ``OperationValueError`` as a rejected argument."""

    assert issubclass(GitConfigError, Exception)
    assert issubclass(CommandError, Exception)
    with pytest.raises(OperationValueError):
        _guarded(function, *args)  # type: ignore[arg-type]


def test_guarded_does_not_swallow_unrelated_errors() -> None:
    def explode() -> None:
        raise ZeroDivisionError("boom")

    with pytest.raises(ZeroDivisionError):
        _guarded(explode)


def test_config_entry_signature_keeps_state_flags_keyword_only() -> None:
    """``present`` and ``path`` are intent, not positional data."""

    parameters = inspect.signature(config_entry).parameters

    assert [name for name, p in parameters.items() if p.kind is p.KEYWORD_ONLY] == [
        "present",
        "path",
    ]
    assert parameters["value"].default is None
    assert parameters["present"].default is True
    assert parameters["path"].default == "."


def test_package_exports_the_sample_primitives() -> None:
    assert pyinfra_vyos.__all__ == ["GitConfig", "GitVersion", "config_entry"]
    for exported in pyinfra_vyos.__all__:
        assert getattr(pyinfra_vyos, exported) is not None
