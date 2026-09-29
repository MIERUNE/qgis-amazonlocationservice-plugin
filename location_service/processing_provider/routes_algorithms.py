from __future__ import annotations

import json
from typing import Any

from qgis.core import (
    QgsCsException,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingOutputString,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterString,
    QgsVectorLayer,
)

from ..functions import routes_capabilities, routes_layers, routes_results
from ..functions.routes import RoutesFunctions
from ..functions.routes_requests import (
    AVOID_KEYS,
    ISOLINE_DIRECTIONS,
    MAX_ALTERNATIVES,
    MAX_MATRIX_CELLS,
    MAX_SNAP_RADIUS,
    MAX_TRACE_POINTS,
    MAX_WAYPOINTS,
    MIN_SNAP_RADIUS,
    OPTIMIZE_FOR,
    ROUTES_TRAVEL_MODES,
    THRESHOLD_TYPES,
    TRANSIT_LIKE_MODES,
    TRANSIT_MODE_VALUES,
    TRAVEL_MODES,
    IsolineOptions,
    MatrixOptions,
    RouteOptions,
    SnapOptions,
    TracePoint,
    build_isolines_body,
    build_matrix_body,
    build_routes_body,
    build_snap_body,
    haversine_meters,
    parse_thresholds,
)
from . import compat
from .base import WGS84, LocationServiceAlgorithm
from .inputs import iso_time_with_offset, source_point_rows

ROUTES_PERMISSION_HINT = (
    "The request was rejected (HTTP 403). Check that the API key allows the "
    "actions geo-routes:CalculateRoutes, geo-routes:CalculateIsolines, "
    "geo-routes:SnapToRoads and geo-routes:CalculateRouteMatrix (or "
    "geo-routes:*) on arn:aws:geo-routes:Region::provider/default, and check "
    "the configured region, the key's expiry, and any client restrictions "
    "on the key."
)

TIME_CHOICES = (
    ("Not specified", ""),
    ("Depart now", "depart_now"),
    ("Departure time", "departure_time"),
    ("Arrival time", "arrival_time"),
)
TRANSIT_FILTERS = (
    ("All modes allowed (default)", ""),
    ("Allow only the selected modes", "allow"),
    ("Exclude the selected modes", "exclude"),
)
# "All" is expressed by leaving the transit filter on its default.
TRANSIT_MODE_CHOICES = tuple(mode for mode in TRANSIT_MODE_VALUES if mode != "All")
ISOLINE_DIRECTION_LABELS = {
    "Origin": "Departure (from origin)",
    "Destination": "Arrival (to destination)",
}
THRESHOLD_TYPE_LABELS = {"Time": "Time (minutes)", "Distance": "Distance (km)"}


def billing_estimate_routes(region: str, travel_mode: str) -> tuple[str, str]:
    """Returns the estimated ``(bucket, reason)`` of a CalculateRoutes request."""
    grab = routes_capabilities.is_grab_region(region)
    if travel_mode == "Intermodal":
        return "Premium", "Intermodal travel mode"
    if travel_mode == "Scooter" and not grab:
        return "Advanced", "Scooter travel mode"
    return "Core", f"{travel_mode} travel mode"


def billing_estimate_isolines(options: IsolineOptions) -> tuple[str, str]:
    """Returns the estimated ``(bucket, reason)`` of a CalculateIsolines request."""
    if options.travel_mode == "Scooter":
        return "Premium", "Scooter travel mode"
    advanced_limit = 3600 if options.threshold_type == "Time" else 100_000
    if any(value > advanced_limit for value in options.thresholds):
        limit = "60 minutes" if options.threshold_type == "Time" else "100 km"
        return "Premium", f"a threshold exceeds {limit}"
    return "Advanced", "within the Advanced threshold range"


def billing_estimate_snap(options: SnapOptions) -> tuple[str, str]:
    """Returns the estimated ``(bucket, reason)`` of a SnapToRoads request."""
    if options.travel_mode == "Scooter":
        return "Premium", "Scooter travel mode"
    points = options.trace_points
    if len(points) > 200:
        return "Premium", "more than 200 trace points"
    # AWS does not say "adjacent", so check every pair. At most 200 points
    # means 19,900 checks and this also handles the antimeridian.
    for index, first in enumerate(points):
        for second in points[index + 1 :]:
            if haversine_meters(first.position, second.position) > 100_000:
                return "Premium", "trace points more than 100 km apart"
    return "Advanced", "within the Advanced limits"


