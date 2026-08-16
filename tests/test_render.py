from __future__ import annotations

from collections.abc import Sequence
from dataclasses import FrozenInstanceError

import pytest

from pyinfra_vyos._render import (
    Absent,
    Exact,
    Merge,
    RenderError,
    Scope,
    coerce_token,
    parse_route_destination,
    render_interface,
    render_static_route,
    render_system_basics,
    render_user,
    require_absent_args_unset,
    schema_key,
)
from pyinfra_vyos._tree import diff_tree, select_subtree


def _is_prefix(left: list[str], right: list[str]) -> bool:
    """Return True if *left* equals *right* or is a prefix of it."""

    return len(left) <= len(right) and right[: len(left)] == left


def assert_disjoint(scopes: Sequence[Scope]) -> None:
    """Assert the architecture §3 disjointness invariant.

    Importable by later phases' renderer tests::

        from test_render import assert_disjoint

    (a) Exact/Absent paths are pairwise non-prefix (equal or prefix overlap).
    (b) At most one Merge, at the resource root; its top-level keys are
        disjoint from the next path token of every Exact/Absent beneath it.
    """

    owned: list[Scope] = []
    merge: Scope | None = None
    for scope in scopes:
        if isinstance(scope.intent, Merge):
            if merge is not None:
                raise AssertionError("at most one Merge scope is allowed")
            merge = scope
        else:
            owned.append(scope)

    for index, left in enumerate(owned):
        for right in owned[index + 1 :]:
            if _is_prefix(left.path, right.path) or _is_prefix(right.path, left.path):
                raise AssertionError(
                    f"Exact/Absent paths overlap: {left.path} and {right.path}",
                )

    if merge is None:
        return

    assert isinstance(merge.intent, Merge)
    merge_keys = merge.intent.subtree
    for scope in owned:
        if _is_prefix(scope.path, merge.path):
            raise AssertionError(
                f"Exact/Absent path {scope.path} overlaps Merge root {merge.path}",
            )
        if not _is_prefix(merge.path, scope.path):
            # Outside the Merge root: no shared ownership, nothing to check.
            continue
        next_token = scope.path[len(merge.path)]
        if next_token in merge_keys:
            raise AssertionError(
                f"Merge key {next_token!r} collides with Exact/Absent path {scope.path}",
            )


# --- schema_key --------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("VyOS 1.4.0", "1.4"),
        ("1.4.2", "1.4"),
        ("VyOS 1.5.0", "1.5"),
        ("VyOS 2026.03", "1.5"),
        ("2026.03.1", "1.5"),
    ],
)
def test_schema_key_maps_supported_versions(version: str, expected: str) -> None:
    assert schema_key(version) == expected


@pytest.mark.parametrize(
    "version",
    [
        "VyOS 1.4-rolling-202106270801",
        "VyOS 1.5-rolling-202403010000",
        "circinus",
        "stream",
        "2027.01",
        "2027.01.1",
        "9999.99.1",
        "2026.0x.1",
        "abcdefg.5",
        "",
        "garbage",
    ],
)
def test_schema_key_fails_closed_on_unqualified_versions(version: str) -> None:
    with pytest.raises(RenderError) as caught:
        schema_key(version)

    message = str(caught.value)
    assert version in message
    assert "config" in message


# --- Scope / intents ---------------------------------------------------------


def test_scope_defaults_sensitive_false_and_is_frozen() -> None:
    scope = Scope(path=["system", "host-name"], intent=Exact(node=["gw"]))

    assert scope.sensitive is False
    with pytest.raises(FrozenInstanceError):
        scope.sensitive = True
    with pytest.raises(FrozenInstanceError):
        scope.path = ["other"]
    with pytest.raises(FrozenInstanceError):
        scope.intent = Absent()


def test_intent_dataclasses_are_frozen() -> None:
    exact = Exact(node=["v"])
    merge = Merge(subtree={"k": ["v"]})

    with pytest.raises(FrozenInstanceError):
        exact.node = ["w"]
    with pytest.raises(FrozenInstanceError):
        merge.subtree = {}
    with pytest.raises(FrozenInstanceError):
        Absent().path = []  # Absent is parameterless; any assignment is frozen


