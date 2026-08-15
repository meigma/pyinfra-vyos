"""Pure config-tree domain logic for the scoped ``config`` operation.

This module validates a caller's desired subtree, selects the matching
subtree from a ``show configuration json`` tree, and computes the
``set`` / ``delete`` command delta between them. It performs no I/O and
holds no pyinfra state (A2); ``operations.py`` turns the returned command
argv lists into a session script.

Security contract held here: every path component, key, and value that will
become device-side ``set`` / ``delete`` argv is validated to be a nonempty
string that does not begin with ``-`` (C2 — shell quoting stops the shell,
not the config wrapper's own argument handling), before any command text is
assembled.

Desired-value convention (mirrors ``show configuration json``):

- nested ``dict``  -> config subtree; ``{}`` is a valueless/presence node
- ``str``          -> single-value leaf
- ``list[str]``    -> multi-value leaf

Both desired and active trees normalize through the same function (leaf
values become ``list[str]``), so the diff is a uniform tree-to-tree walk.
Multi-value leaves compare as unordered sets; value ordering is not managed.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "TreeError",
    "diff_tree",
    "normalize_tree",
    "select_subtree",
    "validate_path",
]

# A normalized node: subtree mapping or leaf value list.
Node = dict[str, "Node"] | list[str]


class TreeError(ValueError):
    """Rejected desired-state input; translated to OperationValueError."""


def _require_token(token: object, *, what: str) -> str:
    """Validate one path key or leaf value destined for device argv."""

    if not isinstance(token, str):
        raise TreeError(f"{what} must be a string, got {type(token).__name__}: {token!r}")
    if not token:
        raise TreeError(f"{what} must be a nonempty string")
    if token.startswith("-"):
        raise TreeError(f"{what} must not begin with '-': {token!r}")
    return token


def validate_path(path: object) -> list[str]:
    """Validate the operation's ``path`` argument into config-path tokens."""

    if not isinstance(path, (list, tuple)) or not path:
        raise TreeError("path must be a nonempty list of config path tokens")
    return [_require_token(token, what="path token") for token in path]


def normalize_tree(values: object, *, strict: bool, _where: str = "values") -> dict[str, Node]:
    """Normalize a config tree into ``dict`` nodes with ``list[str]`` leaves.

    ``strict=True`` is the desired-state contract: keys and values must pass
    the argv token rules above. ``strict=False`` tolerates device output —
    non-string scalars are coerced with ``str()`` and token rules are not
    applied, because active-tree values never become command argv.
    """

    if not isinstance(values, dict):
        raise TreeError(f"{_where} must be a mapping, got {type(values).__name__}")

    normalized: dict[str, Node] = {}
    for raw_key, raw_value in values.items():
        if strict:
            key = _require_token(raw_key, what=f"{_where} key")
        else:
            key = raw_key if isinstance(raw_key, str) else str(raw_key)
        where = f"{_where}[{key!r}]"

        if isinstance(raw_value, dict):
            normalized[key] = normalize_tree(raw_value, strict=strict, _where=where)
        elif isinstance(raw_value, list):
            if strict:
                normalized[key] = [
                    _require_token(item, what=f"{where} value") for item in raw_value
                ]
            else:
                normalized[key] = [
                    item if isinstance(item, str) else str(item) for item in raw_value
                ]
        elif isinstance(raw_value, str):
            if strict:
                _require_token(raw_value, what=f"{where} value")
            normalized[key] = [raw_value]
        elif strict:
            raise TreeError(
                f"{where} must be a mapping, string, or list of strings, "
                f"got {type(raw_value).__name__}: {raw_value!r}"
            )
        else:
            normalized[key] = [str(raw_value)]
    return normalized


def select_subtree(config: dict[str, Any], path: list[str]) -> dict[str, Node] | None:
    """Select and normalize the subtree at *path* from an active config tree.

    Returns ``None`` when the path is absent. A leaf at the exact path is a
    shape mismatch the diff handles; it is returned as an empty subtree so
    the caller still sees "present".
    """

    node: Any = config
    for token in path:
        if not isinstance(node, dict) or token not in node:
            return None
        node = node[token]
    if isinstance(node, dict):
        return normalize_tree(node, strict=False, _where="active")
    return {}


def diff_tree(
    active: dict[str, Node] | None,
    desired: dict[str, Node],
    path: list[str],
    *,
    replace: bool,
) -> tuple[list[list[str]], list[list[str]]]:
    """Compute ``(delete_argvs, set_argvs)`` turning *active* into *desired*.

    Each argv is a full config path token list (without the ``set`` /
    ``delete`` word). Merge mode (``replace=False``) emits only sets; extra
    active state is unmanaged. Replace mode also deletes active keys and
    leaf values absent from *desired*. Deletes are ordered before sets so a
    single-value overwrite (delete old value, set new) is safe for both
    single- and multi-value nodes within one atomic session.
    """

    deletes: list[list[str]] = []
    sets: list[list[str]] = []
    _diff_node(active if active is not None else {}, desired, path, replace, deletes, sets)
    if not deletes and not sets and active is None:
        # Path itself absent and desired is empty: creating the bare node is
        # still a change (presence node).
        sets.append(list(path))
    return deletes, sets


def _diff_node(
    active: dict[str, Node],
    desired: dict[str, Node],
    prefix: list[str],
    replace: bool,
    deletes: list[list[str]],
    sets: list[list[str]],
) -> None:
    for key, desired_value in desired.items():
        child_prefix = [*prefix, key]
        active_value = active.get(key)

        if isinstance(desired_value, dict):
            if isinstance(active_value, dict):
                if not desired_value and not active_value:
                    continue
                _diff_node(active_value, desired_value, child_prefix, replace, deletes, sets)
            else:
                if active_value is not None and replace:
                    # Leaf where a subtree is desired: clear it first.
                    deletes.append(child_prefix)
                if desired_value:
                    _diff_node({}, desired_value, child_prefix, replace, deletes, sets)
                elif active_value is None:
                    sets.append(child_prefix)
        else:
            active_values = active_value if isinstance(active_value, list) else []
            if isinstance(active_value, dict) and replace:
                # Subtree where a leaf is desired: clear it first.
                deletes.append(child_prefix)
            for value in desired_value:
                if value not in active_values:
                    sets.append([*child_prefix, value])
            if replace:
                for value in active_values:
                    if value not in desired_value:
                        deletes.append([*child_prefix, value])

    if replace:
        for key in active:
            if key not in desired:
                deletes.append([*prefix, key])
