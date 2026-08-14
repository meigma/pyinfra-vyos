"""Tests for the release distribution validator.

The script under test lives in this directory rather than on the import path,
so it is loaded by file location instead of by name. That keeps the tests
independent of pytest's import mode and of the working directory the suite is
invoked from.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib
import sys
import tarfile
import zipfile
from collections.abc import Sequence
from types import ModuleType

import pytest


def _load_script(name: str) -> ModuleType:
    path = pathlib.Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validate_release = _load_script("validate_release")

PACKAGE = "template-pyinfra"
MODULE = "template_pyinfra"
VERSION = "1.2.3"


def metadata_text(
    name: str = PACKAGE,
    version: str = VERSION,
    requires: Sequence[str] = (),
) -> bytes:
    lines = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
    lines.extend(f"Requires-Dist: {requirement}" for requirement in requires)
    return ("\n".join(lines) + "\n\n").encode()


def write_wheel(
    dist: pathlib.Path,
    name: str = PACKAGE,
    version: str = VERSION,
    requires: Sequence[str] = (),
    suffix: str = "",
) -> pathlib.Path:
    dist.mkdir(parents=True, exist_ok=True)
    module = name.replace("-", "_")
    path = dist / f"{module}-{version}{suffix}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{module}-{version}.dist-info/METADATA",
            metadata_text(name, version, requires),
        )
    return path


def write_sdist(
    dist: pathlib.Path,
    name: str = PACKAGE,
    version: str = VERSION,
    requires: Sequence[str] = (),
    suffix: str = "",
) -> pathlib.Path:
    dist.mkdir(parents=True, exist_ok=True)
    module = name.replace("-", "_")
    path = dist / f"{module}-{version}{suffix}.tar.gz"
    payload = metadata_text(name, version, requires)
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(f"{module}-{version}/PKG-INFO")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return path


def test_accepts_matching_distributions(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dist = tmp_path / "dist"
    write_wheel(dist)
    write_sdist(dist)

    validate_release.main([str(dist), "--tag", f"v{VERSION}"])

    assert f"validated {PACKAGE} {VERSION}" in capsys.readouterr().out


def test_accepts_tag_without_v_prefix(tmp_path: pathlib.Path) -> None:
    dist = tmp_path / "dist"
    write_wheel(dist)
    write_sdist(dist)

    validate_release.main([str(dist), "--tag", VERSION])


def test_defaults_to_dist_directory(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    write_wheel(dist)
    write_sdist(dist)
    monkeypatch.chdir(tmp_path)

    validate_release.main([])


def test_accepts_normalized_name_variants(tmp_path: pathlib.Path) -> None:
    dist = tmp_path / "dist"
    write_wheel(dist, name=MODULE)
    write_sdist(dist, name=MODULE)

    validate_release.main([str(dist)])


def test_rejects_version_mismatch(tmp_path: pathlib.Path) -> None:
    dist = tmp_path / "dist"
    write_wheel(dist)
    write_sdist(dist)

    with pytest.raises(SystemExit) as excinfo:
        validate_release.main([str(dist), "--tag", "v9.9.9"])

    assert "does not match package version" in str(excinfo.value)


def test_rejects_duplicate_wheels(tmp_path: pathlib.Path) -> None:
    dist = tmp_path / "dist"
    write_wheel(dist)
    write_wheel(dist, suffix=".post1")
    write_sdist(dist)

    with pytest.raises(SystemExit) as excinfo:
        validate_release.main([str(dist)])

    assert "found 2 wheel(s) and 1 sdist(s)" in str(excinfo.value)


def test_rejects_missing_sdist(tmp_path: pathlib.Path) -> None:
    dist = tmp_path / "dist"
    write_wheel(dist)

    with pytest.raises(SystemExit) as excinfo:
        validate_release.main([str(dist)])

    assert "found 1 wheel(s) and 0 sdist(s)" in str(excinfo.value)


def test_rejects_direct_reference_requirement(tmp_path: pathlib.Path) -> None:
    dist = tmp_path / "dist"
    write_wheel(dist, requires=["pyinfra @ https://example.invalid/pyinfra.whl"])
    write_sdist(dist)

    with pytest.raises(SystemExit) as excinfo:
        validate_release.main([str(dist)])

    assert "direct dependency references" in str(excinfo.value)


def test_rejects_foreign_package_name(tmp_path: pathlib.Path) -> None:
    dist = tmp_path / "dist"
    write_wheel(dist, name="some-other-package")
    write_sdist(dist)

    with pytest.raises(SystemExit) as excinfo:
        validate_release.main([str(dist)])

    assert f"expected package name {PACKAGE!r}" in str(excinfo.value)


def test_rejects_wheel_and_sdist_version_disagreement(tmp_path: pathlib.Path) -> None:
    dist = tmp_path / "dist"
    write_wheel(dist)
    write_sdist(dist, version="1.2.4")

    with pytest.raises(SystemExit) as excinfo:
        validate_release.main([str(dist)])

    assert "does not match sdist version" in str(excinfo.value)


def test_rejects_missing_dist_directory(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        validate_release.main([str(tmp_path / "dist")])

    assert "does not exist" in str(excinfo.value)
