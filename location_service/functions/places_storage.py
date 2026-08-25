from __future__ import annotations

import math
import re
from typing import Any

COUNTRY_CODE_PATTERN = re.compile(r"[A-Z]{2,3}")


def result_position(result: object) -> list[float] | None:
    """Returns a drawable ``[lon, lat]`` pair, or ``None`` when unusable."""
    if not isinstance(result, dict):
        return None
    position = result.get("Position")
    if not isinstance(position, (list, tuple)) or len(position) != 2:
        return None
    lon, lat = position
    if isinstance(lon, bool) or isinstance(lat, bool):
        return None
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        return None
    try:
        finite = math.isfinite(lon) and math.isfinite(lat)
    except OverflowError:
        return None
    if not finite:
        return None
    if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
        return None
    return [float(lon), float(lat)]


def drawable_result_items(result: object) -> list[dict[str, Any]]:
    """Returns the drawable place items from a search response."""
    if not isinstance(result, dict):
        return []
    items = result.get("ResultItems")
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict) and result_position(item) is not None
    ]


def normalize_country_code(value: object) -> str | None:
    """Returns an uppercase ASCII country code, or ``None`` when invalid."""
    code = str(value or "").strip().upper()
    if COUNTRY_CODE_PATTERN.fullmatch(code):
        return code
    return None