def test_scope_sensitive_can_be_set_true() -> None:
    scope = Scope(path=["system"], intent=Absent(), sensitive=True)

    assert scope.sensitive is True


# --- coerce_token ------------------------------------------------------------


def test_coerce_token_int_and_str() -> None:
    assert coerce_token(1500) == "1500"
    assert coerce_token("eth0") == "eth0"


@pytest.mark.parametrize("value", [True, False])
def test_coerce_token_rejects_bool(value: bool) -> None:
    with pytest.raises(RenderError):
        coerce_token(value)


# --- require_absent_args_unset -----------------------------------------------


def test_require_absent_args_unset_raises_naming_the_arg() -> None:
    with pytest.raises(RenderError) as caught:
        require_absent_args_unset(False, addresses=["192.0.2.1/32"], description=None)

    message = str(caught.value)
    assert "addresses" in message
    assert "present=False" in message


def test_require_absent_args_unset_passes_when_all_none() -> None:
    require_absent_args_unset(
        False,
        addresses=None,
        description=None,
        mtu=None,
        disabled=None,
        values=None,
    )


# --- assert_disjoint ---------------------------------------------------------


def test_assert_disjoint_rejects_overlapping_paths() -> None:
    with pytest.raises(AssertionError):
        assert_disjoint(
            [
                Scope(["system", "host-name"], Exact(["a"])),
                Scope(["system", "host-name"], Exact(["b"])),
            ],
        )
    with pytest.raises(AssertionError):
        assert_disjoint(
            [
                Scope(["system"], Absent()),
                Scope(["system", "host-name"], Exact(["a"])),
            ],
        )


def test_assert_disjoint_accepts_disjoint_paths() -> None:
    assert_disjoint(
        [
            Scope(["system", "host-name"], Exact(["a"])),
            Scope(["system", "domain-name"], Exact(["b"])),
            Scope(["system", "time-zone"], Absent()),
        ],
    )


def test_assert_disjoint_accepts_authentication_sibling_paths() -> None:
    """Nested authentication leaves are siblings, not prefixes.

    Round-1 counterexample class: ``encrypted-password`` and ``public-keys``
    share the ``authentication`` parent but neither path prefixes the other.
    """

    root = ["system", "login", "user", "alice"]
    assert_disjoint(
        [
            Scope([*root, "full-name"], Exact(["Alice"])),
            Scope([*root, "authentication", "encrypted-password"], Exact(["$6$x"])),
            Scope([*root, "authentication", "public-keys"], Exact({"k": {}})),
        ],
    )


def test_assert_disjoint_rejects_merge_key_collision() -> None:
    with pytest.raises(AssertionError):
        assert_disjoint(
            [
                Scope(
                    ["interfaces", "ethernet", "eth0"],
                    Merge({"address": ["192.0.2.1/24"]}),
                ),
                Scope(
                    ["interfaces", "ethernet", "eth0", "address"],
                    Exact(["192.0.2.1/24"]),
                ),
            ],
        )


def test_assert_disjoint_accepts_merge_with_disjoint_keys() -> None:
    assert_disjoint(
        [
            Scope(
                ["interfaces", "ethernet", "eth0"],
                Merge({"hw-id": ["aa:bb:cc:dd:ee:ff"]}),
            ),
            Scope(
                ["interfaces", "ethernet", "eth0", "address"],
                Exact(["192.0.2.1/24"]),
            ),
            Scope(
                ["interfaces", "ethernet", "eth0", "description"],
                Exact(["uplink"]),
            ),
        ],
    )


def test_assert_disjoint_rejects_two_merges() -> None:
    with pytest.raises(AssertionError):
        assert_disjoint(
            [
                Scope(["interfaces", "ethernet", "eth0"], Merge({"hw-id": ["aa"]})),
                Scope(["interfaces", "ethernet", "eth1"], Merge({"hw-id": ["bb"]})),
            ],
        )


