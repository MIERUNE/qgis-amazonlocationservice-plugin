from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRendererCategory,
    QgsRuleBasedRenderer,
    QgsSimpleLineSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsSymbol,
    QgsVectorLayer,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor

from .routes_results import (
    LEG_TRAVEL_MODES,
    LEG_TYPES,
    BrokenResponseError,
    leg_line_points,
    leg_summary,
    major_road_names,
    route_totals,
    transit_leg_attributes,
)

WGS84_CRS = "EPSG:4326"

# The leg layer keeps the v4.4 name, RoadName field and red main route so
# saved projects that reference them keep working.
LAYER_ROUTES = "CalculateRoutes"
LAYER_ROUTE_SUMMARY = "CalculateRoutes (route summary)"
LAYER_ISOLINES = "CalculateIsolines"
LAYER_SNAP_LINE = "SnapToRoads"
LAYER_SNAP_POINTS = "SnapToRoads (confidence points)"
LAYER_MATRIX = "CalculateRouteMatrix"
# The OD lines are straight origin-destination lines, not road geometry;
# the layer name says so because the geometry alone looks like a route.
LAYER_MATRIX_LINES = "CalculateRouteMatrix (OD straight lines)"

# Layer provenance never stores credentials, request URLs, or raw responses.
# Public attribution URLs are the required exception. Distances are meters;
# durations are seconds.
PROPERTY_MARKER = "als_routes/layer"
PROPERTY_SCHEMA_VERSION = "als_routes/schema_version"
PROPERTY_SOURCE_OPERATION = "als_routes/source_operation"
PROPERTY_RETRIEVED_AT = "als_routes/retrieved_at"
PROPERTY_UNITS = "als_routes/units"
PROPERTY_ATTRIBUTIONS = "als_routes/attributions"
SCHEMA_VERSION = 1
UNITS_NOTE = "Distance: meters, Duration: seconds"

MAIN_LINE_COLOR = QColor(255, 0, 0)
LINE_WIDTH = 2.0
ALTERNATIVE_COLORS = (
    QColor(255, 140, 0),
    QColor(138, 43, 226),
    QColor(0, 128, 128),
    QColor(199, 21, 133),
    QColor(139, 69, 19),
)
LEG_TYPE_PEN_STYLES = {
    "Vehicle": Qt.PenStyle.SolidLine,
    "Taxi": Qt.PenStyle.SolidLine,
    "Rental": Qt.PenStyle.SolidLine,
    "Pedestrian": Qt.PenStyle.DotLine,
    "Transit": Qt.PenStyle.DashLine,
    "Ferry": Qt.PenStyle.DashDotLine,
}
ISOLINE_NEAR_COLOR = QColor(76, 175, 80, 120)
ISOLINE_FAR_COLOR = QColor(211, 47, 47, 120)
SNAP_LINE_COLOR = QColor(33, 113, 181)
SNAP_POINT_COLOR = QColor(230, 85, 13)
OD_LINE_COLOR = QColor(120, 120, 120)


