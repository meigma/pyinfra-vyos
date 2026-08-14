"""Validate the built distributions before a registry upload.

Run against a populated ``dist/`` directory. Without ``--tag`` the check is
self-consistency only (the wheel and the sdist must agree); with ``--tag`` the
distributions must also match the version being released.

Stdlib only: release workflows run this with the bare interpreter, before any
project environment is synchronized.
"""

from __future__ import annotations

import argparse
import email
import pathlib
import re
import tarfile
import zipfile
from collections.abc import Sequence
from email.message import Message
from typing import NoReturn

PACKAGE_NAME = "pyinfra-vyos"


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def normalize(name: str) -> str:
    """Normalize a distribution name the way PEP 503 does."""
    return re.sub(r"[-_.]+", "-", name).lower()


def metadata_from_wheel(path: pathlib.Path) -> Message:
    with zipfile.ZipFile(path) as archive:
        metadata_files = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_files) != 1:
            fail(f"expected one METADATA file in {path.name}, found {len(metadata_files)}")
        return email.message_from_bytes(archive.read(metadata_files[0]))


def metadata_from_sdist(path: pathlib.Path) -> Message:
    with tarfile.open(path, "r:gz") as archive:
        metadata_files = [
            member for member in archive.getmembers() if member.name.endswith("/PKG-INFO")
        ]
        if len(metadata_files) != 1:
            fail(f"expected one PKG-INFO file in {path.name}, found {len(metadata_files)}")
        metadata_file = archive.extractfile(metadata_files[0])
        if metadata_file is None:
            fail(f"could not read PKG-INFO from {path.name}")
        return email.message_from_bytes(metadata_file.read())


def validate_metadata(metadata: Message, expected_version: str | None) -> str:
    name = metadata.get("Name")
    version = metadata.get("Version")
    if name is None or normalize(name) != normalize(PACKAGE_NAME):
        fail(f"expected package name {PACKAGE_NAME!r}, got {name!r}")
    if not version:
        fail("distribution metadata has no version")
    if expected_version is not None and version != expected_version:
        fail(f"tag version {expected_version!r} does not match package version {version!r}")

    # A direct reference (``name @ url``) is legal locally but rejected by
    # public indexes, so it must never reach an upload.
    direct_references = [
        requirement for requirement in metadata.get_all("Requires-Dist", []) if " @ " in requirement
    ]
    if direct_references:
        rendered = "\n".join(f"- {requirement}" for requirement in direct_references)
        fail(f"public package indexes reject direct dependency references:\n{rendered}")

    return version


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "dist",
        nargs="?",
        default=pathlib.Path("dist"),
        type=pathlib.Path,
        help="directory holding the built distributions (default: dist)",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="release tag or version the distributions must match, e.g. v1.2.3",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    expected_version = args.tag[1:] if args.tag and args.tag.startswith("v") else args.tag

    dist: pathlib.Path = args.dist
    if not dist.is_dir():
        fail(f"distribution directory {dist} does not exist")

    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        fail(
            f"expected one wheel and one sdist in {dist}, "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )

    wheel_version = validate_metadata(metadata_from_wheel(wheels[0]), expected_version)
    sdist_version = validate_metadata(metadata_from_sdist(sdists[0]), expected_version)
    if wheel_version != sdist_version:
        fail(f"wheel version {wheel_version!r} does not match sdist version {sdist_version!r}")

    print(f"validated {PACKAGE_NAME} {wheel_version}: {wheels[0].name}, {sdists[0].name}")


if __name__ == "__main__":
    main()