def test_assert_disjoint_accepts_merge_plus_unrelated_exact() -> None:
    assert_disjoint(
        [
            Scope(
                ["interfaces", "ethernet", "eth0"],
                Merge({"hw-id": ["aa:bb:cc:dd:ee:ff"]}),
            ),
            Scope(["system", "host-name"], Exact(["gw"])),
        ],
    )


# --- render_system_basics ----------------------------------------------------


_R2_SYSTEM_BASICS_PATHS = {
    "hostname": ["system", "host-name"],
    "domain_name": ["system", "domain-name"],
    "name_servers": ["system", "name-server"],
    "search_domains": ["system", "domain-search"],
    "time_zone": ["system", "time-zone"],
}


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"hostname": "gw"}, "hostname"),
        ({"domain_name": "example.net"}, "domain_name"),
        ({"name_servers": ["8.8.8.8", "1.1.1.1"]}, "name_servers"),
        ({"search_domains": ["a.net", "b.net"]}, "search_domains"),
        ({"time_zone": "UTC"}, "time_zone"),
    ],
)
def test_render_system_basics_emits_r2_leaf_paths(
    schema: str, kwargs: dict[str, object], field: str
) -> None:
    scopes = render_system_basics(schema, **kwargs)  # type: ignore[arg-type]
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == _R2_SYSTEM_BASICS_PATHS[field]
    value = next(iter(kwargs.values()))
    assert isinstance(scopes[0].intent, Exact)
    if isinstance(value, str):
        assert scopes[0].intent.node == [value]
    else:
        assert scopes[0].intent.node == value
    assert scopes[0].sensitive is False


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_system_basics_all_provided_fields_are_disjoint(schema: str) -> None:
    scopes = render_system_basics(
        schema,
        hostname="gw",
        domain_name="example.net",
        name_servers=["8.8.8.8"],
        search_domains=["example.net"],
        time_zone="UTC",
    )
    assert_disjoint(scopes)
    assert [scope.path for scope in scopes] == [
        ["system", "host-name"],
        ["system", "domain-name"],
        ["system", "name-server"],
        ["system", "domain-search"],
        ["system", "time-zone"],
    ]
    assert [scope.intent for scope in scopes] == [
        Exact(["gw"]),
        Exact(["example.net"]),
        Exact(["8.8.8.8"]),
        Exact(["example.net"]),
        Exact(["UTC"]),
    ]


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"name_servers": []}, "name_servers"),
        ({"search_domains": []}, "search_domains"),
    ],
)
def test_render_system_basics_empty_list_is_absent_at_leaf(
    schema: str, kwargs: dict[str, list[str]], field: str
) -> None:
    scopes = render_system_basics(schema, **kwargs)
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == _R2_SYSTEM_BASICS_PATHS[field]
    assert isinstance(scopes[0].intent, Absent)


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_system_basics_mixes_exact_and_absent(schema: str) -> None:
    scopes = render_system_basics(schema, hostname="gw", name_servers=[])
    assert_disjoint(scopes)
    assert scopes[0].path == ["system", "host-name"]
    assert scopes[0].intent == Exact(["gw"])
    assert scopes[1].path == ["system", "name-server"]
    assert isinstance(scopes[1].intent, Absent)


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_system_basics_all_none_raises(schema: str) -> None:
    with pytest.raises(RenderError):
        render_system_basics(schema)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"hostname": ""},
        {"hostname": "-gw"},
        {"hostname": 1},
        {"hostname": True},
        {"hostname": ["gw"]},
        {"domain_name": ""},
        {"domain_name": "-x"},
        {"domain_name": 1},
        {"time_zone": ""},
        {"time_zone": "-UTC"},
        {"time_zone": 1},
        {"name_servers": [""]},
        {"name_servers": ["-1.1.1.1"]},
        {"name_servers": [1]},
        {"name_servers": "8.8.8.8"},
        {"search_domains": [""]},
        {"search_domains": ["-example.net"]},
        {"search_domains": [1]},
        {"search_domains": "example.net"},
    ],
)
def test_render_system_basics_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(RenderError):
        render_system_basics("1.4", **kwargs)  # type: ignore[arg-type]


