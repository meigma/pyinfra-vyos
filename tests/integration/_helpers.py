"""Shared pyinfra harness for the integration suite.

Facts and operations run through pyinfra's real API. ``new_state`` defaults
to the ``@local`` connector. Appliance tests pass an inventory from
:func:`appliance_inventory`.
"""

from __future__ import annotations

import os
from typing import Any

from pyinfra.api import Config, Inventory, State
from pyinfra.api.connect import connect_all
from pyinfra.api.facts import get_facts
from pyinfra.api.operation import OperationMeta, add_op
from pyinfra.api.operations import run_ops
from pyinfra.context import ctx_host, ctx_state


def appliance_inventory() -> Inventory:
    """Build a single-host SSH inventory from ``PYINFRA_VYOS_TEST_*`` env vars."""

    hostname = os.environ.get("PYINFRA_VYOS_TEST_HOST", "").strip()
    if not hostname:
        raise RuntimeError("PYINFRA_VYOS_TEST_HOST is not set")

    data: dict[str, Any] = {}
    user = os.environ.get("PYINFRA_VYOS_TEST_USER", "").strip()
    if user:
        data["ssh_user"] = user
    port = os.environ.get("PYINFRA_VYOS_TEST_PORT", "").strip()
    if port:
        data["ssh_port"] = int(port)
    key = os.environ.get("PYINFRA_VYOS_TEST_KEY", "").strip()
    if key:
        data["ssh_key"] = key

    return Inventory(([hostname], {}), override_data=data)


def new_state(inventory: Inventory | None = None) -> State:
    """Return a fresh connected pyinfra state.

    ``inventory`` defaults to the ``@local`` connector. A passed inventory is
    cloned by name and override data so each state gets its own Host objects.
    """

    if inventory is None:
        built = Inventory((["@local"], {}))
    else:
        names = [host.name for host in inventory]
        built = Inventory((names, {}), override_data=dict(inventory.override_data))
    state = State(inventory=built, config=Config())
    connect_all(state)
    return state


def prepare(
    operation: Any,
    *,
    inventory: Inventory | None = None,
    **kwargs: Any,
) -> tuple[State, OperationMeta]:
    """Run only the prepare phase (a pure dry run) of one operation."""

    state = new_state(inventory)
    results = add_op(state, operation, **kwargs)
    return state, next(iter(results.values()))


def apply(
    operation: Any,
    *,
    inventory: Inventory | None = None,
    **kwargs: Any,
) -> OperationMeta:
    """Prepare and execute one operation, returning its meta."""

    state, meta = prepare(operation, inventory=inventory, **kwargs)
    run_ops(state)
    return meta


def fact_value(
    fact: Any,
    *,
    inventory: Inventory | None = None,
    **kwargs: Any,
) -> Any:
    """Return the single inventory host's value for one fact."""

    return next(iter(get_facts(new_state(inventory), fact, kwargs=kwargs).values()))


def operation_commands(state: State) -> list[Any]:
    """Evaluate the prepared operation's command generator on the sole host.

    pyinfra's generator closes over the ``context.host`` / ``context.state``
    proxies, so this must bind them the same way ``run_ops`` does.
    """

    host = next(iter(state.inventory))
    op_data = next(iter(state.ops[host].values()))
    with ctx_state.use(state), ctx_host.use(host):
        return list(op_data.command_generator())
