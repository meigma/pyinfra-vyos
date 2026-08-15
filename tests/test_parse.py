from __future__ import annotations

import io

import pytest

from pyinfra_vyos._parse import (
    OUTPUT_MARKER,
    ParseError,
    config_command_lines,
    parse_config_json,
    parse_version,
    stream_is_nonempty,
    strip_marker,
)

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

# synthesized from docs/research, not appliance-captured
SHOW_VERSION_UNKNOWN_LABEL = [
    *SHOW_VERSION_SAGITTA[:8],
    "Kernel:           6.6.20-amd64-vyos",
    *SHOW_VERSION_SAGITTA[8:],
]

# synthesized from docs/research, not appliance-captured
SHOW_VERSION_MISSING_OPTIONAL = [
    "Version:          VyOS 1.4-rolling-202106270801",
    "Release Train:    sagitta",
    "Built by:         senthil@vyos.net",
    "Architecture:     x86_64",
    "Boot via:         installed image",
    "System type:      KVM guest",
    "Copyright:        VyOS maintainers and contributors",
]

CONFIG_JSON_VALID = '{"system": {"host-name": "gateway", "login": {"user": {"vyos": {}}}}}'
CONFIG_JSON_NON_DICT = '["system", "host-name"]'
CONFIG_JSON_INVALID = '{"system":'

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


class _CountingStream:
    """File-like that records each ``read`` size so tests can prove chunking."""

    def __init__(self, inner: io.StringIO | io.BytesIO) -> None:
        self._inner = inner
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> str | bytes:
        self.read_sizes.append(size)
        return self._inner.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._inner.seek(offset, whence)

    def tell(self) -> int:
        return self._inner.tell()


def _with_marker(lines: list[str]) -> list[str]:
    return [*lines, "", OUTPUT_MARKER]


def test_parse_version_reads_sagitta_show_version() -> None:
    parsed = parse_version(SHOW_VERSION_SAGITTA)

    assert parsed["version"] == "VyOS 1.4-rolling-202106270801"
    assert parsed["release_train"] == "sagitta"
    assert parsed["built_by"] == "senthil@vyos.net"
    assert parsed["built_on"] == "Sun 27 Jun 2021 09:50 UTC"
    assert parsed["build_commit_id"] == "ed0b0ebca95fba82"
    assert parsed["architecture"] == "x86_64"
    assert parsed["hardware_s/n"] == ""


def test_parse_version_reads_circinus_show_version() -> None:
    parsed = parse_version(SHOW_VERSION_CIRCINUS)

    assert parsed["version"] == "VyOS 1.5-rolling-202403010000"
    assert parsed["release_train"] == "circinus"


def test_parse_version_splits_on_the_first_colon_only() -> None:
    parsed = parse_version(SHOW_VERSION_SAGITTA)

    assert parsed["built_on"] == "Sun 27 Jun 2021 09:50 UTC"


def test_parse_version_keeps_unknown_labels() -> None:
    parsed = parse_version(SHOW_VERSION_UNKNOWN_LABEL)

    assert parsed["kernel"] == "6.6.20-amd64-vyos"
    assert parsed["version"] == "VyOS 1.4-rolling-202106270801"


def test_parse_version_tolerates_missing_optional_fields() -> None:
    parsed = parse_version(SHOW_VERSION_MISSING_OPTIONAL)

    assert parsed["version"] == "VyOS 1.4-rolling-202106270801"
    assert "hardware_uuid" not in parsed
    assert "build_uuid" not in parsed


def test_parse_version_requires_a_version_key() -> None:
    with pytest.raises(ParseError, match="missing a version field"):
        parse_version(["Release Train:    sagitta"])


def test_parse_config_json_returns_the_raw_tree() -> None:
    parsed = parse_config_json(CONFIG_JSON_VALID)

    assert parsed == {
        "system": {"host-name": "gateway", "login": {"user": {"vyos": {}}}},
    }


def test_parse_config_json_rejects_invalid_json() -> None:
    with pytest.raises(ParseError, match="not valid"):
        parse_config_json(CONFIG_JSON_INVALID)


