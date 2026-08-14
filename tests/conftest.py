"""Shared pytest configuration for both test tiers.

Unit tests run everywhere with no setup. Integration tests drive the real
pyinfra API against ``@local`` and write to the filesystem, so they are
marked ``integration`` and skipped unless ``--integration`` is passed —
``moon run root:test-integration`` is the entry point that passes it.
"""

from __future__ import annotations

import pytest

_SKIP_REASON = "pass --integration to run tests against the local git CLI"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="run integration tests against the local git CLI",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if config.getoption("--integration"):
        return
    skip = pytest.mark.skip(reason=_SKIP_REASON)
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