# --- render_interface --------------------------------------------------------


_R2_INTERFACE_TYPES = ("ethernet", "loopback", "dummy")


def _interface_path(interface_type: str, name: str = "eth0") -> list[str]:
    return ["interfaces", interface_type, name]


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
@pytest.mark.parametrize(
    ("kwargs", "suffix", "intent"),
    [
        ({"addresses": ["192.0.2.1/24"]}, ["address"], Exact(["192.0.2.1/24"])),
        ({"description": "uplink"}, ["description"], Exact(["uplink"])),
        ({"mtu": "1500"}, ["mtu"], Exact(["1500"])),
        ({"mtu": 1500}, ["mtu"], Exact(["1500"])),
        ({"disabled": True}, ["disable"], Exact({})),
        ({"disabled": False}, ["disable"], Absent()),
        (
            {"values": {"hw-id": "aa:bb:cc:dd:ee:ff"}},
            [],
            Merge({"hw-id": ["aa:bb:cc:dd:ee:ff"]}),
        ),
    ],
)
def test_render_interface_emits_scope_per_kwarg(
    schema: str, kwargs: dict[str, object], suffix: list[str], intent: Exact | Absent | Merge
) -> None:
    scopes = render_interface(schema, "eth0", "ethernet", **kwargs)  # type: ignore[arg-type]
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == [*_interface_path("ethernet"), *suffix]
    assert scopes[0].intent == intent
    assert scopes[0].sensitive is False


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_interface_empty_addresses_is_absent_at_leaf(schema: str) -> None:
    scopes = render_interface(schema, "eth0", "ethernet", addresses=[])
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == [*_interface_path("ethernet"), "address"]
    assert isinstance(scopes[0].intent, Absent)


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_interface_disabled_none_omits_disable_scope(schema: str) -> None:
    scopes = render_interface(schema, "eth0", "ethernet", addresses=["192.0.2.1/24"], disabled=None)
    assert_disjoint(scopes)
    assert [scope.path for scope in scopes] == [[*_interface_path("ethernet"), "address"]]


@pytest.mark.parametrize("value", [True, False])
def test_render_interface_rejects_bool_mtu(value: bool) -> None:
    with pytest.raises(RenderError):
        render_interface("1.4", "eth0", "ethernet", mtu=value)


@pytest.mark.parametrize(
    ("key", "typed"),
    [
        ("address", "addresses"),
        ("description", "description"),
        ("mtu", "mtu"),
        ("disable", "disabled"),
    ],
)
def test_render_interface_rejects_typed_key_collision(key: str, typed: str) -> None:
    with pytest.raises(RenderError) as caught:
        render_interface("1.4", "eth0", "ethernet", values={key: ["x"]})

    message = str(caught.value)
    assert key in message
    assert typed in message


