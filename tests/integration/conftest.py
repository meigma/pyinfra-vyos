"""Integration-only pytest fixtures.

``vbash_shim`` is opt-in: tests that omit it keep the no-vbash default() path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pyinfra_vyos._parse import OUTPUT_MARKER

# captured from the Lima lab 2026-08-15
_LAB_VERSION_LINE = "Version:          VyOS 2026.03"

_VBASH_SHIM = rf"""#!/bin/sh
payload="$2"
case "$payload" in
*"show version"*)
    printf '%s\n' '{_LAB_VERSION_LINE}'
    printf '\n%s\n' '{OUTPUT_MARKER}'
    ;;
*"show configuration json"*)
    printf '%s\n' '{{}}'
    printf '\n%s\n' '{OUTPUT_MARKER}'
    ;;
*)
    exit 1
    ;;
esac
"""


@pytest.fixture
def vbash_shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install a PATH-visible ``vbash`` that answers Version and Configuration on @local.

    pyinfra wraps fact commands in ``if command -v vbash`` and then runs
    ``vbash -c …`` via the local shell, which inherits ``os.environ``.
    ``monkeypatch.setenv`` mutates ``PATH`` for that subprocess and restores
    it after the test so later no-vbash cases cannot see a stale shim.
    """

    bindir = tmp_path / "vbash-shim"
    bindir.mkdir()
    shim = bindir / "vbash"
    shim.write_text(_VBASH_SHIM)
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return shim