@pytest.mark.parametrize(
    "payload",
    [CONFIG_JSON_NON_DICT, '"gateway"', "1", "null", "true"],
)
def test_parse_config_json_rejects_a_non_object(payload: str) -> None:
    with pytest.raises(ParseError, match="top-level object"):
        parse_config_json(payload)


def test_config_command_lines_preserves_device_rendered_text() -> None:
    assert config_command_lines(CONFIG_COMMANDS) == CONFIG_COMMANDS


def test_config_command_lines_keeps_strip_private_redaction() -> None:
    assert config_command_lines(CONFIG_COMMANDS_STRIP_PRIVATE) == CONFIG_COMMANDS_STRIP_PRIVATE


def test_config_command_lines_drops_empty_and_trailing_whitespace_lines() -> None:
    lines = [
        "set system host-name 'gateway'",
        "",
        "set interfaces ethernet eth0 address '192.0.2.1/24'  ",
        "   ",
        "",
    ]

    assert config_command_lines(lines) == [
        "set system host-name 'gateway'",
        "set interfaces ethernet eth0 address '192.0.2.1/24'",
    ]


def test_config_command_lines_does_not_strip_leading_whitespace() -> None:
    assert config_command_lines(["  set system host-name 'gateway'"]) == [
        "  set system host-name 'gateway'",
    ]


def test_strip_marker_removes_a_trailing_marker() -> None:
    assert strip_marker(_with_marker(SHOW_VERSION_SAGITTA)) == [
        *SHOW_VERSION_SAGITTA,
        "",
    ]


def test_strip_marker_rejects_a_missing_marker() -> None:
    with pytest.raises(ParseError, match="missing the trailing package marker"):
        strip_marker(SHOW_VERSION_SAGITTA)


def test_strip_marker_rejects_an_empty_payload() -> None:
    with pytest.raises(ParseError, match="missing the trailing package marker"):
        strip_marker([])


def test_strip_marker_rejects_a_marker_that_is_not_trailing() -> None:
    with pytest.raises(ParseError, match="missing the trailing package marker"):
        strip_marker([OUTPUT_MARKER, "Version: VyOS 1.4.0"])


def test_strip_marker_allows_an_empty_success_payload() -> None:
    assert strip_marker(["", OUTPUT_MARKER, ""]) == [""]


def test_parse_version_fails_loudly_on_empty_marked_payload() -> None:
    with pytest.raises(ParseError, match="missing a version field"):
        parse_version(strip_marker([OUTPUT_MARKER]))


def test_stream_is_nonempty_detects_text_content() -> None:
    stream = io.StringIO("set system host-name 'gateway'\n")

    assert stream_is_nonempty(stream) is True
    assert stream.tell() == 0
    assert stream.read().startswith("set system")


def test_stream_is_nonempty_detects_binary_content() -> None:
    stream = io.BytesIO(b"set system host-name 'gateway'\n")

    assert stream_is_nonempty(stream) is True
    assert stream.tell() == 0


def test_stream_is_nonempty_rejects_whitespace_only_text() -> None:
    assert stream_is_nonempty(io.StringIO(" \n\t  \n")) is False


def test_stream_is_nonempty_rejects_whitespace_only_bytes() -> None:
    assert stream_is_nonempty(io.BytesIO(b" \n\t  \n")) is False


def test_stream_is_nonempty_rejects_an_empty_stream() -> None:
    assert stream_is_nonempty(io.StringIO("")) is False
    assert stream_is_nonempty(io.BytesIO(b"")) is False


def test_stream_is_nonempty_reads_in_chunks_and_stops_early() -> None:
    payload = "x" + (" " * 100_000)
    stream = _CountingStream(io.StringIO(payload))

    assert stream_is_nonempty(stream) is True
    assert stream.read_sizes
    assert all(size != -1 for size in stream.read_sizes)
    assert sum(size for size in stream.read_sizes) < len(payload)
    assert stream.tell() == 0


def test_stream_is_nonempty_restores_position_to_the_start() -> None:
    stream = io.StringIO("abc")
    stream.read(1)

    assert stream_is_nonempty(stream) is True
    assert stream.tell() == 0
    assert stream.read() == "abc"
