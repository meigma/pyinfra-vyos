from __future__ import annotations

import pytest
from pyinfra.api.exceptions import FactProcessError

from pyinfra_vyos._parse import OUTPUT_MARKER
from pyinfra_vyos.facts import Configuration, ConfigurationCommands, Version

# synthesized from docs/research, not appliance-captured
SHOW_VERSION_SAGITTA = [
    "Version:          VyOS 1.4-rolling-202106270801",
    "Release Train:    sagitta",
    "",
    "Built by:         senthil@vyos.net",
    "Built on:         Sun 27 Jun 2021 09:50 UTC",
    "Build UUID:       ee77c375-9100-49f4-b536-0a0a14c6a1e8",
    "Build Commit ID:  ed0b0ebca95fba82",
    "",
    "Architecture:     x86_64",
    "Boot via:         installed image",
    "System type:      KVM guest",
    "",
    "Hardware vendor:  QEMU",
    "Hardware model:   Standard PC (i440FX + PIIX, 1996)",
    "Hardware S/N:     ",
    "Hardware UUID:    Unknown",
    "",
    "Copyright:        VyOS maintainers and contributors",
]

# synthesized from docs/research, not appliance-captured
SHOW_VERSION_CIRCINUS = [
    "Version:          VyOS 1.5-rolling-202403010000",
    "Release Train:    circinus",
    "",
    "Built by:         autobuild@vyos.net",
    "Built on:         Fri 01 Mar 2024 00:00 UTC",
    "Build UUID:       11111111-2222-3333-4444-555555555555",
    "Build Commit ID:  abcdef0123456789",
    "",
    "Architecture:     x86_64",
    "Boot via:         installed image",
    "System type:      KVM guest",
    "",
    "Hardware vendor:  QEMU",
    "Hardware model:   Standard PC (Q35 + ICH9, 2009)",
    "Hardware S/N:     ",
    "Hardware UUID:    Unknown",
    "",
    "Copyright:        VyOS maintainers and contributors",
]

CONFIG_JSON_VALID = '{"system": {"host-name": "gateway", "login": {"user": {"vyos": {}}}}}'

# synthesized from docs/research, not appliance-captured
CONFIG_COMMANDS = [
    "set system host-name 'gateway'",
    "set interfaces ethernet eth0 address '192.0.2.1/24'",
    "set system login user vyos authentication hashed-password '$6$secret'",
]
CONFIG_COMMANDS_STRIP_PRIVATE = [
    "set system host-name 'gateway'",
    "set interfaces ethernet eth0 address '192.0.2.1/24'",
    "set system login user vyos authentication hashed-password '****************'",
]


def _with_marker(lines: list[str]) -> list[str]:
    return [*lines, "", OUTPUT_MARKER]


def test_version_fact_runs_show_version_with_one_run_and_marker() -> None:
    command = Version().command()
    rendered = command.get_raw_value()

    assert rendered == (
        "vbash -c 'set -o pipefail\n"
        "export VYATTA_PAGER=cat\n"
        "source /opt/vyatta/etc/functions/script-template\n"
        f"run show version && printf '\\''\\n%s\\n'\\'' {OUTPUT_MARKER}'"
    )
    assert OUTPUT_MARKER in rendered
    assert rendered.count("\nrun ") == 1
    assert command.get_masked_value() == rendered


def test_version_fact_parses_marker_wrapped_sagitta_payload() -> None:
    parsed = Version().process(_with_marker(SHOW_VERSION_SAGITTA))

    assert parsed["version"] == "VyOS 1.4-rolling-202106270801"
    assert parsed["release_train"] == "sagitta"
    assert parsed["built_by"] == "senthil@vyos.net"
    assert parsed["built_on"] == "Sun 27 Jun 2021 09:50 UTC"
    assert parsed["architecture"] == "x86_64"
    assert parsed["hardware_s/n"] == ""


def test_version_fact_parses_marker_wrapped_circinus_payload() -> None:
    parsed = Version().process(_with_marker(SHOW_VERSION_CIRCINUS))

    assert parsed["version"] == "VyOS 1.5-rolling-202403010000"
    assert parsed["release_train"] == "circinus"


def test_configuration_fact_runs_show_configuration_json() -> None:
    command = Configuration().command()
    rendered = command.get_raw_value()

    assert rendered == (
        "vbash -c 'set -o pipefail\n"
        "export VYATTA_PAGER=cat\n"
        "source /opt/vyatta/etc/functions/script-template\n"
        "run show configuration json && "
        f"printf '\\''\\n%s\\n'\\'' {OUTPUT_MARKER}'"
    )
    assert OUTPUT_MARKER in rendered
    assert rendered.count("\nrun ") == 1


