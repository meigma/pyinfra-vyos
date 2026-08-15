"""Shared pytest configuration for both test tiers.

Unit tests run everywhere with no setup. Integration tests drive the real
pyinfra API against ``@local`` and write to the filesystem, so they are
marked ``integration`` and skipped unless ``--integration`` is passed —
``moon run root:test-integration`` is the entry point that passes it.

Appliance tests talk to a live VyOS device. They are marked ``appliance``
and skipped unless both ``--appliance`` and ``PYINFRA_VYOS_TEST_HOST`` are
set. Plain ``--integration`` runs do not collect them as runnable.
"""

from __future__ import annotations

import os

import pytest

_SKIP_REASON = "pass --integration to run tests against the @local pyinfra connector"
_APPLIANCE_SKIP_REASON = (
    "pass --appliance and set PYINFRA_VYOS_TEST_HOST to run tests against a live VyOS appliance"
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="run integration tests against the @local pyinfra connector",
    )
    parser.addoption(
        "--appliance",
        action="store_true",
        default=False,
        help="run appliance tests against a live VyOS device",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    # Use get_closest_marker, not `in item.keywords`: keywords include parent
    # collector names, and every test under tests/integration/ has the
    # keyword "integration" from the directory alone — which wrongly gated
    # appliance tests behind --integration as well.
    run_appliance = config.getoption("--appliance") and bool(
        os.environ.get("PYINFRA_VYOS_TEST_HOST", "").strip(),
    )
    skip_appliance = pytest.mark.skip(reason=_APPLIANCE_SKIP_REASON)
    skip_integration = pytest.mark.skip(reason=_SKIP_REASON)
    run_integration = config.getoption("--integration")
    for item in items:
        if item.get_closest_marker("appliance") is not None:
            if not run_appliance:
                item.add_marker(skip_appliance)
        elif item.get_closest_marker("integration") is not None and not run_integration:
            item.add_marker(skip_integration)
