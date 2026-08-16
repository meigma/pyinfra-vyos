"""Pure renderer seam: desired keyword models to disjoint ``Scope`` lists.

Typed operations are renderers (A2, D6). This module holds the ``Scope``
algebra, the helpers every renderer shares, and the per-op renderer
functions themselves — :func:`render_system_basics`,
:func:`render_interface`, and :func:`render_static_route` so far; later
phases add their own. There is no I/O and no pyinfra state. Callers pass
values in and get values out.

Layer contract: a renderer maps a desired keyword model plus a schema key
to ``list[Scope]``. ``operations.py`` feeds those scopes to the shared
planner. Every token a renderer emits — path components, keys, and leaf
values — is validated through ``_tree`` (``normalize_tree(strict=True)`` /
``_require_token``) so the C2 leading-dash / nonempty-string rule holds
without a second validator in this module. Ergonomic ints (MTU, rule
numbers) are coerced to ``str`` with :func:`coerce_token` **before** those
token rules run.

Secret-field convention (used from Phase 5): validation errors name the
field and the accepted forms, never the value. A rejected
``encrypted_password`` must not echo the submitted hash or plaintext into
the exception message.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from pyinfra_vyos._tree import Node, TreeError, normalize_tree, validate_path

__all__ = [
    "Absent",
    "Exact",
    "Merge",
    "RenderError",
    "Scope",
    "coerce_token",
    "parse_route_destination",
    "render_interface",
    "render_static_route",
    "render_system_basics",
    "require_absent_args_unset",
    "schema_key",
]

# Qualified rolling families: calendar-version prefix -> schema key.
# Later fixture/appliance-qualified releases are one-line additions (D9).
_QUALIFIED_ROLLING: dict[str, str] = {
    "2026.03": "1.5",
}

_VERSION_PREFIX = "VyOS "

# Stable families: ``1.4`` / ``1.4.N[.suffix]`` and the 1.5 analogue.
# ``1.4-rolling-*`` does not match — the hyphen form is fail-closed.
_STABLE_SCHEMA: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"1\.4(?:\.\d+.*)?"), "1.4"),
    (re.compile(r"1\.5(?:\.\d+.*)?"), "1.5"),
)


class RenderError(ValueError):
    """Rejected renderer input; translated to OperationValueError."""


@dataclass(frozen=True)
class Absent:
    """Marker intent: ensure the node at ``path`` does not exist.

    Parameterless frozen dataclass. Callers construct ``Absent()``; checks
    use ``isinstance(intent, Absent)``. The class object is not the marker.
    """


@dataclass(frozen=True)
class Exact:
    """The node at ``path`` becomes exactly ``node``.

    ``node`` is a normalized leaf (``list[str]``, unordered set) or a
    subtree (``dict``, replace semantics: extra active keys/values pruned).
    """

    node: Node


@dataclass(frozen=True)
class Merge:
    """Open-body merge at ``path``: sets only, omitted state unmanaged."""

    subtree: dict[str, Node]


@dataclass(frozen=True)
class Scope:
    """One owned config node produced by a typed-op renderer (D8).

    Every ``set`` / ``delete`` planned from this scope inherits ``sensitive``.
    """

    path: list[str]
    intent: Absent | Exact | Merge
    sensitive: bool = False


def coerce_token(value: str | int) -> str:
    """Coerce an ergonomic int token to ``str`` before C2 token rules.

    Bools are rejected explicitly: ``bool`` is an ``int`` subclass, but
    ``True`` / ``False`` are not config-path tokens.
    """

    if isinstance(value, bool):
        raise RenderError("token must be a string or int, not a bool")
    if isinstance(value, int):
        return str(value)
    return value


def schema_key(version_string: str) -> str:
    """Map a raw ``Version`` fact ``version`` field to ``"1.4"`` or ``"1.5"``.

    Input is the fact field as captured (D9, fail-closed). An optional
    ``VyOS `` prefix is stripped (lab form ``VyOS 2026.03``; fixtures use
    ``VyOS 1.4-rolling-…``). Stable ``1.4`` / ``1.4.N[.suffix]`` map to
    ``"1.4"``; stable ``1.5`` / ``1.5.N[.suffix]`` map to ``"1.5"``. The
    only qualified rolling family is ``2026.03`` with an optional ``.N``
    patch, mapped to ``"1.5"``. Everything else — ``1.4-rolling-*``,
    ``1.5-rolling-*``, bare train names, unqualified calendar versions,
    empty, junk — raises :class:`RenderError` naming the version string
    and the version-agnostic ``config`` / ``config_load`` escape hatches.
    """

    token = (
        version_string[len(_VERSION_PREFIX) :]
        if version_string.startswith(_VERSION_PREFIX)
        else version_string
    )
    for pattern, schema in _STABLE_SCHEMA:
        if pattern.fullmatch(token):
            return schema
    for family, schema in _QUALIFIED_ROLLING.items():
        if not token.startswith(family):
            continue
        rest = token[len(family) :]
        if not rest or (rest.startswith(".") and rest[1:].isdigit()):
            return schema
    raise RenderError(
        f"unrecognized VyOS version {version_string!r}; typed operations "
        f"require a known 1.4 or 1.5 schema. Use the version-agnostic "
        f"config / config_load operations as an escape hatch"
    )


# D9 seam: 1.4 and 1.5 emit the same R§2 modern-baseline system identity
# leaves. Tables are keyed by schema anyway so a later fork does not break
# this signature.
_SYSTEM_BASICS_LEAVES: dict[str, list[str]] = {
    "hostname": ["system", "host-name"],
    "domain_name": ["system", "domain-name"],
    "name_servers": ["system", "name-server"],
    "search_domains": ["system", "domain-search"],
    "time_zone": ["system", "time-zone"],
}
_SYSTEM_BASICS_PATHS: dict[str, dict[str, list[str]]] = {
    "1.4": _SYSTEM_BASICS_LEAVES,
    "1.5": _SYSTEM_BASICS_LEAVES,
}

_SYSTEM_BASICS_SCALARS = frozenset({"hostname", "domain_name", "time_zone"})
_SYSTEM_BASICS_LISTS = frozenset({"name_servers", "search_domains"})

# D9 seam: 1.4 and 1.5 emit the same R§2 modern-baseline interface types
# (ethernet, loopback, dummy). Tables are keyed by schema anyway so a later
# fork does not break this signature.
_INTERFACE_TYPE_SET = frozenset({"ethernet", "loopback", "dummy"})
_INTERFACE_TYPES: dict[str, frozenset[str]] = {
    "1.4": _INTERFACE_TYPE_SET,
    "1.5": _INTERFACE_TYPE_SET,
}

# values top-level key -> typed argument to use instead (D10).
_INTERFACE_TYPED_KEYS: dict[str, str] = {
    "address": "addresses",
    "description": "description",
    "mtu": "mtu",
    "disable": "disabled",
}

# D9 seam: 1.4 and 1.5 emit the same R§2 modern-baseline static-route
# paths (protocols static route / route6). Tables are keyed by schema
# anyway so a later fork does not break this signature.
_STATIC_ROUTE_LEAVES: dict[int, str] = {4: "route", 6: "route6"}
_STATIC_ROUTE_PATHS: dict[str, dict[int, str]] = {
    "1.4": _STATIC_ROUTE_LEAVES,
    "1.5": _STATIC_ROUTE_LEAVES,
}


def _validated_path(path: list[str]) -> list[str]:
    try:
        return validate_path(path)
    except TreeError as error:
        raise RenderError(str(error)) from error


def _validated_leaf(value: str | list[str], *, field: str, where: str = "argument") -> Node:
    try:
        normalized = normalize_tree({field: value}, strict=True, _where=where)
    except TreeError as error:
        raise RenderError(str(error)) from error
    return normalized[field]


def require_absent_args_unset(present: bool, **desired: object) -> None:
    """Reject desired-state kwargs when ``present=False`` (ARCHITECTURE §4).

    Schema-independent: callers may invoke this before the Version fact.
    """

    if present:
        return
    for name, value in desired.items():
        if value is not None:
            raise RenderError(f"{name} must be omitted when present=False")


def render_system_basics(
    schema: str,
    *,
    hostname: str | None = None,
    domain_name: str | None = None,
    name_servers: list[str] | None = None,
    search_domains: list[str] | None = None,
    time_zone: str | None = None,
) -> list[Scope]:
    """Render per-field ``system`` identity leaves as disjoint ``Scope`` values.

    ``None`` is unmanaged and omitted. A provided scalar becomes
    ``Exact([value])``. A provided list becomes ``Exact(list)``; ``[]`` is
    own-and-empty (``Absent`` at the leaf). All-``None`` is an error.
    """

    fields: dict[str, str | list[str] | None] = {
        "hostname": hostname,
        "domain_name": domain_name,
        "name_servers": name_servers,
        "search_domains": search_domains,
        "time_zone": time_zone,
    }
    if all(value is None for value in fields.values()):
        raise RenderError(
            "system_basics requires at least one of hostname, domain_name, "
            "name_servers, search_domains, time_zone"
        )

    try:
        table = _SYSTEM_BASICS_PATHS[schema]
    except KeyError as error:
        raise RenderError(f"unknown schema {schema!r}") from error

    scopes: list[Scope] = []
    for field, value in fields.items():
        if value is None:
            continue
        path = _validated_path(table[field])
        if field in _SYSTEM_BASICS_LISTS:
            if not isinstance(value, list):
                raise RenderError(f"{field} must be a list of strings")
            if value == []:
                scopes.append(Scope(path=path, intent=Absent()))
                continue
        elif field in _SYSTEM_BASICS_SCALARS:
            if not isinstance(value, str):
                raise RenderError(f"{field} must be a string")
        node = _validated_leaf(value, field=field, where=field)
        scopes.append(Scope(path=path, intent=Exact(node=node)))
    return scopes


def render_interface(
    schema: str,
    interface: str,
    interface_type: str,
    *,
    addresses: list[str] | None = None,
    description: str | None = None,
    mtu: str | int | None = None,
    disabled: bool | None = None,
    values: dict[str, object] | None = None,
    present: bool = True,
) -> list[Scope]:
    """Render one ``interfaces <type> <name>`` node as disjoint ``Scope`` values.

    Per-field ownership: ``None`` is unmanaged. ``addresses=[]`` is
    own-and-empty (``Absent`` at the address leaf). ``disabled`` is a
    tri-state (``True`` → presence node, ``False`` → ``Absent``, ``None``
    omitted). All typed args ``None`` and no ``values`` ensures a bare
    interface via ``Merge({})``. ``present=False`` is a single ``Absent``
    at the interface path; every desired-state argument must be unset.
    """

    try:
        allowed_types = _INTERFACE_TYPES[schema]
    except KeyError as error:
        raise RenderError(f"unknown schema {schema!r}") from error
    if interface_type not in allowed_types:
        allowed = ", ".join(sorted(allowed_types))
        raise RenderError(f"unknown interface_type {interface_type!r}; allowed types: {allowed}")

    desired_args: dict[str, object] = {
        "addresses": addresses,
        "description": description,
        "mtu": mtu,
        "disabled": disabled,
        "values": values,
    }
    require_absent_args_unset(present, **desired_args)

    if not isinstance(interface, str):
        raise RenderError("interface must be a string")
    _validated_leaf(interface, field="interface", where="interface")
    path = _validated_path(["interfaces", interface_type, interface])

    if not present:
        return [Scope(path=path, intent=Absent())]

    if all(value is None for value in desired_args.values()):
        return [Scope(path=path, intent=Merge({}))]

    scopes: list[Scope] = []
    if addresses is not None:
        address_path = _validated_path([*path, "address"])
        if not isinstance(addresses, list):
            raise RenderError("addresses must be a list of strings")
        if addresses == []:
            scopes.append(Scope(path=address_path, intent=Absent()))
        else:
            node = _validated_leaf(addresses, field="addresses", where="addresses")
            scopes.append(Scope(path=address_path, intent=Exact(node=node)))
    if description is not None:
        if not isinstance(description, str):
            raise RenderError("description must be a string")
        node = _validated_leaf(description, field="description", where="description")
        scopes.append(Scope(path=_validated_path([*path, "description"]), intent=Exact(node=node)))
    if mtu is not None:
        token = coerce_token(mtu)
        node = _validated_leaf(token, field="mtu", where="mtu")
        scopes.append(Scope(path=_validated_path([*path, "mtu"]), intent=Exact(node=node)))
    if disabled is not None:
        if not isinstance(disabled, bool):
            raise RenderError("disabled must be a bool")
        disable_path = _validated_path([*path, "disable"])
        if disabled:
            scopes.append(Scope(path=disable_path, intent=Exact(node={})))
        else:
            scopes.append(Scope(path=disable_path, intent=Absent()))
    if values is not None:
        if isinstance(values, dict):
            for key in values:
                if key in _INTERFACE_TYPED_KEYS:
                    typed = _INTERFACE_TYPED_KEYS[key]
                    raise RenderError(
                        f"values key {key!r} collides with the typed {typed} argument"
                    )
        try:
            subtree = normalize_tree(values, strict=True, _where="values")
        except TreeError as error:
            raise RenderError(str(error)) from error
        scopes.append(Scope(path=path, intent=Merge(subtree=subtree)))
    return scopes


def parse_route_destination(
    destination: str,
) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """Parse *destination* as a network; host bits and garbage are errors.

    An explicit prefix length is required: a bare host address is rejected
    with the ``/32`` (or ``/128``) form named, because the caller string is
    used verbatim as the device path token and a prefix-less token cannot
    round-trip against the active tree.

    Schema-independent: callers may hoist this before the Version fact (A1).
    ``render_static_route`` still owns authoritative AF dispatch.
    """

    # strict=True (the ipaddress default): reject prefixes with host bits set.
    # Caller states intent exactly (plan 4.1 recommendation).
    if not isinstance(destination, str):
        raise RenderError("destination must be a string")
    try:
        network = ipaddress.ip_network(destination, strict=True)
    except ValueError as error:
        try:
            relaxed = ipaddress.ip_network(destination, strict=False)
        except ValueError:
            raise RenderError(f"invalid destination {destination!r}") from error
        raise RenderError(
            f"destination {destination!r} has host bits set; use the network form {relaxed!s}"
        ) from error
    if "/" not in destination:
        raise RenderError(
            f"destination {destination!r} has no prefix length; use the network form {network!s}"
        )
    return network


def _next_hops_wrapper(next_hops: object) -> dict[str, object]:
    """Wrap typed ``next_hops`` as a ``next-hop`` mapping for normalize_tree."""

    if isinstance(next_hops, list):
        if not all(isinstance(addr, str) for addr in next_hops):
            raise RenderError("next_hops must be a list of strings")
        return {"next-hop": {addr: {} for addr in next_hops}}
    if isinstance(next_hops, dict):
        for subtree in next_hops.values():
            if not isinstance(subtree, dict):
                raise RenderError("next_hops mapping values must be dicts")
        return {"next-hop": next_hops}
    raise RenderError("next_hops must be a list of addresses or a mapping of address to subtree")


def render_static_route(
    schema: str,
    destination: str,
    *,
    next_hops: list[str] | dict[str, dict[str, object]] | None = None,
    values: dict[str, object] | None = None,
    present: bool = True,
) -> list[Scope]:
    """Render one ``protocols static route[6] <dest>`` node as a single ``Scope``.

    Address-family dispatch uses ``ipaddress.ip_network(destination)`` with
    ``strict=True``: a prefix with host bits set is rejected, and so is a
    destination with no prefix length at all, so the caller states intent
    exactly. The original caller string is the path token; the device is
    the canonicalization authority.

    ``next_hops`` as ``list[str]`` becomes ``{"next-hop": {addr: {}}}``;
    as ``dict[str, dict]`` the per-hop subtrees are preserved. Addresses
    are token-validated only (C2); next-hop interface forms ride in
    ``values``. Integer per-hop attributes such as ``distance`` must be
    strings — the merged body is normalized strict, not coerced.

    Merged with ``values``. A top-level ``values["next-hop"]`` collides
    with a provided ``next_hops`` argument; when ``next_hops`` is
    ``None``, ``values`` may carry ``next-hop`` itself. ``present=True``
    requires a nonempty body (nonempty ``next_hops`` or nonempty
    ``values``); a bare route object is never commit-valid.
    ``present=False`` is a single ``Absent`` at the route path; every
    desired-state argument must be unset.
    """

    try:
        table = _STATIC_ROUTE_PATHS[schema]
    except KeyError as error:
        raise RenderError(f"unknown schema {schema!r}") from error

    require_absent_args_unset(present, next_hops=next_hops, values=values)

    network = parse_route_destination(destination)
    # Use the original caller string as the path token. Do not substitute
    # the parsed/normalized form — the device is the canonicalization authority.
    path = _validated_path(["protocols", "static", table[network.version], destination])

    if not present:
        return [Scope(path=path, intent=Absent())]

    if not next_hops and not values:
        raise RenderError(
            "static_route requires a nonempty next_hops or values; "
            "a bare route object is never commit-valid"
        )

    body: dict[str, Node] = {}
    if next_hops:
        try:
            body.update(
                normalize_tree(_next_hops_wrapper(next_hops), strict=True, _where="next_hops")
            )
        except TreeError as error:
            raise RenderError(str(error)) from error
    if values is not None:
        if isinstance(values, dict) and "next-hop" in values and next_hops is not None:
            raise RenderError("values key 'next-hop' collides with the typed next_hops argument")
        try:
            body.update(normalize_tree(values, strict=True, _where="values"))
        except TreeError as error:
            raise RenderError(str(error)) from error
    return [Scope(path=path, intent=Exact(node=body))]