def test_render_interface_nested_values_keys_are_not_collisions() -> None:
    scopes = render_interface(
        "1.4",
        "eth0",
        "ethernet",
        values={"vif": {"10": {"address": ["192.0.2.1/24"]}}},
    )
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == _interface_path("ethernet")
    assert isinstance(scopes[0].intent, Merge)
    assert "vif" in scopes[0].intent.subtree


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
@pytest.mark.parametrize("disabled", [True, False])
def test_render_interface_full_kwarg_matrix_is_disjoint(schema: str, disabled: bool) -> None:
    scopes = render_interface(
        schema,
        "eth0",
        "ethernet",
        addresses=["192.0.2.1/24"],
        description="uplink",
        mtu=1500,
        disabled=disabled,
        values={"hw-id": "aa:bb:cc:dd:ee:ff"},
    )
    assert_disjoint(scopes)
    disable_intent: Exact | Absent = Exact({}) if disabled else Absent()
    assert [scope.path for scope in scopes] == [
        [*_interface_path("ethernet"), "address"],
        [*_interface_path("ethernet"), "description"],
        [*_interface_path("ethernet"), "mtu"],
        [*_interface_path("ethernet"), "disable"],
        _interface_path("ethernet"),
    ]
    assert [scope.intent for scope in scopes] == [
        Exact(["192.0.2.1/24"]),
        Exact(["uplink"]),
        Exact(["1500"]),
        disable_intent,
        Merge({"hw-id": ["aa:bb:cc:dd:ee:ff"]}),
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"addresses": ["192.0.2.1/24"]},
        {"description": "uplink"},
        {"mtu": 1500},
        {"disabled": True},
        {"disabled": False},
        {"values": {"hw-id": "aa:bb:cc:dd:ee:ff"}},
    ],
)
def test_render_interface_present_false_rejects_desired_args(kwargs: dict[str, object]) -> None:
    with pytest.raises(RenderError) as caught:
        render_interface("1.4", "eth0", "ethernet", present=False, **kwargs)  # type: ignore[arg-type]

    name = next(iter(kwargs))
    assert name in str(caught.value)


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_interface_present_false_alone_is_absent(schema: str) -> None:
    scopes = render_interface(schema, "eth0", "ethernet", present=False)
    assert_disjoint(scopes)
    assert scopes == [Scope(_interface_path("ethernet"), Absent())]


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_interface_unknown_type_names_allowed_types(schema: str) -> None:
    with pytest.raises(RenderError) as caught:
        render_interface(schema, "eth0", "bridge")

    message = str(caught.value)
    assert "bridge" in message
    for allowed in _R2_INTERFACE_TYPES:
        assert allowed in message


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
@pytest.mark.parametrize("interface_type", _R2_INTERFACE_TYPES)
def test_render_interface_emits_r2_path_tokens(schema: str, interface_type: str) -> None:
    scopes = render_interface(schema, "eth0", interface_type)
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == ["interfaces", interface_type, "eth0"]
    assert scopes[0].intent == Merge({})


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_interface_all_none_is_bare_merge(schema: str) -> None:
    scopes = render_interface(schema, "eth0", "ethernet")
    assert_disjoint(scopes)
    assert scopes == [Scope(_interface_path("ethernet"), Merge({}))]


# --- render_static_route -----------------------------------------------------


def _static_route_path(destination: str, *, ipv6: bool = False) -> list[str]:
    leaf = "route6" if ipv6 else "route"
    return ["protocols", "static", leaf, destination]


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_static_route_ipv4_dispatches_to_route(schema: str) -> None:
    scopes = render_static_route(schema, "192.0.2.0/24", next_hops=["192.0.2.1"])
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == _static_route_path("192.0.2.0/24")
    assert scopes[0].intent == Exact({"next-hop": {"192.0.2.1": {}}})
    assert scopes[0].sensitive is False


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_static_route_ipv6_dispatches_to_route6(schema: str) -> None:
    scopes = render_static_route(schema, "2001:db8::/64", next_hops=["2001:db8::1"])
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == _static_route_path("2001:db8::/64", ipv6=True)
    assert scopes[0].intent == Exact({"next-hop": {"2001:db8::1": {}}})


def test_parse_route_destination_accepts_networks() -> None:
    import ipaddress

    assert parse_route_destination("192.0.2.0/24") == ipaddress.ip_network("192.0.2.0/24")
    assert parse_route_destination("2001:db8::/64") == ipaddress.ip_network("2001:db8::/64")
    assert parse_route_destination("192.0.2.5/32") == ipaddress.ip_network("192.0.2.5/32")
    # Expanded/uppercase v6 still has a prefix and all-zero host bits; the
    # original caller string stays the path token in the renderer.
    assert parse_route_destination(
        "2001:0DB8:0000:0001:0000:0000:0000:0000/64"
    ) == ipaddress.ip_network("2001:db8:0:1::/64")


def test_parse_route_destination_rejects_a_missing_prefix_length() -> None:
    # The caller string is the device path token, so a prefix-less form could
    # never round-trip against the active tree. Reject it, naming the form.
    for destination, network in (("192.0.2.5", "192.0.2.5/32"), ("2001:db8::1", "2001:db8::1/128")):
        with pytest.raises(RenderError) as caught:
            parse_route_destination(destination)
        message = str(caught.value)
        assert destination in message
        assert network in message