def test_configuration_fact_parses_marker_wrapped_json() -> None:
    parsed = Configuration().process(_with_marker([CONFIG_JSON_VALID]))

    assert parsed == {
        "system": {"host-name": "gateway", "login": {"user": {"vyos": {}}}},
    }


def test_configuration_fact_rejoins_multiline_json() -> None:
    """pyinfra splits stdout on newlines; JSON is rejoined before parsing."""

    output = _with_marker(
        [
            "{",
            '  "system": {',
            '    "host-name": "gateway"',
            "  }",
            "}",
        ],
    )

    assert Configuration().process(output) == {"system": {"host-name": "gateway"}}


def test_configuration_commands_fact_runs_show_configuration_commands() -> None:
    command = ConfigurationCommands().command()
    rendered = command.get_raw_value()

    assert rendered == (
        "vbash -c 'set -o pipefail\n"
        "export VYATTA_PAGER=cat\n"
        "source /opt/vyatta/etc/functions/script-template\n"
        "run show configuration commands && "
        f"printf '\\''\\n%s\\n'\\'' {OUTPUT_MARKER}'"
    )
    assert OUTPUT_MARKER in rendered
    assert rendered.count("\nrun ") == 1
    assert "strip-private" not in rendered
    assert "strip-private" not in rendered


def test_configuration_commands_fact_pipes_through_strip_private_filter() -> None:
    """Redaction is a real shell pipeline through the target's filter script."""

    command = ConfigurationCommands().command(strip_private=True)
    rendered = command.get_raw_value()

    assert rendered == (
        "vbash -c 'set -o pipefail\n"
        "export VYATTA_PAGER=cat\n"
        "source /opt/vyatta/etc/functions/script-template\n"
        "run show configuration commands | /usr/libexec/vyos/strip-private.py && "
        f"printf '\\''\\n%s\\n'\\'' {OUTPUT_MARKER}'"
    )
    assert OUTPUT_MARKER in rendered
    assert rendered.count("\nrun ") == 1
    assert "| /usr/libexec/vyos/strip-private.py" in rendered


def test_configuration_commands_fact_parses_marker_wrapped_set_form() -> None:
    assert ConfigurationCommands().process(_with_marker(CONFIG_COMMANDS)) == CONFIG_COMMANDS


def test_configuration_commands_fact_keeps_strip_private_redaction() -> None:
    assert (
        ConfigurationCommands().process(_with_marker(CONFIG_COMMANDS_STRIP_PRIVATE))
        == CONFIG_COMMANDS_STRIP_PRIVATE
    )


def test_facts_require_the_vbash_binary() -> None:
    """Hosts without vbash yield ``default()``; this is not a VyOS-ness check."""

    assert Version().requires_command() == "vbash"
    assert Configuration().requires_command() == "vbash"
    assert ConfigurationCommands().requires_command() == "vbash"
    assert ConfigurationCommands().requires_command(strip_private=True) == "vbash"


def test_fact_defaults_are_empty() -> None:
    assert Version().default() == {}
    assert Configuration().default() == {}
    assert ConfigurationCommands().default() == []


@pytest.mark.parametrize(
    "fact",
    [Version(), Configuration(), ConfigurationCommands()],
)
def test_marker_missing_payload_raises_fact_process_error(
    fact: Version | Configuration | ConfigurationCommands,
) -> None:
    """Processing failures must fail only the affected host, not the run.

    pyinfra contains only ``FactProcessError`` around ``fact.process()``;
    anything else escaping would abort the entire multi-host deploy.
    """

    with pytest.raises(FactProcessError, match="missing the trailing package marker"):
        fact.process(["Version:          VyOS 1.4-rolling-202106270801"])


def test_version_empty_payload_with_marker_fails_loudly() -> None:
    with pytest.raises(FactProcessError, match="missing a version field"):
        Version().process([OUTPUT_MARKER])
    with pytest.raises(FactProcessError, match="missing a version field"):
        Version().process(["", OUTPUT_MARKER])


def test_configuration_empty_payload_with_marker_fails_loudly() -> None:
    with pytest.raises(FactProcessError, match="not valid"):
        Configuration().process([OUTPUT_MARKER])
    with pytest.raises(FactProcessError, match="not valid"):
        Configuration().process(["", OUTPUT_MARKER])


def test_configuration_commands_empty_payload_with_marker_is_empty() -> None:
    assert ConfigurationCommands().process([OUTPUT_MARKER]) == []
    assert ConfigurationCommands().process(["", OUTPUT_MARKER]) == []
