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
    render_system_basics,
    schema_key,
)


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