def test_parse_route_destination_rejects_host_bits_and_garbage() -> None:
    with pytest.raises(RenderError) as caught:
        parse_route_destination("192.0.2.1/24")
    message = str(caught.value)
    assert "192.0.2.1/24" in message
    assert "192.0.2.0/24" in message

    with pytest.raises(RenderError) as caught:
        parse_route_destination("garbage")
    assert "garbage" in str(caught.value)


def test_render_static_route_host_bits_rejected() -> None:
    with pytest.raises(RenderError) as caught:
        render_static_route("1.4", "192.0.2.1/24")

    message = str(caught.value)
    assert "192.0.2.1/24" in message
    assert "192.0.2.0/24" in message


def test_render_static_route_garbage_destination_rejected() -> None:
    with pytest.raises(RenderError) as caught:
        render_static_route("1.4", "garbage")

    assert "garbage" in str(caught.value)


def test_render_static_route_bare_host_rejected() -> None:
    # A prefix-less token cannot round-trip: present=False would plan nothing
    # and noop while the route is still active. Reject at planning instead.
    with pytest.raises(RenderError) as caught:
        render_static_route("1.4", "192.0.2.5", next_hops=["192.0.2.1"])

    message = str(caught.value)
    assert "192.0.2.5" in message
    assert "192.0.2.5/32" in message


def test_render_static_route_explicit_host_prefix_accepted() -> None:
    scopes = render_static_route("1.4", "192.0.2.5/32", next_hops=["192.0.2.1"])
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == _static_route_path("192.0.2.5/32")
    assert scopes[0].intent == Exact({"next-hop": {"192.0.2.1": {}}})


def test_render_static_route_list_and_dict_next_hops_are_equivalent() -> None:
    from_list = render_static_route("1.4", "192.0.2.0/24", next_hops=["192.0.2.1", "192.0.2.2"])
    from_dict = render_static_route(
        "1.4",
        "192.0.2.0/24",
        next_hops={"192.0.2.1": {}, "192.0.2.2": {}},
    )
    assert_disjoint(from_list)
    assert_disjoint(from_dict)
    assert from_list == from_dict
    assert from_list[0].intent == Exact({"next-hop": {"192.0.2.1": {}, "192.0.2.2": {}}})


def test_render_static_route_dict_preserves_per_hop_subtree() -> None:
    scopes = render_static_route(
        "1.4",
        "192.0.2.0/24",
        next_hops={"192.0.2.1": {"distance": "10"}},
    )
    assert_disjoint(scopes)
    assert scopes[0].intent == Exact({"next-hop": {"192.0.2.1": {"distance": ["10"]}}})


def test_render_static_route_rejects_next_hop_collision_naming_both() -> None:
    with pytest.raises(RenderError) as caught:
        render_static_route(
            "1.4",
            "192.0.2.0/24",
            next_hops=["192.0.2.1"],
            values={"next-hop": {"192.0.2.2": {}}},
        )

    message = str(caught.value)
    assert "next-hop" in message
    assert "next_hops" in message


def test_render_static_route_values_only_next_hop_accepted() -> None:
    scopes = render_static_route(
        "1.4",
        "192.0.2.0/24",
        values={"next-hop": {"192.0.2.1": {}}},
    )
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].intent == Exact({"next-hop": {"192.0.2.1": {}}})