def billing_estimate_matrix(region: str, travel_mode: str) -> tuple[str, str]:
    """Returns the estimated ``(bucket, reason)`` of a route matrix request."""
    if travel_mode == "Scooter" and not routes_capabilities.is_grab_region(region):
        return "Advanced", "Scooter travel mode"
    return "Core", f"{travel_mode} travel mode"


class RoutesAlgorithm(LocationServiceAlgorithm):
    """Shared behavior of the Routes algorithms."""

    GROUP = "Routes"
    GROUP_ID = "routes"
    ICON = "ui/routes/routes.png"
    PERMISSION_HINT = ROUTES_PERMISSION_HINT
    TRAVEL_MODES: tuple[str, ...] = TRAVEL_MODES

    TRAVEL_MODE = "TRAVEL_MODE"

    def add_travel_mode_parameter(self) -> None:
        """Adds the travel mode choice."""
        self.addParameter(
            QgsProcessingParameterEnum(
                self.TRAVEL_MODE,
                "Travel mode",
                options=list(self.TRAVEL_MODES),
                defaultValue=0,
            )
        )

    def travel_mode(
        self, parameters: dict[str, Any], context: QgsProcessingContext
    ) -> str:
        """Returns the selected travel mode."""
        return self.TRAVEL_MODES[
            self.parameterAsEnum(parameters, self.TRAVEL_MODE, context)
        ]

    def position(
        self, parameters: dict[str, Any], name: str, context: QgsProcessingContext
    ) -> tuple[float, float]:
        """Returns a required point parameter as WGS 84 ``(lon, lat)``."""
        if parameters.get(name) in (None, ""):
            raise ValueError(f"Set the {name.lower().replace('_', ' ')}.")
        try:
            point = self.parameterAsPoint(parameters, name, context, WGS84)
        except QgsCsException as error:
            raise ValueError("The point could not be transformed to WGS 84.") from error
        return (point.x(), point.y())

    def source(self, parameters: dict[str, Any], name: str, context):
        """Returns a required feature source parameter."""
        source = self.parameterAsSource(parameters, name, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, name))
        return source

    def field(self, parameters: dict[str, Any], name: str, context) -> str:
        """Returns an optional field parameter, or an empty string."""
        return self.parameterAsString(parameters, name, context) or ""

    def report_estimate(
        self, feedback: QgsProcessingFeedback, unit: str, estimate: tuple[str, str]
    ) -> None:
        """Logs the billing estimate before the request is sent."""
        bucket, reason = estimate
        message = (
            f"This sends {unit} at the estimated {bucket} pricing bucket ({reason})."
        )
        if bucket == "Premium":
            feedback.pushWarning(message)
        else:
            feedback.pushInfo(message)

    def publish(
        self,
        parameters: dict[str, Any],
        context: QgsProcessingContext,
        outputs: list[tuple[str, QgsVectorLayer | None, bool]],
        attributions: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Records provenance and writes each ``(name, layer, optional)`` output."""
        results = {}
        for name, layer, optional in outputs:
            if layer is None:
                continue
            routes_layers.record_layer_source(layer, self.OPERATION, attributions)
            dest_id = self.write_layer(
                parameters, name, context, layer, optional=optional
            )
            if dest_id is not None:
                results[name] = dest_id
        return results


class CalculateRoutesAlgorithm(RoutesAlgorithm):
    """CalculateRoutes: a route between an origin and a destination."""

    OPERATION = "CalculateRoutes"
    TRAVEL_MODES = ROUTES_TRAVEL_MODES
    HELP = (
        "Calculates a route between an origin and a destination, optionally "
        f"through up to {MAX_WAYPOINTS} waypoints taken from a point layer in "
        "the order of its order field.\n\n"
        "Waypoints, Optimize for and Avoid do not apply to Transit or "
        "Intermodal routing; the transit mode filter applies to Transit only. "
        "Live and dynamic traffic is only reflected when a time is specified. "
        "Distances are meters and durations are seconds.\n\n"
        "Required data attributions are written to the log and to the layer "
        "metadata."
    )

    ORIGIN = "ORIGIN"
    DESTINATION = "DESTINATION"
    WAYPOINTS = "WAYPOINTS"
    WAYPOINTS_ORDER_FIELD = "WAYPOINTS_ORDER_FIELD"
    OPTIMIZE_FOR = "OPTIMIZE_FOR"
    AVOID = "AVOID"
    MAX_ALTERNATIVES = "MAX_ALTERNATIVES"
    TIME_CHOICE = "TIME_CHOICE"
    TIME = "TIME"
    TRANSIT_FILTER = "TRANSIT_FILTER"
    TRANSIT_MODES = "TRANSIT_MODES"
    OUTPUT = "OUTPUT"
    OUTPUT_SUMMARY = "OUTPUT_SUMMARY"
    ATTRIBUTIONS = "ATTRIBUTIONS"
    NOTICES = "NOTICES"

    def initAlgorithm(self, config=None) -> None:
        """Defines the CalculateRoutes parameters."""
        self.addParameter(QgsProcessingParameterPoint(self.ORIGIN, "Origin"))
        self.addParameter(QgsProcessingParameterPoint(self.DESTINATION, "Destination"))
        self.add_travel_mode_parameter()
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.WAYPOINTS,
                "Waypoints",
                [compat.SOURCE_POINT],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.WAYPOINTS_ORDER_FIELD,
                "Waypoint order field",
                parentLayerParameterName=self.WAYPOINTS,
                type=compat.FIELD_ANY,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.OPTIMIZE_FOR,
                "Optimize for",
                options=list(OPTIMIZE_FOR),
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.AVOID,
                "Avoid",
                options=list(AVOID_KEYS),
                allowMultiple=True,
                optional=True,
                defaultValue=[],
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_ALTERNATIVES,
                "Maximum alternative routes",
                type=compat.NUMBER_INTEGER,
                defaultValue=0,
                minValue=0,
                maxValue=MAX_ALTERNATIVES,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.TIME_CHOICE,
                "Departure / arrival",
                options=[label for label, _value in TIME_CHOICES],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.TIME,
                "Departure or arrival time",
                type=compat.DATETIME,
                optional=True,
            )
        )
        self.addParameter(
            compat.set_advanced(
                QgsProcessingParameterEnum(
                    self.TRANSIT_FILTER,
                    "Transit mode filter (Transit only)",
                    options=[label for label, _value in TRANSIT_FILTERS],
                    defaultValue=0,
                )
            )
        )
        self.addParameter(
            compat.set_advanced(
                QgsProcessingParameterEnum(
                    self.TRANSIT_MODES,
                    "Transit modes for the filter",
                    options=list(TRANSIT_MODE_CHOICES),
                    allowMultiple=True,
                    optional=True,
                    defaultValue=[],
                )
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, "CalculateRoutes (legs)", type=compat.SOURCE_LINE
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_SUMMARY,
                "CalculateRoutes (route summary)",
                type=compat.SOURCE_TABLE,
                optional=True,
                createByDefault=False,
            )
        )
        self.addOutput(
            QgsProcessingOutputString(self.ATTRIBUTIONS, "Data attributions (JSON)")
        )
        self.addOutput(QgsProcessingOutputString(self.NOTICES, "Notices (JSON)"))
        self.add_pricing_output()

    def _time_options(self, parameters, context, feedback) -> dict[str, Any]:
        """Returns the depart/arrival choice as RouteOptions keyword values."""
        choice = TIME_CHOICES[
            self.parameterAsEnum(parameters, self.TIME_CHOICE, context)
        ][1]
        time_given = parameters.get(self.TIME) not in (None, "")
        if time_given and choice in ("", "depart_now"):
            feedback.pushWarning(
                "The time was ignored because no departure or arrival time was chosen."
            )
        if not choice:
            return {}
        if choice == "depart_now":
            return {"depart_now": True}
        value = self.parameterAsDateTime(parameters, self.TIME, context)
        if value is None or not value.isValid():
            raise ValueError("Set the departure or arrival time.")
        return {choice: iso_time_with_offset(value)}

    def _transit_options(self, parameters, context, travel_mode: str, feedback) -> dict:
        """Returns the allowed/excluded transit modes for RouteOptions."""
        transit_filter = TRANSIT_FILTERS[
            self.parameterAsEnum(parameters, self.TRANSIT_FILTER, context)
        ][1]
        if not transit_filter:
            return {}
        if travel_mode != "Transit":
            feedback.pushWarning(
                "The transit mode filter applies to Transit only and was ignored."
            )
            return {}
        modes = tuple(
            TRANSIT_MODE_CHOICES[index]
            for index in self.parameterAsEnums(parameters, self.TRANSIT_MODES, context)
        )
        if not modes:
            raise ValueError(
                "Select at least one transit mode, or set the transit mode "
                "filter back to 'All modes allowed'."
            )
        if transit_filter == "allow":
            return {"transit_allowed_modes": modes}
        return {"transit_excluded_modes": modes}

    def _waypoints(self, parameters, context, feedback) -> tuple:
        """Returns the ordered waypoint positions of the optional layer."""
        if parameters.get(self.WAYPOINTS) in (None, ""):
            return ()
        rows = source_point_rows(
            self.source(parameters, self.WAYPOINTS, context),
            max_rows=MAX_WAYPOINTS,
            order_field=self.field(parameters, self.WAYPOINTS_ORDER_FIELD, context),
            name="Waypoints",
            transform_context=context.transformContext(),
            feedback=feedback,
        )
        return tuple(tuple(row["position"]) for row in rows)

    def execute(self, parameters, context, feedback) -> dict[str, Any]:
        """Sends CalculateRoutes and writes the leg and summary layers."""
        travel_mode = self.travel_mode(parameters, context)
        transit_like = travel_mode in TRANSIT_LIKE_MODES
        # Transit and Intermodal ignore waypoints, so the layer is not read.
        waypoints = ()
        if not transit_like:
            waypoints = self._waypoints(parameters, context, feedback)
        elif parameters.get(self.WAYPOINTS) not in (None, ""):
            feedback.pushWarning(
                "Waypoints do not apply to Transit or Intermodal routing and "
                "were ignored."
            )
        optimize_for = OPTIMIZE_FOR[
            self.parameterAsEnum(parameters, self.OPTIMIZE_FOR, context)
        ]
        if transit_like and optimize_for != OPTIMIZE_FOR[0]:
            feedback.pushWarning(
                "Optimize for does not apply to Transit or Intermodal routing "
                "and was ignored."
            )
        avoid = tuple(
            AVOID_KEYS[index]
            for index in self.parameterAsEnums(parameters, self.AVOID, context)
        )
        if transit_like and avoid:
            feedback.pushWarning(
                "Avoid options do not apply to Transit or Intermodal routing "
                "and were ignored."
            )
            avoid = ()
        options = RouteOptions(
            origin=self.position(parameters, self.ORIGIN, context),
            destination=self.position(parameters, self.DESTINATION, context),
            waypoints=waypoints,
            travel_mode=travel_mode,
            optimize_for=optimize_for,
            avoid=avoid,
            max_alternatives=self.parameterAsInt(
                parameters, self.MAX_ALTERNATIVES, context
            ),
            **self._time_options(parameters, context, feedback),
            **self._transit_options(parameters, context, travel_mode, feedback),
        )
        routes_capabilities.validate_region_options(
            self.region,
            self.OPERATION,
            travel_mode=options.travel_mode,
            avoid=options.avoid,
            max_alternatives_value=options.max_alternatives,
            arrival_time=options.arrival_time,
        )
        build_routes_body(options)
        self.report_estimate(
            feedback, "1 request", billing_estimate_routes(self.region, travel_mode)
        )

        routes = RoutesFunctions()
        result = self.send(
            routes.api_handler,
            lambda: routes.request_routes(options, credentials=self.credentials),
            feedback,
        )

        routes_results.validate_route_count(result, options.max_alternatives)
        attributions = routes_results.collect_attributions(result)
        notices = routes_results.collect_notices(result)
        report_route_notices(feedback, notices)
        report_attributions(feedback, attributions)
        legs = routes_layers.build_route_leg_layer(result)
        if legs is None:
            feedback.pushWarning("CalculateRoutes returned no drawable results.")
            legs = routes_layers.empty_layer(
                "LineString", routes_layers.LAYER_ROUTES, routes_layers.ROUTE_LEG_FIELDS
            )
        elif any(
            None in routes_results.route_totals(route)
            for route in result.get("Routes") or []
            if isinstance(route, dict)
        ):
            feedback.pushWarning(
                "Some route totals are unavailable; their RouteDistance and "
                "RouteDuration are NULL."
            )
        summary = None
        if parameters.get(self.OUTPUT_SUMMARY) is not None:
            summary = routes_layers.build_route_summary_layer(result)
            if summary is None:
                # A requested output is written even when it is empty, so a
                # model step that reads it does not fail.
                summary = routes_layers.empty_layer(
                    "None",
                    routes_layers.LAYER_ROUTE_SUMMARY,
                    routes_layers.ROUTE_SUMMARY_FIELDS,
                )
        feedback.pushInfo(routes_success_message(result))
        results = self.publish(
            parameters,
            context,
            [(self.OUTPUT, legs, False), (self.OUTPUT_SUMMARY, summary, True)],
            attributions,
        )
        results[self.ATTRIBUTIONS] = json.dumps(attributions)
        results[self.NOTICES] = json.dumps(notices)
        return results


class CalculateIsolinesAlgorithm(RoutesAlgorithm):
    """CalculateIsolines: areas reachable within times or distances."""

    OPERATION = "CalculateIsolines"
    HELP = (
        "Calculates the areas reachable from (or to) a point within up to five "
        "time or distance thresholds.\n\n"
        "Enter the thresholds as comma-separated minutes (Time) or kilometers "
        "(Distance), such as 5, 10, 15. Each threshold is one billed isoline."
    )

    CENTER = "CENTER"
    DIRECTION = "DIRECTION"
    THRESHOLD_TYPE = "THRESHOLD_TYPE"
    THRESHOLDS = "THRESHOLDS"
    OUTPUT = "OUTPUT"

    def initAlgorithm(self, config=None) -> None:
        """Defines the CalculateIsolines parameters."""
        self.addParameter(QgsProcessingParameterPoint(self.CENTER, "Center point"))
        self.addParameter(
            QgsProcessingParameterEnum(
                self.DIRECTION,
                "Direction",
                options=[
                    ISOLINE_DIRECTION_LABELS[value] for value in ISOLINE_DIRECTIONS
                ],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.THRESHOLD_TYPE,
                "Threshold type",
                options=[THRESHOLD_TYPE_LABELS[value] for value in THRESHOLD_TYPES],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.THRESHOLDS,
                "Thresholds (comma-separated minutes or kilometers)",
                defaultValue="5, 10, 15",
            )
        )
        self.add_travel_mode_parameter()
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, self.OPERATION, type=compat.SOURCE_POLYGON
            )
        )
        self.add_pricing_output()

    def execute(self, parameters, context, feedback) -> dict[str, Any]:
        """Sends CalculateIsolines and writes the isoline polygons."""
        threshold_type = THRESHOLD_TYPES[
            self.parameterAsEnum(parameters, self.THRESHOLD_TYPE, context)
        ]
        options = IsolineOptions(
            center=self.position(parameters, self.CENTER, context),
            direction=ISOLINE_DIRECTIONS[
                self.parameterAsEnum(parameters, self.DIRECTION, context)
            ],
            threshold_type=threshold_type,
            thresholds=parse_thresholds(
                self.parameterAsString(parameters, self.THRESHOLDS, context),
                threshold_type,
            ),
            travel_mode=self.travel_mode(parameters, context),
        )
        routes_capabilities.validate_region_options(
            self.region, self.OPERATION, travel_mode=options.travel_mode
        )
        build_isolines_body(options)
        self.report_estimate(
            feedback,
            f"{len(options.thresholds)} billed isoline(s) (one per threshold)",
            billing_estimate_isolines(options),
        )

        routes = RoutesFunctions()
        result = self.send(
            routes.api_handler,
            lambda: routes.request_isolines(options, credentials=self.credentials),
            feedback,
        )
        isolines = routes_results.normalize_isolines(
            result, options.threshold_type, options.thresholds
        )
        layer = routes_layers.build_isoline_layer(
            isolines, options.direction, options.travel_mode
        )
        if layer is None:
            feedback.pushWarning("CalculateIsolines returned no drawable results.")
            layer = routes_layers.empty_layer(
                "MultiPolygon",
                routes_layers.LAYER_ISOLINES,
                routes_layers.ISOLINE_FIELDS,
            )
        else:
            feedback.pushInfo(f"CalculateIsolines returned {len(isolines)} isoline(s).")
        return self.publish(parameters, context, [(self.OUTPUT, layer, False)])


class SnapToRoadsAlgorithm(RoutesAlgorithm):
    """SnapToRoads: snaps a GPS trace from a point layer to roads."""

    OPERATION = "SnapToRoads"
    HELP = (
        "Snaps a GPS trace from a point or multipoint layer to roads. Points "
        "are sent in the order of the order field (numbers, dates and times "
        "first, NULL last), "
        "then by feature id.\n\n"
        "Timestamps must be date-time fields or ISO 8601 text with a timezone "
        "offset, such as 2026-08-26T09:00:00+09:00. Heading is 0 to 360 "
        "degrees and speed is in kilometers per hour."
    )

    INPUT = "INPUT"
    ID_FIELD = "ID_FIELD"
    ORDER_FIELD = "ORDER_FIELD"
    TIMESTAMP_FIELD = "TIMESTAMP_FIELD"
    HEADING_FIELD = "HEADING_FIELD"
    SPEED_FIELD = "SPEED_FIELD"
    SNAP_RADIUS = "SNAP_RADIUS"
    OUTPUT = "OUTPUT"
    OUTPUT_POINTS = "OUTPUT_POINTS"
    NOTICES = "NOTICES"

    def initAlgorithm(self, config=None) -> None:
        """Defines the SnapToRoads parameters."""
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT, "GPS trace points", [compat.SOURCE_POINT]
            )
        )
        for name, label, field_type in (
            (self.ID_FIELD, "ID field", compat.FIELD_ANY),
            (self.ORDER_FIELD, "Order field", compat.FIELD_ANY),
            (self.TIMESTAMP_FIELD, "Timestamp field", compat.FIELD_ANY),
            (self.HEADING_FIELD, "Heading field (degrees)", compat.FIELD_NUMERIC),
            (self.SPEED_FIELD, "Speed field (km/h)", compat.FIELD_NUMERIC),
        ):
            self.addParameter(
                QgsProcessingParameterField(
                    name,
                    label,
                    parentLayerParameterName=self.INPUT,
                    type=field_type,
                    optional=True,
                )
            )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SNAP_RADIUS,
                "Snap radius (meters)",
                type=compat.NUMBER_INTEGER,
                defaultValue=300,
                minValue=MIN_SNAP_RADIUS,
                maxValue=MAX_SNAP_RADIUS,
            )
        )
        self.add_travel_mode_parameter()
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, self.OPERATION, type=compat.SOURCE_LINE
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_POINTS,
                "SnapToRoads (confidence points)",
                type=compat.SOURCE_POINT,
                optional=True,
                createByDefault=True,
            )
        )
        self.addOutput(QgsProcessingOutputString(self.NOTICES, "Notices (JSON)"))
        self.add_pricing_output()

    def execute(self, parameters, context, feedback) -> dict[str, Any]:
        """Sends SnapToRoads and writes the snapped line and points."""
        rows = source_point_rows(
            self.source(parameters, self.INPUT, context),
            max_rows=MAX_TRACE_POINTS,
            id_field=self.field(parameters, self.ID_FIELD, context),
            order_field=self.field(parameters, self.ORDER_FIELD, context),
            timestamp_field=self.field(parameters, self.TIMESTAMP_FIELD, context),
            heading_field=self.field(parameters, self.HEADING_FIELD, context),
            speed_field=self.field(parameters, self.SPEED_FIELD, context),
            name="GPS trace points",
            transform_context=context.transformContext(),
            feedback=feedback,
        )
        self.raise_if_cancelled(feedback)
        options = SnapOptions(
            trace_points=tuple(
                TracePoint(
                    position=tuple(row["position"]),
                    timestamp=row.get("timestamp"),
                    heading=row.get("heading"),
                    speed=row.get("speed"),
                )
                for row in rows
            ),
            snap_radius=self.parameterAsInt(parameters, self.SNAP_RADIUS, context),
            travel_mode=self.travel_mode(parameters, context),
        )
        routes_capabilities.validate_region_options(
            self.region, self.OPERATION, travel_mode=options.travel_mode
        )
        build_snap_body(options)
        self.report_estimate(
            feedback,
            f"1 request ({len(rows)} trace points)",
            billing_estimate_snap(options),
        )

        routes = RoutesFunctions()
        result = self.send(
            routes.api_handler,
            lambda: routes.request_snap_to_roads(options, credentials=self.credentials),
            feedback,
        )
        notices = routes_results.validate_notices(result, "The SnapToRoads response")
        report_snap_notices(feedback, notices)
        # Always verify response indexes before joining input attributes,
        # even when the confidence-point output is skipped.
        snapped = routes_results.normalize_snapped_trace_points(
            result, [row["position"] for row in rows]
        )
        line_points = routes_results.snapped_line_points(result)
        if line_points is None:
            feedback.pushWarning("SnapToRoads returned no snapped line.")
            line = routes_layers.empty_layer(
                "LineString",
                routes_layers.LAYER_SNAP_LINE,
                routes_layers.SNAP_LINE_FIELDS,
            )
        else:
            line = routes_layers.build_snap_line_layer(
                line_points, len(rows), len(notices)
            )
        points = None
        if parameters.get(self.OUTPUT_POINTS) is not None:
            # The points come from the trace, so write them even without a line.
            points = routes_layers.build_snap_points_layer(snapped, rows)
        results = self.publish(
            parameters,
            context,
            [(self.OUTPUT, line, False), (self.OUTPUT_POINTS, points, True)],
        )
        results[self.NOTICES] = json.dumps(notices)
        return results


class CalculateRouteMatrixAlgorithm(RoutesAlgorithm):
    """CalculateRouteMatrix: distances and durations between two point layers."""

    OPERATION = "CalculateRouteMatrix"
    HELP = (
        "Calculates distances and durations between every origin and every "
        "destination of two point layers. Points are sent in the order of the "
        "order field, then by feature id.\n\n"
        f"Every origin x destination pair is one billed route calculation; "
        f"the plugin allows at most {MAX_MATRIX_CELLS} pairs. The optional OD "
        "lines are straight lines in EPSG:4326, not road geometry."
    )

    ORIGINS = "ORIGINS"
    ORIGINS_ID_FIELD = "ORIGINS_ID_FIELD"
    ORIGINS_ORDER_FIELD = "ORIGINS_ORDER_FIELD"
    DESTINATIONS = "DESTINATIONS"
    DESTINATIONS_ID_FIELD = "DESTINATIONS_ID_FIELD"
    DESTINATIONS_ORDER_FIELD = "DESTINATIONS_ORDER_FIELD"
    OUTPUT = "OUTPUT"
    OUTPUT_LINES = "OUTPUT_LINES"

    def initAlgorithm(self, config=None) -> None:
        """Defines the CalculateRouteMatrix parameters."""
        for layer_name, label, id_name, order_name in (
            (
                self.ORIGINS,
                "Origins",
                self.ORIGINS_ID_FIELD,
                self.ORIGINS_ORDER_FIELD,
            ),
            (
                self.DESTINATIONS,
                "Destinations",
                self.DESTINATIONS_ID_FIELD,
                self.DESTINATIONS_ORDER_FIELD,
            ),
        ):
            self.addParameter(
                QgsProcessingParameterFeatureSource(
                    layer_name, label, [compat.SOURCE_POINT]
                )
            )
            for field_name, field_label in (
                (id_name, f"{label} ID field"),
                (order_name, f"{label} order field"),
            ):
                self.addParameter(
                    QgsProcessingParameterField(
                        field_name,
                        field_label,
                        parentLayerParameterName=layer_name,
                        type=compat.FIELD_ANY,
                        optional=True,
                    )
                )
        self.add_travel_mode_parameter()
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, self.OPERATION, type=compat.SOURCE_TABLE
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LINES,
                "CalculateRouteMatrix (OD straight lines)",
                type=compat.SOURCE_LINE,
                optional=True,
                createByDefault=False,
            )
        )
        self.add_pricing_output()

    def execute(self, parameters, context, feedback) -> dict[str, Any]:
        """Sends CalculateRouteMatrix and writes the table and OD lines."""
        max_origins, max_destinations = routes_capabilities.matrix_axis_limits(
            self.region
        )
        origins = source_point_rows(
            self.source(parameters, self.ORIGINS, context),
            max_rows=min(max_origins, MAX_MATRIX_CELLS),
            id_field=self.field(parameters, self.ORIGINS_ID_FIELD, context),
            order_field=self.field(parameters, self.ORIGINS_ORDER_FIELD, context),
            name="Origins",
            transform_context=context.transformContext(),
            feedback=feedback,
        )
        # Stop once another destination would exceed the billed-cell cap.
        destination_cap = min(
            max_destinations, MAX_MATRIX_CELLS // max(len(origins), 1)
        )
        destinations = source_point_rows(
            self.source(parameters, self.DESTINATIONS, context),
            max_rows=max(destination_cap, 1),
            id_field=self.field(parameters, self.DESTINATIONS_ID_FIELD, context),
            order_field=self.field(parameters, self.DESTINATIONS_ORDER_FIELD, context),
            name="Destinations",
            transform_context=context.transformContext(),
            feedback=feedback,
        )
        self.raise_if_cancelled(feedback)
        travel_mode = self.travel_mode(parameters, context)
        options = MatrixOptions(
            origins=tuple(tuple(row["position"]) for row in origins),
            destinations=tuple(tuple(row["position"]) for row in destinations),
            travel_mode=travel_mode,
        )
        routes_capabilities.validate_region_options(
            self.region,
            self.OPERATION,
            travel_mode=travel_mode,
            origins_count=len(origins),
            destinations_count=len(destinations),
        )
        build_matrix_body(options)
        cells = len(origins) * len(destinations)
        self.report_estimate(
            feedback,
            f"{len(origins)} x {len(destinations)} = {cells} billed route calculations",
            billing_estimate_matrix(self.region, travel_mode),
        )

        routes = RoutesFunctions()
        result = self.send(
            routes.api_handler,
            lambda: routes.request_route_matrix(options, credentials=self.credentials),
            feedback,
        )
        rows = routes_results.normalize_matrix(result, len(origins), len(destinations))
        error_count = sum(1 for row in rows for cell in row if cell["error"])
        if error_count:
            feedback.pushWarning(
                f"CalculateRouteMatrix returned {error_count} cell error(s); "
                "their Distance and Duration are NULL."
            )
        include_lines = parameters.get(self.OUTPUT_LINES) is not None
        layers = routes_layers.build_matrix_layers(
            rows, origins, destinations, include_lines
        )
        table = layers[0]
        lines = layers[1] if len(layers) > 1 else None
        if include_lines and lines is None:
            lines = routes_layers.empty_layer(
                "LineString",
                routes_layers.LAYER_MATRIX_LINES,
                routes_layers.MATRIX_FIELDS,
            )
        feedback.pushInfo(f"CalculateRouteMatrix returned {cells} route(s).")
        return self.publish(
            parameters,
            context,
            [(self.OUTPUT, table, False), (self.OUTPUT_LINES, lines, True)],
        )


def routes_success_message(result: dict[str, Any]) -> str:
    """Summarizes the main route and the alternative count."""
    routes = [route for route in result.get("Routes") or [] if route]
    message = "CalculateRoutes finished."
    if routes:
        distance, duration = routes_results.route_totals(routes[0])
        parts = []
        if distance is not None:
            parts.append(f"{distance / 1000:.1f} km")
        if duration is not None:
            parts.append(f"{round(duration / 60)} min")
        if parts:
            message = f"{message} Main route: {', '.join(parts)}."
        if len(routes) > 1:
            message = f"{message} {len(routes) - 1} alternative(s)."
    return message


def report_route_notices(feedback: QgsProcessingFeedback, notices: list) -> None:
    """Writes every CalculateRoutes notice to the log."""
    for notice in notices:
        feedback.pushWarning(
            f"CalculateRoutes notice: code={notice['code']!r} "
            f"impact={notice['impact']!r} scope={notice['scope']} "
            f"route={notice['route_index']} leg={notice['leg_index']} "
            f"type={notice['leg_type']!r} details={notice['details']!r}"
        )


def report_snap_notices(feedback: QgsProcessingFeedback, notices: list) -> None:
    """Writes every SnapToRoads notice (Code / Title / TracePointIndexes)."""
    for notice in notices:
        feedback.pushWarning(
            f"SnapToRoads notice: code={notice.get('Code')!r} "
            f"title={notice.get('Title')!r} "
            f"trace_points={notice.get('TracePointIndexes')!r}"
        )


def report_attributions(
    feedback: QgsProcessingFeedback, attributions: list[dict[str, str]]
) -> None:
    """Writes the required data attributions to the log."""
    if not attributions:
        return
    feedback.pushInfo("Data attributions:")
    for entry in attributions:
        text = entry.get("description") or entry.get("url") or ""
        url = entry.get("url") or ""
        feedback.pushInfo(f"  {text} ({url})" if url and url != text else f"  {text}")
