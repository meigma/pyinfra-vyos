"""Parsers for VyOS op-mode fact output and controller-side stream checks.

This is the parse half of the domain layer: pure functions with no I/O and
no pyinfra state, which is what lets the unit tests run without mocks.
``facts.py`` feeds it raw CLI output after stripping the package marker;
``operations.py`` uses the streaming non-empty check on ``config_load`` src.

Source-verified VyOS behaviour this module relies on (1.4 sagitta / 1.5
circinus, documented ``show version`` / ``show configuration``):

- ``show version`` is ``Label: value`` lines. Values may contain colons
  (timestamps), so only the first colon is the separator. Labels are
  normalized to lowercase with spaces turned into underscores. The
  ``version`` field is required; unknown labels and missing optionals are
  tolerated because the field set drifts across trains.
- ``show configuration json`` is a JSON object. The tree is returned as
  loaded; this wave does not normalize keys.
- ``show configuration commands`` lines are device-rendered set-form and
  kept as-is aside from dropping empty and trailing-whitespace-only lines.
- Fact commands emit a package-controlled trailing marker so empty stdout
  cannot masquerade as ``default()`` (pyinfra skips ``process()`` on empty
  stdout). The marker must be present as the last non-empty line.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

__all__ = [
    "OUTPUT_MARKER",
    "ParseError",
    "config_command_lines",
    "parse_config_json",
    "parse_version",
    "stream_is_nonempty",
    "strip_marker",
]

# Distinct from session sentinels (``PYINFRA_VYOS changed`` / ``noop``) and
# implausible as a ``show`` payload line so a collision fails closed.
OUTPUT_MARKER = "PYINFRA_VYOS_FACT_MARKER_v1"

_CHUNK_SIZE = 8192


class ParseError(RuntimeError):
    """Raised when VyOS fact output cannot be parsed safely."""


class _SeekableStream(Protocol):
    def read(self, size: int = ...) -> str | bytes: ...

    def seek(self, offset: int, whence: int = 0) -> int: ...


def strip_marker(lines: list[str]) -> list[str]:
    """Require and remove the trailing package output marker.

    Fact commands append :data:`OUTPUT_MARKER` on its own line so a successful
    empty payload cannot be mistaken for a missing binary. Absence of the
    marker is a parse failure, not an empty result.
    """

    index = len(lines) - 1
    while index >= 0 and lines[index] == "":
        index -= 1
    if index < 0 or lines[index] != OUTPUT_MARKER:
        raise ParseError("fact output is missing the trailing package marker")
    return lines[:index]


def parse_version(lines: list[str]) -> dict[str, str]:
    """Parse ``show version`` lines into a label-to-value mapping.

    Each line is split on the first colon only. Labels are lowercased and
    have spaces replaced with underscores. Unknown labels are kept; missing
    optional fields are omitted. A ``version`` key is required.
    """

    fields: dict[str, str] = {}
    for line in lines:
        label, separator, value = line.partition(":")
        if not separator:
            continue
        key = label.strip().lower().replace(" ", "_")
        if not key:
            continue
        fields[key] = value.strip()
    if "version" not in fields:
        raise ParseError("show version output is missing a version field")
    return fields


def parse_config_json(text: str) -> dict[str, Any]:
    """Parse ``show configuration json`` into the raw config tree.

    The top-level value must be a JSON object. Nested structure is returned
    as ``json.loads`` produced it — no key or value normalization.
    """

    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as error:
        raise ParseError(f"configuration JSON is not valid: {error}") from error
    if not isinstance(loaded, dict):
        raise ParseError(
            f"configuration JSON must be a top-level object, got {type(loaded).__name__}",
        )
    return loaded


def config_command_lines(lines: list[str]) -> list[str]:
    """Return nonempty ``show configuration commands`` lines as rendered.

    Trailing whitespace and empty lines are dropped. Leading whitespace and
    the rest of each line are left unchanged — there is no fixture-backed
    rewrite of device-rendered set-form.
    """

    rendered: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if stripped:
            rendered.append(stripped)
    return rendered


def stream_is_nonempty(fileobj: _SeekableStream) -> bool:
    """Return True if *fileobj* contains at least one non-whitespace byte.

    Reads in chunks and returns on the first non-whitespace hit so a large
    config is never fully buffered. Works for text and binary streams. The
    stream is seeked back to the start afterwards so a later upload or parse
    sees the same content.
    """

    fileobj.seek(0)
    try:
        while True:
            chunk = fileobj.read(_CHUNK_SIZE)
            if not chunk:
                return False
            if chunk.strip():
                return True
    finally:
        fileobj.seek(0)
