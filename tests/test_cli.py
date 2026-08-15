from __future__ import annotations

import inspect

from pyinfra.api.arguments import all_argument_meta

import pyinfra_vyos
from pyinfra_vyos import operations
from pyinfra_vyos._cli import sg_probe, sg_vbash_run, vyos_op_command


def test_vyos_op_command_wraps_argv_in_one_run_and_chains_the_marker() -> None:
    command = vyos_op_command("show", "version", marker="PYINFRA_VYOS")
    rendered = command.get_raw_value()

    assert rendered == (
        "vbash -c 'export VYATTA_PAGER=cat\n"
        "source /opt/vyatta/etc/functions/script-template\n"
        "run show version && printf '\\''\\n%s\\n'\\'' PYINFRA_VYOS'"
    )
    assert "\nsource /opt/vyatta/etc/functions/script-template\nrun " in rendered
    assert rendered.count("\nrun ") == 1


def test_vyos_op_command_renders_op_pipe_tokens_literally() -> None:
    """``\\|`` ``strip-private`` is a VyOS op pipe, not a shell pipeline."""

    command = vyos_op_command(
        "show",
        "configuration",
        "commands",
        r"\|",
        "strip-private",
        marker="PYINFRA_VYOS",
    )

    rendered = command.get_raw_value()
    assert rendered == (
        "vbash -c 'export VYATTA_PAGER=cat\n"
        "source /opt/vyatta/etc/functions/script-template\n"
        "run show configuration commands \\| strip-private && "
        "printf '\\''\\n%s\\n'\\'' PYINFRA_VYOS'"
    )
    assert "\nsource /opt/vyatta/etc/functions/script-template\nrun " in rendered


def test_sg_probe_closes_stdin_and_checks_the_substrate() -> None:
    command = sg_probe()

    assert command.get_raw_value() == (
        "sg vyattacfg -c 'test -x /bin/vbash && "
        "test -r /opt/vyatta/etc/functions/script-template' </dev/null"
    )


def test_sg_vbash_run_quotes_a_staging_path_containing_a_space() -> None:
    command = sg_vbash_run("/tmp/my staging/session.sh", "/tmp/my staging")

    assert command.get_raw_value() == (
        "sg vyattacfg -c \"/bin/vbash '/tmp/my staging/session.sh'\" "
        "</dev/null; rc=$?; rm -rf '/tmp/my staging'; exit $rc"
    )


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