def test_render_static_route_merges_next_hops_with_values() -> None:
    scopes = render_static_route(
        "1.4",
        "192.0.2.0/24",
        next_hops=["192.0.2.1"],
        values={"blackhole": {}},
    )
    assert_disjoint(scopes)
    assert scopes[0].intent == Exact({"next-hop": {"192.0.2.1": {}}, "blackhole": {}})


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"next_hops": [], "values": {}},
    ],
)
def test_render_static_route_empty_body_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(RenderError) as caught:
        render_static_route("1.4", "192.0.2.0/24", **kwargs)  # type: ignore[arg-type]

    assert "bare route" in str(caught.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"next_hops": ["192.0.2.1"]},
        {"values": {"blackhole": {}}},
    ],
)
def test_render_static_route_present_false_rejects_desired_args(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(RenderError) as caught:
        render_static_route("1.4", "192.0.2.0/24", present=False, **kwargs)  # type: ignore[arg-type]

    name = next(iter(kwargs))
    assert name in str(caught.value)


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_static_route_present_false_alone_is_absent(schema: str) -> None:
    scopes = render_static_route(schema, "192.0.2.0/24", present=False)
    assert_disjoint(scopes)
    assert scopes == [Scope(_static_route_path("192.0.2.0/24"), Absent())]


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
@pytest.mark.parametrize(
    ("destination", "ipv6", "hop"),
    [
        ("192.0.2.0/24", False, "192.0.2.1"),
        ("2001:db8::/64", True, "2001:db8::1"),
    ],
)
def test_render_static_route_emits_r2_path_tokens(
    schema: str, destination: str, ipv6: bool, hop: str
) -> None:
    scopes = render_static_route(schema, destination, next_hops=[hop])
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == _static_route_path(destination, ipv6=ipv6)
    assert isinstance(scopes[0].intent, Exact)


# --- render_user -------------------------------------------------------------


def _user_path(name: str = "alice") -> list[str]:
    return ["system", "login", "user", name]


# Accepted crypt forms and lock markers. These are synthetic (not live hashes).
_USER_ACCEPTED_HASHES = (
    "$6$rounds=656000$salt$digest",
    "$y$j9T$salt$hash",
    "$2b$12$saltandsalthashhashhashhashhu",
    "!",
    "*",
)


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
@pytest.mark.parametrize("value", _USER_ACCEPTED_HASHES)
def test_render_user_accepts_encrypted_password_forms(schema: str, value: str) -> None:
    scopes = render_user(schema, "alice", encrypted_password=value)
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == [*_user_path(), "authentication", "encrypted-password"]
    assert scopes[0].intent == Exact([value])
    assert scopes[0].sensitive is True


@pytest.mark.parametrize("value", ["hunter2", "", "!!", "*x"])
def test_render_user_rejects_encrypted_password_without_echoing(value: str) -> None:
    with pytest.raises(RenderError) as caught:
        render_user("1.4", "alice", encrypted_password=value)

    message = str(caught.value)
    # Redaction (D11): never interpolate the supplied value into the error.
    # Empty string is a substring of every message, so skip that case.
    if value:
        assert value not in message
    assert "encrypted_password" in message
    assert "$-prefixed" in message
    assert "mkpasswd" in message
    assert "passlib" in message


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_user_only_encrypted_password_scope_is_sensitive(schema: str) -> None:
    scopes = render_user(
        schema,
        "alice",
        full_name="Alice Admin",
        encrypted_password="$6$rounds=656000$salt$digest",
        ssh_keys={"laptop": {"type": "ssh-ed25519", "key": "AAAA"}},
    )
    assert_disjoint(scopes)
    by_suffix = {tuple(scope.path[len(_user_path()) :]): scope for scope in scopes}
    assert by_suffix[("full-name",)].sensitive is False
    assert by_suffix[("authentication", "encrypted-password")].sensitive is True
    assert by_suffix[("authentication", "public-keys")].sensitive is False


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_user_ssh_keys_exact_set_passes_nested_body_keys(schema: str) -> None:
    scopes = render_user(
        schema,
        "alice",
        ssh_keys={
            "laptop": {
                "type": "ssh-ed25519",
                "key": "AAAA",
                "options": "no-agent-forwarding",
            }
        },
    )
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == [*_user_path(), "authentication", "public-keys"]
    assert scopes[0].intent == Exact(
        {
            "laptop": {
                "type": ["ssh-ed25519"],
                "key": ["AAAA"],
                "options": ["no-agent-forwarding"],
            }
        }
    )
    assert scopes[0].sensitive is False


def test_render_user_ssh_keys_exact_set_removes_an_omitted_key() -> None:
    """An active key omitted from ssh_keys is deleted; declared keys stay."""

    active = {
        "system": {
            "login": {
                "user": {
                    "alice": {
                        "authentication": {
                            "public-keys": {
                                "laptop": {"type": "ssh-ed25519", "key": "AAAA"},
                                "stale": {"type": "ssh-rsa", "key": "BBBB"},
                            }
                        }
                    }
                }
            }
        }
    }
    scopes = render_user(
        "1.5",
        "alice",
        ssh_keys={"laptop": {"type": "ssh-ed25519", "key": "AAAA"}},
    )
    assert_disjoint(scopes)
    assert len(scopes) == 1
    scope = scopes[0]
    assert isinstance(scope.intent, Exact)
    deletes, sets = diff_tree(
        select_subtree(active, scope.path),
        scope.intent.node,
        scope.path,
        replace=True,
    )
    assert deletes == [[*scope.path, "stale"]]
    assert sets == []


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_user_empty_ssh_keys_is_absent_at_leaf(schema: str) -> None:
    scopes = render_user(schema, "alice", ssh_keys={})
    assert_disjoint(scopes)
    assert len(scopes) == 1
    assert scopes[0].path == [*_user_path(), "authentication", "public-keys"]
    assert isinstance(scopes[0].intent, Absent)
    assert scopes[0].sensitive is False


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_user_full_kwarg_matrix_is_disjoint(schema: str) -> None:
    # No values pass-through exists on this renderer; typed fields only.
    scopes = render_user(
        schema,
        "alice",
        full_name="Alice Admin",
        encrypted_password="$6$rounds=656000$salt$digest",
        ssh_keys={"laptop": {"type": "ssh-ed25519", "key": "AAAA"}},
    )
    assert_disjoint(scopes)
    paths = [scope.path for scope in scopes]
    assert paths == [
        [*_user_path(), "full-name"],
        [*_user_path(), "authentication", "encrypted-password"],
        [*_user_path(), "authentication", "public-keys"],
    ]
    encrypted = paths[1][len(_user_path()) :]
    public_keys = paths[2][len(_user_path()) :]
    assert encrypted == ["authentication", "encrypted-password"]
    assert public_keys == ["authentication", "public-keys"]
    assert not _is_prefix(encrypted, public_keys)
    assert not _is_prefix(public_keys, encrypted)


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_user_all_none_is_bare_merge(schema: str) -> None:
    scopes = render_user(schema, "alice")
    assert_disjoint(scopes)
    assert scopes == [Scope(_user_path(), Merge({}))]
    assert scopes[0].sensitive is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"full_name": "Alice"},
        {"encrypted_password": "$6$rounds=656000$salt$digest"},
        {"ssh_keys": {"laptop": {"type": "ssh-ed25519", "key": "AAAA"}}},
    ],
)
def test_render_user_present_false_rejects_desired_args(kwargs: dict[str, object]) -> None:
    with pytest.raises(RenderError) as caught:
        render_user("1.4", "alice", present=False, **kwargs)  # type: ignore[arg-type]

    name = next(iter(kwargs))
    assert name in str(caught.value)


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_user_present_false_alone_is_absent(schema: str) -> None:
    scopes = render_user(schema, "alice", present=False)
    assert_disjoint(scopes)
    assert scopes == [Scope(_user_path(), Absent())]
    assert scopes[0].sensitive is False


@pytest.mark.parametrize("schema", ["1.4", "1.5"])
def test_render_user_emits_r2_path_tokens(schema: str) -> None:
    scopes = render_user(
        schema,
        "alice",
        full_name="Alice Admin",
        encrypted_password="$6$rounds=656000$salt$digest",
        ssh_keys={"laptop": {"type": "ssh-ed25519", "key": "AAAA"}},
    )
    assert_disjoint(scopes)
    assert [scope.path for scope in scopes] == [
        ["system", "login", "user", "alice", "full-name"],
        ["system", "login", "user", "alice", "authentication", "encrypted-password"],
        ["system", "login", "user", "alice", "authentication", "public-keys"],
    ]
