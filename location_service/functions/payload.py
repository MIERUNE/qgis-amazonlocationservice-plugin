from __future__ import annotations

from typing import Any


def prune_payload(data: dict[str, Any]) -> dict[str, Any]:
    """
    Returns a copy of a request body without ``None`` or empty values.

    Keys whose values are ``None``, ``""``, ``[]``, or ``{}`` are removed
    recursively so request bodies only carry parameters the user actually set.
    This module is intentionally free of QGIS imports so the request-body
    builders that rely on it can be unit-tested without a QGIS runtime.
    """
    pruned: dict[str, Any] = {}
    for key, value in data.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            nested = prune_payload(value)
            if nested:
                pruned[key] = nested
        else:
            pruned[key] = value
    return pruned
