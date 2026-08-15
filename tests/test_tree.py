from __future__ import annotations

import pytest

from pyinfra_vyos._tree import (
    TreeError,
    diff_tree,
    normalize_tree,
    select_subtree,
    validate_path,
)

_PATH = ["service", "ntp"]


# --- validate_path -----------------------------------------------------------


def test_validate_path_returns_tokens() -> None:
    assert validate_path(("system", "host-name")) == ["system", "host-name"]


@pytest.mark.parametrize(
    "path",
    ["system", [], None, ["system", ""], ["system", 7], ["--force"], ["system", "-x"]],
)
def test_validate_path_rejects_bad_input(path: object) -> None:
    with pytest.raises(TreeError):
        validate_path(path)


# --- normalize_tree ----------------------------------------------------------


def test_normalize_strict_canonicalizes_leaf_shapes() -> None:
    normalized = normalize_tree(
        {"single": "v", "multi": ["a", "b"], "node": {}, "sub": {"leaf": "x"}},
        strict=True,
    )

    assert normalized == {
        "single": ["v"],
        "multi": ["a", "b"],
        "node": {},
        "sub": {"leaf": ["x"]},
    }


@pytest.mark.parametrize(
    "values",
    [
        "not-a-dict",
        {"key": 7},
        {"key": True},
        {"key": None},
        {"key": ""},
        {"key": ["ok", 7]},
        {"": "v"},
        {7: "v"},
        {"-dash": "v"},
        {"key": "-dash"},
        {"sub": {"key": ["-dash"]}},
    ],
)
def test_normalize_strict_rejects_non_argv_safe_input(values: object) -> None:
    with pytest.raises(TreeError):
        normalize_tree(values, strict=True)


def test_normalize_lenient_coerces_device_scalars() -> None:
    normalized = normalize_tree({"mtu": 1500, "flags": [1, "b"], 7: "x"}, strict=False)

    assert normalized == {"mtu": ["1500"], "flags": ["1", "b"], "7": ["x"]}


# --- select_subtree ----------------------------------------------------------

_CONFIG = {
    "system": {
        "host-name": "r1",
        "static-host-mapping": {"host-name": {"a.test": {"inet": ["192.0.2.1"]}}},
    }
}


def test_select_returns_normalized_subtree() -> None:
    subtree = select_subtree(_CONFIG, ["system", "static-host-mapping"])

    assert subtree == {"host-name": {"a.test": {"inet": ["192.0.2.1"]}}}


def test_select_absent_path_returns_none() -> None:
    assert select_subtree(_CONFIG, ["service", "ntp"]) is None
    assert select_subtree(_CONFIG, ["system", "host-name", "deeper"]) is None


def test_select_leaf_at_exact_path_reports_present_as_empty_subtree() -> None:
    assert select_subtree(_CONFIG, ["system", "host-name"]) == {}


# --- diff_tree: merge --------------------------------------------------------


def test_equal_trees_produce_an_empty_delta() -> None:
    desired = {"server": {"a.test": {}}, "listen-address": ["192.0.2.9"]}

    assert diff_tree(dict(desired), desired, _PATH, replace=False) == ([], [])


def test_merge_sets_missing_and_differing_leaves_only() -> None:
    active = {"host": ["old"], "keep": ["k"]}
    desired = {"host": ["new"], "added": {"leaf": ["v"]}}

    deletes, sets = diff_tree(active, desired, _PATH, replace=False)

    assert deletes == []
    assert sets == [[*_PATH, "host", "new"], [*_PATH, "added", "leaf", "v"]]


def test_merge_adds_missing_multi_values_and_keeps_extras() -> None:
    active = {"address": ["192.0.2.1/32", "192.0.2.2/32"]}
    desired = {"address": ["192.0.2.2/32", "192.0.2.3/32"]}

    deletes, sets = diff_tree(active, desired, _PATH, replace=False)

    assert deletes == []
    assert sets == [[*_PATH, "address", "192.0.2.3/32"]]


def test_merge_never_touches_unmanaged_active_keys() -> None:
    active = {"unmanaged": {"deep": ["x"]}}

    deletes, sets = diff_tree(active, {"mine": ["v"]}, _PATH, replace=False)

    assert deletes == []
    assert sets == [[*_PATH, "mine", "v"]]


def test_multi_value_order_is_not_managed() -> None:
    active = {"address": ["a", "b"]}
    desired = {"address": ["b", "a"]}

    assert diff_tree(active, desired, _PATH, replace=True) == ([], [])


# --- diff_tree: replace ------------------------------------------------------


def test_replace_deletes_extra_active_keys() -> None:
    active = {"mine": ["v"], "extra": {"deep": ["x"]}}

    deletes, sets = diff_tree(active, {"mine": ["v"]}, _PATH, replace=True)

    assert deletes == [[*_PATH, "extra"]]
    assert sets == []


def test_replace_swaps_a_leaf_value_via_delete_then_set() -> None:
    deletes, sets = diff_tree({"host": ["old"]}, {"host": ["new"]}, _PATH, replace=True)

    assert deletes == [[*_PATH, "host", "old"]]
    assert sets == [[*_PATH, "host", "new"]]


def test_replace_prunes_extra_multi_values() -> None:
    active = {"address": ["a", "b", "c"]}

    deletes, sets = diff_tree(active, {"address": ["b"]}, _PATH, replace=True)

    assert deletes == [[*_PATH, "address", "a"], [*_PATH, "address", "c"]]
    assert sets == []


def test_replace_clears_a_leaf_where_a_subtree_is_desired() -> None:
    deletes, sets = diff_tree({"node": ["leaf"]}, {"node": {"child": ["v"]}}, _PATH, replace=True)

    assert deletes == [[*_PATH, "node"]]
    assert sets == [[*_PATH, "node", "child", "v"]]


def test_replace_clears_a_subtree_where_a_leaf_is_desired() -> None:
    deletes, sets = diff_tree({"node": {"child": ["v"]}}, {"node": ["leaf"]}, _PATH, replace=True)

    assert deletes == [[*_PATH, "node"]]
    assert sets == [[*_PATH, "node", "leaf"]]


# --- diff_tree: node presence ------------------------------------------------


def test_absent_path_with_empty_desired_creates_the_bare_node() -> None:
    assert diff_tree(None, {}, _PATH, replace=False) == ([], [[*_PATH]])


def test_present_path_with_empty_desired_is_a_noop_in_merge() -> None:
    assert diff_tree({}, {}, _PATH, replace=False) == ([], [])


def test_present_path_with_empty_desired_clears_children_in_replace() -> None:
    deletes, sets = diff_tree({"a": ["1"], "b": {}}, {}, _PATH, replace=True)

    assert deletes == [[*_PATH, "a"], [*_PATH, "b"]]
    assert sets == []


def test_valueless_desired_node_is_created_once() -> None:
    deletes, sets = diff_tree({}, {"enable": {}}, _PATH, replace=False)

    assert deletes == []
    assert sets == [[*_PATH, "enable"]]

    assert diff_tree({"enable": {}}, {"enable": {}}, _PATH, replace=False) == ([], [])