def record_layer_source(
    layer: QgsVectorLayer,
    operation: str,
    attributions: list[dict[str, str]] | None = None,
) -> None:
    """Marks the layer as a Routes result and records its provenance."""
    layer.setCustomProperty(PROPERTY_MARKER, True)
    layer.setCustomProperty(PROPERTY_SCHEMA_VERSION, SCHEMA_VERSION)
    layer.setCustomProperty(PROPERTY_SOURCE_OPERATION, operation)
    layer.setCustomProperty(
        PROPERTY_RETRIEVED_AT,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    layer.setCustomProperty(PROPERTY_UNITS, UNITS_NOTE)
    if attributions:
        lines = [_attribution_line(entry) for entry in attributions]
        layer.setCustomProperty(PROPERTY_ATTRIBUTIONS, "\n".join(lines))
        metadata = layer.metadata()
        metadata.setRights(lines)
        layer.setMetadata(metadata)


def _attribution_line(entry: dict[str, str]) -> str:
    """Returns one attribution as display text."""
    text = entry.get("description") or entry.get("url") or ""
    url = entry.get("url") or ""
    if url and url != text:
        return f"{text} ({url})"
    return text


def is_routes_layer(layer: object) -> bool:
    """Returns whether the layer was created by the Routes functions."""
    if not isinstance(layer, QgsVectorLayer) or sip.isdeleted(layer):
        return False
    # QGIS round-trips custom properties through strings.
    if layer.customProperty(PROPERTY_MARKER) not in (True, 1, "true", "1"):
        return False
    try:
        version = int(layer.customProperty(PROPERTY_SCHEMA_VERSION))
    except (TypeError, ValueError):
        return False
    return version == SCHEMA_VERSION


def publish_layers(layers: list[QgsVectorLayer]) -> None:
    """
    Adds fully-built layers to the project as one step.

    Every layer is checked before the first one is added; when adding fails
    part-way, the layers added by this call are removed again so a failed
    operation leaves no output layers behind. Existing layers are never
    touched.
    """
    for layer in layers:
        if not layer.isValid():
            raise RuntimeError("Could not build a valid result layer.")
    project = QgsProject.instance()
    added = []
    try:
        for layer in layers:
            # addMapLayer reports failure by returning None, not by raising.
            if project.addMapLayer(layer) is None:
                raise RuntimeError("Could not add a result layer to the project.")
            added.append(layer)
            if project.mapLayer(layer.id()) is not layer:
                raise RuntimeError("Could not add a result layer to the project.")
    except Exception:
        for layer in added:
            project.removeMapLayer(layer.id())
        raise


def _memory_layer(geometry: str, name: str) -> QgsVectorLayer:
    """Creates an unregistered memory layer in EPSG:4326."""
    if geometry == "None":
        return QgsVectorLayer("None", name, "memory")
    return QgsVectorLayer(f"{geometry}?crs={WGS84_CRS}", name, "memory")


FIELD_ALIASES = {
    "LegDistance": "LegDistance (m)",
    "LegDuration": "LegDuration (s)",
    "RouteDistance": "RouteDistance (m)",
    "RouteDuration": "RouteDuration (s)",
    "Distance": "Distance (m)",
    "Duration": "Duration (s)",
    "ThresholdValue": "ThresholdValue (s or m)",
    "Heading": "Heading (deg)",
    "Speed": "Speed (km/h)",
}


def _add_fields(layer: QgsVectorLayer, field_defs: list[tuple[str, int]]) -> None:
    """Adds the fields, checks the provider accepted them, and sets aliases."""
    fields = QgsFields()
    for name, variant in field_defs:
        fields.append(QgsField(name, variant))
    if not layer.dataProvider().addAttributes(fields):
        raise RuntimeError(f"Could not add fields to the {layer.name()} layer.")
    layer.updateFields()
    if layer.fields().count() != len(field_defs):
        raise RuntimeError(f"Could not add fields to the {layer.name()} layer.")
    for index, (name, _variant) in enumerate(field_defs):
        alias = FIELD_ALIASES.get(name)
        if alias:
            layer.setFieldAlias(index, alias)


def _add_features(layer: QgsVectorLayer, features: list[QgsFeature]) -> None:
    """Adds the features to the layer and checks the provider accepted them."""
    if not features:
        return
    added, stored = layer.dataProvider().addFeatures(features)
    if not added or len(stored) != len(features):
        raise RuntimeError(f"Could not add features to the {layer.name()} layer.")
    layer.updateExtents()


ROUTE_LEG_FIELDS = [
    ("RouteIndex", QVariant.Int),
    ("LegIndex", QVariant.Int),
    ("LegDistance", QVariant.Double),
    ("LegDuration", QVariant.Double),
    ("RouteDistance", QVariant.Double),
    ("RouteDuration", QVariant.Double),
    ("RoadName", QVariant.String),
    ("TravelMode", QVariant.String),
    ("LegType", QVariant.String),
    ("Agency", QVariant.String),
    ("RouteName", QVariant.String),
    ("Headsign", QVariant.String),
    ("DepartureTime", QVariant.String),
    ("ArrivalTime", QVariant.String),
]


def build_route_leg_layer(data: dict[str, Any]) -> QgsVectorLayer | None:
    """Builds the leg line layer of a CalculateRoutes response."""
    layer = _memory_layer("LineString", LAYER_ROUTES)
    _add_fields(layer, ROUTE_LEG_FIELDS)

    features = []
    styled_legs = []
    for route_index, route in enumerate(data.get("Routes") or []):
        if not isinstance(route, dict):
            raise BrokenResponseError(
                "The response contains a malformed route; no layer was "
                "created from this response."
            )
        road_name = major_road_names(route)
        route_distance, route_duration = route_totals(route)
        legs = route.get("Legs")
        if not isinstance(legs, list) or not legs:
            # An empty route must not count as an alternative.
            raise BrokenResponseError(
                "A route carries no legs; no layer was created from this response."
            )
        for leg_index, leg in enumerate(legs):
            points = leg_line_points(leg) if isinstance(leg, dict) else None
            if points is None:
                # Never draw a partial route under full-route totals.
                raise BrokenResponseError(
                    "A route leg has missing or invalid geometry; no layer "
                    "was created from this response."
                )
            leg_distance, leg_duration = leg_summary(leg)
            leg_type = str(leg.get("Type") or "")
            travel_mode = str(leg.get("TravelMode") or "")
            if leg_type not in LEG_TYPES or travel_mode not in LEG_TRAVEL_MODES:
                # Unknown enum values require a plugin update before publishing.
                raise BrokenResponseError(
                    "A route leg carries an invalid type or travel mode; no "
                    "layer was created from this response."
                )
            transit = transit_leg_attributes(leg)
            feature = QgsFeature(layer.fields())
            feature.setAttributes(
                [
                    route_index,
                    leg_index,
                    leg_distance,
                    leg_duration,
                    route_distance,
                    route_duration,
                    road_name,
                    travel_mode,
                    leg_type,
                    transit["Agency"],
                    transit["RouteName"],
                    transit["Headsign"],
                    transit["DepartureTime"],
                    transit["ArrivalTime"],
                ]
            )
            feature.setGeometry(
                QgsGeometry.fromPolylineXY(
                    [QgsPointXY(lon, lat) for lon, lat in points]
                )
            )
            features.append(feature)
            styled_legs.append((route_index, leg_type))

    if not features:
        return None
    _add_features(layer, features)
    _apply_route_renderer(layer, styled_legs)
    return layer


def _route_line_symbol(route_index: int, leg_type: str, geometry_type) -> QgsSymbol:
    """Returns a line symbol colored by route and styled by leg type."""
    if route_index == 0:
        color = MAIN_LINE_COLOR
    else:
        color = ALTERNATIVE_COLORS[(route_index - 1) % len(ALTERNATIVE_COLORS)]
    symbol_layer = QgsSimpleLineSymbolLayer()
    symbol_layer.setColor(color)
    symbol_layer.setWidth(LINE_WIDTH)
    symbol_layer.setPenStyle(LEG_TYPE_PEN_STYLES.get(leg_type, Qt.PenStyle.SolidLine))
    symbol = QgsSymbol.defaultSymbol(geometry_type)
    symbol.changeSymbolLayer(0, symbol_layer)
    return symbol


def _apply_route_renderer(
    layer: QgsVectorLayer, styled_legs: list[tuple[int, str]]
) -> None:
    """
    Styles the leg layer on two axes: color per route, pen style per leg type.

    A single route made only of Vehicle legs keeps the plain red line that
    v4.4 projects expect.
    """
    combos = sorted(set(styled_legs))
    geometry_type = layer.geometryType()
    if combos in ([(0, "Vehicle")], []):
        layer.setRenderer(
            QgsSingleSymbolRenderer(_route_line_symbol(0, "Vehicle", geometry_type))
        )
        return
    root = QgsRuleBasedRenderer.Rule(None)
    for route_index, leg_type in combos:
        rule = QgsRuleBasedRenderer.Rule(
            _route_line_symbol(route_index, leg_type, geometry_type),
            filterExp=(f'"RouteIndex" = {route_index} AND "LegType" = \'{leg_type}\''),
            label=f"Route {route_index} / {leg_type or 'Unknown'}",
        )
        root.appendChild(rule)
    layer.setRenderer(QgsRuleBasedRenderer(root))


ROUTE_SUMMARY_FIELDS = [
    ("RouteIndex", QVariant.Int),
    ("RouteDistance", QVariant.Double),
    ("RouteDuration", QVariant.Double),
    ("LegCount", QVariant.Int),
    ("RoadName", QVariant.String),
]


def build_route_summary_layer(data: dict[str, Any]) -> QgsVectorLayer | None:
    """Builds the optional geometry-less route summary table."""
    layer = _memory_layer("None", LAYER_ROUTE_SUMMARY)
    _add_fields(layer, ROUTE_SUMMARY_FIELDS)
    features = []
    for route_index, route in enumerate(data.get("Routes") or []):
        if not isinstance(route, dict):
            continue
        distance, duration = route_totals(route)
        feature = QgsFeature(layer.fields())
        legs = [leg for leg in route.get("Legs") or [] if isinstance(leg, dict)]
        feature.setAttributes(
            [
                route_index,
                distance,
                duration,
                len(legs),
                major_road_names(route),
            ]
        )
        features.append(feature)
    if not features:
        return None
    _add_features(layer, features)
    return layer


ISOLINE_FIELDS = [
    ("Direction", QVariant.String),
    ("ThresholdType", QVariant.String),
    ("ThresholdValue", QVariant.Double),
    ("Unit", QVariant.String),
    ("TravelMode", QVariant.String),
]


def build_isoline_layer(
    isolines: list[dict[str, Any]], direction: str, travel_mode: str
) -> QgsVectorLayer | None:
    """Builds the polygon layer for normalized isolines."""
    if not isolines:
        return None
    layer = _memory_layer("MultiPolygon", LAYER_ISOLINES)
    _add_fields(layer, ISOLINE_FIELDS)

    # Larger thresholds are inserted first so smaller areas draw on top.
    ordered = sorted(isolines, key=lambda item: item["threshold_value"], reverse=True)
    features = []
    for isoline in ordered:
        unit = "seconds" if isoline["threshold_type"] == "Time" else "meters"
        polygons = [
            [[QgsPointXY(lon, lat) for lon, lat in ring] for ring in polygon]
            for polygon in isoline["polygons"]
        ]
        feature = QgsFeature(layer.fields())
        feature.setAttributes(
            [
                direction,
                isoline["threshold_type"],
                isoline["threshold_value"],
                unit,
                travel_mode,
            ]
        )
        feature.setGeometry(QgsGeometry.fromMultiPolygonXY(polygons))
        features.append(feature)
    _add_features(layer, features)
    _apply_isoline_renderer(layer, ordered)
    return layer


def _apply_isoline_renderer(
    layer: QgsVectorLayer, ordered: list[dict[str, Any]]
) -> None:
    """Colors each threshold from green (near) to red (far)."""
    values = sorted({isoline["threshold_value"] for isoline in ordered})
    categories = []
    for position, value in enumerate(values):
        fraction = position / (len(values) - 1) if len(values) > 1 else 0.0
        color = QColor(
            round(
                ISOLINE_NEAR_COLOR.red()
                + fraction * (ISOLINE_FAR_COLOR.red() - ISOLINE_NEAR_COLOR.red())
            ),
            round(
                ISOLINE_NEAR_COLOR.green()
                + fraction * (ISOLINE_FAR_COLOR.green() - ISOLINE_NEAR_COLOR.green())
            ),
            round(
                ISOLINE_NEAR_COLOR.blue()
                + fraction * (ISOLINE_FAR_COLOR.blue() - ISOLINE_NEAR_COLOR.blue())
            ),
            ISOLINE_NEAR_COLOR.alpha(),
        )
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(color)
        categories.append(QgsRendererCategory(value, symbol, str(value)))
    layer.setRenderer(QgsCategorizedSymbolRenderer("ThresholdValue", categories))


SNAP_LINE_FIELDS = [
    ("TracePointCount", QVariant.Int),
    ("SnappedVertexCount", QVariant.Int),
    ("NoticeCount", QVariant.Int),
]


def build_snap_line_layer(
    line_points: list[list[float]],
    trace_point_count: int,
    notice_count: int,
) -> QgsVectorLayer:
    """Builds the snapped line layer."""
    layer = _memory_layer("LineString", LAYER_SNAP_LINE)
    _add_fields(layer, SNAP_LINE_FIELDS)
    feature = QgsFeature(layer.fields())
    feature.setAttributes([trace_point_count, len(line_points), notice_count])
    feature.setGeometry(
        QgsGeometry.fromPolylineXY([QgsPointXY(lon, lat) for lon, lat in line_points])
    )
    _add_features(layer, [feature])
    symbol_layer = QgsSimpleLineSymbolLayer()
    symbol_layer.setColor(SNAP_LINE_COLOR)
    symbol_layer.setWidth(LINE_WIDTH)
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    symbol.changeSymbolLayer(0, symbol_layer)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


SNAP_POINT_FIELDS = [
    ("SourceID", QVariant.String),
    ("SourceOrder", QVariant.String),
    ("SnappedIndex", QVariant.Int),
    ("Confidence", QVariant.Double),
    ("Timestamp", QVariant.String),
    ("Heading", QVariant.Double),
    ("Speed", QVariant.Double),
]


def build_snap_points_layer(
    snapped_points: list[dict[str, Any]],
    sent_points: list[dict[str, Any]],
) -> QgsVectorLayer | None:
    """
    Builds the confidence point layer.

    ``snapped_points`` comes from ``normalize_snapped_trace_points`` and is
    index-aligned with ``sent_points``, the input snapshot rows that carry
    the source id, order and optional trace fields.
    """
    if not snapped_points:
        return None
    layer = _memory_layer("Point", LAYER_SNAP_POINTS)
    _add_fields(layer, SNAP_POINT_FIELDS)
    features = []
    for point in snapped_points:
        sent = sent_points[point["index"]]
        feature = QgsFeature(layer.fields())
        feature.setAttributes(
            [
                str(sent.get("id", "")),
                None if sent.get("order") is None else str(sent.get("order")),
                point["index"],
                point["confidence"],
                sent.get("timestamp"),
                sent.get("heading"),
                sent.get("speed"),
            ]
        )
        lon, lat = point["snapped"]
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
        features.append(feature)
    _add_features(layer, features)
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    symbol.setColor(SNAP_POINT_COLOR)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


MATRIX_FIELDS = [
    ("OriginID", QVariant.String),
    ("DestinationID", QVariant.String),
    ("OriginIndex", QVariant.Int),
    ("DestinationIndex", QVariant.Int),
    ("Distance", QVariant.Double),
    ("Duration", QVariant.Double),
    ("Error", QVariant.String),
]


def build_matrix_layers(
    rows: list[list[dict[str, Any]]],
    origins: list[dict[str, Any]],
    destinations: list[dict[str, Any]],
    include_od_lines: bool,
) -> list[QgsVectorLayer]:
    """
    Builds the matrix table and, optionally, the straight OD lines.

    ``origins`` and ``destinations`` are input snapshot rows with an ``id``
    and a ``position``. Error cells keep NULL distance and duration and only
    successful cells get an OD line.
    """
    table = _memory_layer("None", LAYER_MATRIX)
    _add_fields(table, MATRIX_FIELDS)
    lines = None
    if include_od_lines:
        lines = _memory_layer("LineString", LAYER_MATRIX_LINES)
        _add_fields(lines, MATRIX_FIELDS)

    table_features = []
    line_features = []
    for origin_index, row in enumerate(rows):
        for destination_index, cell in enumerate(row):
            attributes = [
                str(origins[origin_index].get("id", "")),
                str(destinations[destination_index].get("id", "")),
                origin_index,
                destination_index,
                cell["distance"],
                cell["duration"],
                cell["error"],
            ]
            feature = QgsFeature(table.fields())
            feature.setAttributes(attributes)
            table_features.append(feature)
            if lines is not None and not cell["error"]:
                line = QgsFeature(lines.fields())
                line.setAttributes(attributes)
                start = origins[origin_index]["position"]
                end = destinations[destination_index]["position"]
                line.setGeometry(
                    QgsGeometry.fromPolylineXY(
                        [
                            QgsPointXY(start[0], start[1]),
                            QgsPointXY(end[0], end[1]),
                        ]
                    )
                )
                line_features.append(line)

    _add_features(table, table_features)
    layers = [table]
    if lines is not None and line_features:
        _add_features(lines, line_features)
        symbol_layer = QgsSimpleLineSymbolLayer()
        symbol_layer.setColor(OD_LINE_COLOR)
        symbol_layer.setWidth(0.8)
        symbol_layer.setPenStyle(Qt.PenStyle.DashLine)
        symbol = QgsSymbol.defaultSymbol(lines.geometryType())
        symbol.changeSymbolLayer(0, symbol_layer)
        lines.setRenderer(QgsSingleSymbolRenderer(symbol))
        layers.append(lines)
    return layers
