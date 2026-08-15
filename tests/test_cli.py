from __future__ import annotations

import inspect
from io import StringIO

from pyinfra.api import FileUploadCommand, StringCommand
from pyinfra.api.arguments import all_argument_meta

import pyinfra_vyos
from pyinfra_vyos import operations
from pyinfra_vyos._cli import (
    pending_save_probe,
    session_run_sequence,
    sg_probe,
    sg_vbash_run,
    vyos_op_command,
)
from pyinfra_vyos._session import NEEDS_SAVE_COMMAND


def test_vyos_op_command_wraps_argv_in_one_run_and_chains_the_marker() -> None:
    command = vyos_op_command("show", "version", marker="PYINFRA_VYOS")
    rendered = command.get_raw_value()

    assert rendered == (
        "vbash -c 'set -o pipefail\n"
        "export VYATTA_PAGER=cat\n"
        "source /opt/vyatta/etc/functions/script-template\n"
        "run show version && printf '\\''\\n%s\\n'\\'' PYINFRA_VYOS'"
    )
    assert "\nsource /opt/vyatta/etc/functions/script-template\nrun " in rendered
    assert rendered.count("\nrun ") == 1


def test_vyos_op_command_strip_private_is_a_real_shell_pipeline() -> None:
    """The interactive op pipe is unavailable to non-interactive ``run``;
    redaction pipes through the target's strip-private filter script, with
    pipefail preserving the op-mode command's failure."""

    command = vyos_op_command(
        "show",
        "configuration",
        "commands",
        marker="PYINFRA_VYOS",
        strip_private=True,
    )

    rendered = command.get_raw_value()
    assert rendered == (
        "vbash -c 'set -o pipefail\n"
        "export VYATTA_PAGER=cat\n"
        "source /opt/vyatta/etc/functions/script-template\n"
        "run show configuration commands | /usr/libexec/vyos/strip-private.py && "
        "printf '\\''\\n%s\\n'\\'' PYINFRA_VYOS'"
    )
    assert "\nsource /opt/vyatta/etc/functions/script-template\nrun " in rendered
    assert "set -o pipefail" in rendered


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


def test_session_run_sequence_is_five_commands_in_order() -> None:
    staging = "/tmp/pyinfra-vyos-test"
    script_text = "configure\ncommit\n"
    commands = session_run_sequence(staging, script_text)

    assert len(commands) == 5
    probe, mkdir, upload, chmod, vbash = commands

    assert isinstance(probe, StringCommand)
    assert isinstance(mkdir, StringCommand)
    assert isinstance(upload, FileUploadCommand)
    assert isinstance(chmod, StringCommand)
    assert isinstance(vbash, StringCommand)

    assert probe.get_raw_value() == sg_probe().get_raw_value()
    assert mkdir.get_raw_value() == "mkdir -m 700 /tmp/pyinfra-vyos-test"
    assert chmod.get_raw_value() == "chmod 600 /tmp/pyinfra-vyos-test/session.sh"
    assert vbash.get_raw_value() == sg_vbash_run(f"{staging}/session.sh", staging).get_raw_value()

    assert isinstance(upload.src, StringIO)
    assert upload.src.getvalue() == script_text
    assert upload.dest.endswith("/session.sh")
    assert chmod.get_raw_value().endswith("/session.sh")
    assert vbash.get_raw_value().index("/session.sh") != -1


def test_session_run_sequence_quotes_a_staging_path_containing_a_space() -> None:
    staging = "/tmp/my staging"
    script_text = "configure\n"
    commands = session_run_sequence(staging, script_text)
    _, mkdir, upload, chmod, vbash = commands

    assert mkdir.get_raw_value() == "mkdir -m 700 '/tmp/my staging'"
    assert chmod.get_raw_value() == "chmod 600 '/tmp/my staging/session.sh'"
    assert isinstance(upload.src, StringIO)
    assert upload.src.getvalue() == script_text
    assert upload.dest.endswith("/session.sh")
    assert chmod.get_raw_value().rstrip("'").endswith("/session.sh")
    assert vbash.get_raw_value() == sg_vbash_run(f"{staging}/session.sh", staging).get_raw_value()
    assert f"{staging}/session.sh" in vbash.get_raw_value()


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


def test_pending_save_probe_reduces_comparison_to_a_byte_count() -> None:
    command = pending_save_probe("PYINFRA_VYOS")
    rendered = command.get_raw_value()
    pipeline = f"{{ {NEEDS_SAVE_COMMAND} ; }} | tr -d '[:space:]' | LC_ALL=C wc -c"
    quoted_pipeline = pipeline.replace("'", "'\\''")

    assert rendered == (
        f"vbash -c 'set -o pipefail\n{quoted_pipeline} && printf '\\''\\n%s\\n'\\'' PYINFRA_VYOS'"
    )
    assert "set -o pipefail" in rendered
    assert "wc -c" in rendered
    assert "tr -d '[:space:]'" in pipeline
    assert quoted_pipeline in rendered
    assert NEEDS_SAVE_COMMAND in rendered
    # Marker is chained with && so it prints only on pipeline success,
    # and printf's leading newline forces it onto its own line.
    assert "&& printf" in rendered
    assert "\\n%s\\n" in rendered
    assert "PYINFRA_VYOS" in rendered
    assert rendered.count(NEEDS_SAVE_COMMAND) == 1
    assert rendered.index(NEEDS_SAVE_COMMAND) < rendered.index("tr -d")
    assert rendered.index("[:space:]") < rendered.index("| LC_ALL=C wc -c")
    # Comparison stdout is consumed by wc; showConfig never reaches
    # the outer command's stdout on its own.
    assert rendered.index("showConfig") < rendered.index("| LC_ALL=C wc -c")
