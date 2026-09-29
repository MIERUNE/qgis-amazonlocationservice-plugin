from __future__ import annotations

import math
from typing import Any

from qgis.core import (
    NULL,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeatureSource,
    QgsPointXY,
    QgsProcessingFeedback,
    QgsProject,
)

WGS84_CRS = "EPSG:4326"


def source_point_rows(
    source: QgsFeatureSource | None,
    *,
    max_rows: int,
    id_field: str = "",
    order_field: str = "",
    timestamp_field: str = "",
    heading_field: str = "",
    speed_field: str = "",
    name: str = "Point layer",
    transform_context: Any = None,
    feedback: QgsProcessingFeedback | None = None,
) -> list[dict[str, Any]]:
    """
    Copies a point source into request rows before anything is sent.

    Multipoint features are expanded per part, and the scan stops as soon
    as ``max_rows`` is exceeded instead of reading a large layer to the end.
    Rows are ordered by the order field value (numbers before other values,
    NULL last), then by feature id, then by the part index. A missing or
    NULL id falls back to the feature id as a string.
    """
    if source is None:
        raise ValueError(f"{name}: select a point layer first.")
    transform = _wgs84_transform(source, name, transform_context)
    rows = []
    for feature in source.getFeatures():
        if feedback is not None and feedback.isCanceled():
            break
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            continue
        for part_index, point in enumerate(geometry.vertices()):
            if len(rows) >= max_rows:
                raise ValueError(
                    f"{name}: the layer provides more than {max_rows} usable points."
                )
            wgs84 = _transform_point(transform, point, name)
            identifier = field_text(feature, id_field)
            order = field_value(feature, order_field)
            if isinstance(order, float) and not math.isfinite(order):
                order = None
            rows.append(
                {
                    "id": identifier if identifier else str(feature.id()),
                    "order": order,
                    "feature_id": feature.id(),
                    "part": part_index,
                    "position": [wgs84.x(), wgs84.y()],
                    "timestamp": field_timestamp(feature, timestamp_field),
                    "heading": field_number(feature, heading_field),
                    "speed": field_number(feature, speed_field),
                }
            )
    rows.sort(
        key=lambda row: (
            order_sort_key(row["order"]),
            row["feature_id"],
            row["part"],
        )
    )
    return rows


def _wgs84_transform(
    source: QgsFeatureSource, name: str, transform_context: Any
) -> QgsCoordinateTransform:
    """Returns a valid transform from the source CRS to WGS 84."""
    source_crs = source.sourceCrs()
    if not source_crs.isValid():
        raise ValueError(f"{name}: the layer has no valid CRS.")
    if transform_context is None:
        transform_context = QgsProject.instance().transformContext()
    transform = QgsCoordinateTransform(
        source_crs, QgsCoordinateReferenceSystem(WGS84_CRS), transform_context
    )
    if not transform.isValid():
        raise ValueError(f"{name}: the layer CRS cannot be transformed to WGS 84.")
    return transform


def _transform_point(transform, point, name: str) -> QgsPointXY:
    """Transforms one layer point and reports a readable input error."""
    try:
        return transform.transform(QgsPointXY(point))
    except QgsCsException as error:
        raise ValueError(
            f"{name}: a point could not be transformed to WGS 84."
        ) from error


def is_null(value) -> bool:
    """Returns whether a feature attribute is NULL."""
    return value is None or value == NULL


def field_value(feature, field_name: str):
    """Returns a raw attribute value, or ``None`` for NULL or no field."""
    if not field_name:
        return None
    value = feature[field_name]
    return None if is_null(value) else value


def field_text(feature, field_name: str) -> str:
    """Returns an attribute as text, or an empty string."""
    value = field_value(feature, field_name)
    return "" if value is None else str(value)


def field_number(feature, field_name: str):
    """Returns an attribute as a float, or ``None``."""
    value = field_value(feature, field_name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"Field {field_name!r} must contain numbers (got {value!r})."
        ) from None


def field_timestamp(feature, field_name: str):
    """Returns an attribute as an offset-aware ISO time string, or ``None``."""
    value = field_value(feature, field_name)
    if value is None:
        return None
    if hasattr(value, "toPyDateTime"):
        # QDateTime values are local time; attach the system UTC offset.
        return value.toPyDateTime().astimezone().isoformat(timespec="seconds")
    return str(value).strip() or None


def order_sort_key(order) -> tuple:
    """Returns a type-safe sort key: numbers, then text, then NULL."""
    if order is None:
        return (2, 0.0, "")
    if isinstance(order, bool):
        return (1, 0.0, str(order))
    if isinstance(order, (int, float)):
        # NaN breaks sorting, so non-finite values sort with NULL, last.
        if math.isfinite(order):
            return (0, float(order), "")
        return (2, 0.0, "")
    return (1, 0.0, str(order))
