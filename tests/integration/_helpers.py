"""Shared pyinfra harness and ``git`` CLI helpers for the integration suite.

Facts and operations run ``git`` on the target through pyinfra's ``@local``
connector, which inherits the test process environment. The subprocess
helpers here run the same CLI directly for independent state reads, so an
assertion never confirms the code under test using the code under test.

The four pyinfra helpers are the whole harness — copy them as-is into a real
package; only the ``git``-specific helpers below them need replacing.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

import pytest
from pyinfra.api import Config, Inventory, State
from pyinfra.api.connect import connect_all
from pyinfra.api.facts import get_facts
from pyinfra.api.operation import OperationMeta, add_op
from pyinfra.api.operations import run_ops


def new_state() -> State:
    """Return a fresh connected pyinfra state over the ``@local`` connector."""

    state = State(inventory=Inventory((["@local"], {})), config=Config())
    connect_all(state)
    return state


def prepare(operation: Any, **kwargs: Any) -> tuple[State, OperationMeta]:
    """Run only the prepare phase (a pure dry run) of one operation."""

    state = new_state()
    results = add_op(state, operation, **kwargs)
    return state, next(iter(results.values()))


def apply(operation: Any, **kwargs: Any) -> OperationMeta:
    """Prepare and execute one operation, returning its meta."""

    state, meta = prepare(operation, **kwargs)
    run_ops(state)
    return meta


def fact_value(fact: Any, **kwargs: Any) -> Any:
    """Return the single ``@local`` host's value for one fact."""

    return next(iter(get_facts(new_state(), fact, kwargs=kwargs).values()))


def try_git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the ``git`` CLI directly, returning the result even on failure."""

    binary = shutil.which("git")
    if binary is None:
        pytest.fail("the git CLI is unavailable")
    return subprocess.run(
        [binary, *args],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def run_git(*args: str) -> str:
    """Run the ``git`` CLI directly, failing hard on a nonzero exit."""

    result = try_git(*args)
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}",
        )
    return result.stdout


def init_repository(path: str) -> str:
    """Create a throwaway repository and return its path.

    ``init.defaultBranch`` is set explicitly so the run is quiet and
    deterministic regardless of the runner's git version or user config.
    """

    run_git("-c", "init.defaultBranch=main", "init", "--quiet", path)
    return path


def config_value(path: str, key: str) -> str | None:
    """Read one repository-local config value, or ``None`` when it is unset.

    ``git config --get`` exits 1 for a missing key, which is not a failure
    here — it is the assertion that the key is gone.
    """

    result = try_git("-C", path, "config", "--local", "--get", key)
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")
