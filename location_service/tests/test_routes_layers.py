import unittest
from unittest.mock import Mock, patch

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from qgis.core import (
        NULL,
        QgsCategorizedSymbolRenderer,
        QgsProject,
        QgsRuleBasedRenderer,
        QgsSingleSymbolRenderer,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtCore import Qt

    from location_service.functions import routes_layers
    from location_service.functions.routes_results import (
        LEG_TRAVEL_MODES,
        LEG_TYPES,
        BrokenResponseError,
    )

# Coordinates are [longitude, latitude].
LINE_A = [[139.70, 35.60], [139.75, 35.65]]
LINE_B = [[139.75, 35.65], [139.80, 35.70]]
SNAPPED_LINE = [[139.70, 35.60], [139.72, 35.62], [139.74, 35.64]]
SQUARE = [[139.7, 35.6], [139.8, 35.6], [139.8, 35.7], [139.7, 35.7], [139.7, 35.6]]
ISLAND = [[140.0, 35.9], [140.1, 35.9], [140.1, 36.0], [140.0, 36.0], [140.0, 35.9]]
ORIGINS = [
    {"id": "o1", "position": [139.70, 35.60]},
    {"id": "o2", "position": [139.72, 35.62]},
]
DESTINATIONS = [
    {"id": "d1", "position": [139.80, 35.70]},
    {"id": "d2", "position": [139.82, 35.72]},
    {"id": "d3", "position": [139.84, 35.74]},
]


def _is_null(value):
    """Returns whether a feature attribute came back as NULL."""
    return value is None or value == NULL


def _rgb(color):
    """Returns a QColor as an ``(red, green, blue)`` tuple."""
    return color.red(), color.green(), color.blue()


def _coordinates(geometry):
    """Returns a line geometry as ``(lon, lat)`` tuples."""
    return [(point.x(), point.y()) for point in geometry.asPolyline()]


def _vehicle_leg(line, distance=1000.0, duration=500.0):
    """Returns one drawable Vehicle leg of a CalculateRoutes response."""
    return {
        "Type": "Vehicle",
        "TravelMode": "Car",
        "Geometry": {"LineString": line},
        "VehicleLegDetails": {
            "Summary": {"Overview": {"Distance": distance, "Duration": duration}}
        },
    }


def _typed_leg(leg_type, travel_mode="Car", line=None):
    """Returns one drawable leg of that type, without any leg details."""
    return {
        "Type": leg_type,
        "TravelMode": travel_mode,
        "Geometry": {"LineString": line or LINE_A},
    }


# A RouteLeg TravelMode names the vehicle that runs the leg, so a Transit
# leg carries the mode of its service rather than the request travel mode
# "Transit", which never appears on a leg.
TRANSIT_LEG_TRAVEL_MODE = "Subway"


def _isoline(value, polygons, threshold_type="Time"):
    """Returns one normalized isoline as ``build_isoline_layer`` expects it."""
    return {
        "threshold_type": threshold_type,
        "threshold_value": value,
        "polygons": polygons,
    }


def _cell(distance=None, duration=None, error=""):
    """Returns one normalized route matrix cell."""
    return {"distance": distance, "duration": duration, "error": error}


MATRIX_ERROR = "NoRoute"


def _snapped_point(position):
    """Returns one normalized snapped trace point."""
    return {
        "index": 0,
        "confidence": 0.9,
        "snapped": position,
        "original": position,
    }


class _FakeProject:
    """
    Stands in for QgsProject and misbehaves at the requested position.

    ``addMapLayer`` refuses the layer at ``fail_at`` the way QGIS does, by
    returning None. At ``forget_at`` it reports success but never registers
    the layer, so the recorded calls show what the rollback removed again.
    """

    def __init__(self, fail_at=None, forget_at=None):
        self.fail_at = fail_at
        self.forget_at = forget_at
        self.added = []
        self.removed = []
        self.registry = {}

    def addMapLayer(self, layer):
        self.added.append(layer.id())
        if len(self.added) == self.fail_at:
            return None
        if len(self.added) != self.forget_at:
            self.registry[layer.id()] = layer
        return layer

    def mapLayer(self, layer_id):
        return self.registry.get(layer_id)

    def removeMapLayer(self, layer_id):
        self.removed.append(layer_id)
        self.registry.pop(layer_id, None)


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestRouteLegLayerStyle(unittest.TestCase):
    """A plain single-route drive keeps the v4.4 red line; anything else gets rules."""

    def test_single_vehicle_route_keeps_the_v44_red_line(self):
        layer = routes_layers.build_route_leg_layer(
            {"Routes": [{"Legs": [_vehicle_leg(LINE_A)]}]}
        )

        renderer = layer.renderer()
        assert isinstance(renderer, QgsSingleSymbolRenderer)
        symbol_layer = renderer.symbol().symbolLayer(0)
        assert _rgb(symbol_layer.color()) == (255, 0, 0)
        assert symbol_layer.width() == 2.0
        assert symbol_layer.penStyle() == Qt.PenStyle.SolidLine

    def test_alternative_routes_get_one_rule_per_route(self):
        layer = routes_layers.build_route_leg_layer(
            {
                "Routes": [
                    {"Legs": [_vehicle_leg(LINE_A)]},
                    {"Legs": [_vehicle_leg(LINE_B)]},
                ]
            }
        )

        renderer = layer.renderer()
        assert isinstance(renderer, QgsRuleBasedRenderer)
        rules = renderer.rootRule().children()
        assert [rule.label() for rule in rules] == [
            "Route 0 / Vehicle",
            "Route 1 / Vehicle",
        ]
        assert [rule.filterExpression() for rule in rules] == [
            '"RouteIndex" = 0 AND "LegType" = \'Vehicle\'',
            '"RouteIndex" = 1 AND "LegType" = \'Vehicle\'',
        ]
        assert _rgb(rules[0].symbol().symbolLayer(0).color()) == (255, 0, 0)
        assert _rgb(rules[1].symbol().symbolLayer(0).color()) != (255, 0, 0)

    def test_a_full_set_of_alternatives_gets_one_color_each(self):
        # AWS returns up to five alternatives on top of the main route, so
        # six routes must be told apart by color alone.
        assert len(routes_layers.ALTERNATIVE_COLORS) == 5

        layer = routes_layers.build_route_leg_layer(
            {"Routes": [{"Legs": [_vehicle_leg(LINE_A)]} for _ in range(6)]}
        )

        rules = layer.renderer().rootRule().children()
        colors = [_rgb(rule.symbol().symbolLayer(0).color()) for rule in rules]
        assert len(colors) == 6
        assert len(set(colors)) == 6

    def test_transit_legs_get_their_own_rule_and_pen_style(self):
        transit_leg = {
            "Type": "Transit",
            "TravelMode": TRANSIT_LEG_TRAVEL_MODE,
            "Geometry": {"LineString": LINE_B},
            "TransitLegDetails": {},
        }
        layer = routes_layers.build_route_leg_layer(
            {"Routes": [{"Legs": [_vehicle_leg(LINE_A), transit_leg]}]}
        )

        renderer = layer.renderer()
        assert isinstance(renderer, QgsRuleBasedRenderer)
        rules = {rule.label(): rule for rule in renderer.rootRule().children()}
        assert sorted(rules) == ["Route 0 / Transit", "Route 0 / Vehicle"]
        pen_styles = {
            label: rule.symbol().symbolLayer(0).penStyle()
            for label, rule in rules.items()
        }
        assert pen_styles["Route 0 / Transit"] == Qt.PenStyle.DashLine
        assert pen_styles["Route 0 / Vehicle"] == Qt.PenStyle.SolidLine


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestRouteLegLayerAttributes(unittest.TestCase):
    """Every leg row repeats its route totals so the table reads on its own."""

    def test_route_totals_and_road_names_are_repeated_on_every_leg(self):
        layer = routes_layers.build_route_leg_layer(
            {
                "Routes": [
                    {
                        "Summary": {"Distance": 3000, "Duration": 1500},
                        "MajorRoadLabels": [
                            {"RoadName": {"Value": "Main St"}},
                            {"RouteNumber": {"Value": "I-95"}},
                        ],
                        "Legs": [
                            _vehicle_leg(LINE_A, 1000.0, 500.0),
                            _vehicle_leg(LINE_B, 2000.0, 1000.0),
                        ],
                    }
                ]
            }
        )

        features = list(layer.getFeatures())
        assert [feature["LegIndex"] for feature in features] == [0, 1]
        assert [feature["LegDistance"] for feature in features] == [1000.0, 2000.0]
        assert [feature["LegDuration"] for feature in features] == [500.0, 1000.0]
        assert _coordinates(features[0].geometry()) == [
            (139.70, 35.60),
            (139.75, 35.65),
        ]
        for feature in features:
            with self.subTest(leg_index=feature["LegIndex"]):
                assert feature["RouteIndex"] == 0
                assert feature["RouteDistance"] == 3000.0
                assert feature["RouteDuration"] == 1500.0
                assert feature["RoadName"] == "Main St, I-95"
                assert feature["TravelMode"] == "Car"
                assert feature["LegType"] == "Vehicle"

    def test_transit_attributes_come_from_the_leg_details(self):
        transit_leg = {
            "Type": "Transit",
            "TravelMode": TRANSIT_LEG_TRAVEL_MODE,
            "Geometry": {"LineString": LINE_B},
            "TransitLegDetails": {
                "Agency": {"Name": "Tokyo Metro"},
                "Transport": {"RouteName": "Ginza Line", "Headsign": "Shibuya"},
                "Departure": {"Time": "2026-01-01T09:00:00Z"},
                "Arrival": {"Time": "2026-01-01T09:30:00Z"},
            },
        }

        layer = routes_layers.build_route_leg_layer(
            {"Routes": [{"Legs": [transit_leg]}]}
        )

        feature = next(layer.getFeatures())
        assert feature["Agency"] == "Tokyo Metro"
        assert feature["RouteName"] == "Ginza Line"
        assert feature["Headsign"] == "Shibuya"
        assert feature["DepartureTime"] == "2026-01-01T09:00:00Z"
        assert feature["ArrivalTime"] == "2026-01-01T09:30:00Z"
        assert feature["LegDuration"] == 1800.0
        assert _is_null(feature["LegDistance"])

    def test_every_official_leg_type_is_drawn_with_its_type(self):
        for leg_type in LEG_TYPES:
            with self.subTest(leg_type=leg_type):
                data = {"Routes": [{"Legs": [_typed_leg(leg_type)]}]}

                layer = routes_layers.build_route_leg_layer(data)

                feature = next(layer.getFeatures())
                assert feature["LegType"] == leg_type
                assert feature["TravelMode"] == "Car"

    def test_every_official_travel_mode_reaches_the_attribute_table(self):
        # A leg names the vehicle that runs it, so the mode list is much
        # longer than the six travel modes a request may ask for.
        assert len(LEG_TRAVEL_MODES) == 20
        assert {"CarShuttleTrain", "Bus"} <= set(LEG_TRAVEL_MODES)

        for travel_mode in LEG_TRAVEL_MODES:
            with self.subTest(travel_mode=travel_mode):
                leg = _typed_leg("Vehicle", travel_mode)
                data = {"Routes": [{"Legs": [leg]}]}

                layer = routes_layers.build_route_leg_layer(data)

                assert next(layer.getFeatures())["TravelMode"] == travel_mode

    def test_a_vehicle_leg_never_borrows_transit_details(self):
        leg = dict(
            _typed_leg("Vehicle"),
            TransitLegDetails={
                "Summary": {"Overview": {"Distance": 900, "Duration": 300}},
                "Agency": {"Name": "Tokyo Metro"},
                "Transport": {"RouteName": "Ginza Line"},
            },
        )

        layer = routes_layers.build_route_leg_layer({"Routes": [{"Legs": [leg]}]})

        feature = next(layer.getFeatures())
        assert _is_null(feature["LegDistance"])
        assert _is_null(feature["LegDuration"])
        assert feature["RouteName"] == ""
        assert feature["Agency"] == ""

    def test_a_response_without_routes_produces_no_layer(self):
        for data in ({}, {"Routes": []}, {"Routes": None}):
            with self.subTest(data=data):
                assert routes_layers.build_route_leg_layer(data) is None


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestRouteLegLayerRefusesBrokenRoutes(unittest.TestCase):
    """
    A leg the plugin cannot draw fails the whole layer.

    Every row repeats the totals of its whole route, so publishing the legs
    that are still drawable would show part of a route underneath full-route
    distances and durations.
    """

    def _assert_broken(self, data):
        """Asserts the response is refused instead of partly drawn."""
        with self.assertRaisesRegex(BrokenResponseError, "no layer was created"):
            routes_layers.build_route_leg_layer(data)

    def test_a_leg_without_a_geometry_fails_the_layer(self):
        self._assert_broken(
            {
                "Routes": [
                    {
                        "Legs": [
                            {"Type": "Vehicle", "TravelMode": "Car"},
                            _vehicle_leg(LINE_B),
                        ]
                    }
                ]
            }
        )

    def test_a_leg_with_an_unusable_geometry_fails_the_layer(self):
        cases = (
            {"LineString": None},
            {"LineString": []},
            {"LineString": [[139.70, 35.60]]},
            {"LineString": [[139.70, 35.60], [200.0, 35.65]]},
            {"LineString": [[139.70, 35.60], ["139.75", "35.65"]]},
        )
        for geometry in cases:
            with self.subTest(geometry=geometry):
                leg = dict(_vehicle_leg(LINE_A), Geometry=geometry)
                self._assert_broken({"Routes": [{"Legs": [leg]}]})

    def test_a_leg_that_is_not_an_object_fails_the_layer(self):
        self._assert_broken({"Routes": [{"Legs": [_vehicle_leg(LINE_A), None]}]})

    def test_a_route_that_is_not_an_object_fails_the_layer(self):
        self._assert_broken({"Routes": [{"Legs": [_vehicle_leg(LINE_A)]}, None]})

    def test_a_route_that_carries_no_legs_fails_the_layer(self):
        # Legs is a required route field, and an empty route must not be
        # counted as an alternative of the routes around it either.
        routes = ({"Legs": []}, {"Legs": None}, {"Legs": {}}, {"Legs": "Vehicle"}, {})
        for route in routes:
            with self.subTest(route=route):
                self._assert_broken({"Routes": [route]})

    def test_a_leg_type_outside_the_official_values_fails_the_layer(self):
        # A new leg type would change what the layer must show, so an
        # unknown one is refused instead of drawn as a plain line.
        for leg_type in ("Bicycle", "vehicle", "", None):
            with self.subTest(leg_type=leg_type):
                leg = dict(_typed_leg("Vehicle"), Type=leg_type)
                self._assert_broken({"Routes": [{"Legs": [leg]}]})

    def test_a_leg_without_a_travel_mode_fails_the_layer(self):
        for travel_mode in ("", None):
            with self.subTest(travel_mode=travel_mode):
                leg = dict(_typed_leg("Vehicle"), TravelMode=travel_mode)
                self._assert_broken({"Routes": [{"Legs": [leg]}]})

    def test_a_travel_mode_outside_the_official_values_fails_the_layer(self):
        # The travel mode reaches the attribute table, and the request
        # vocabulary is not the leg one: "Transit" and "Intermodal" are
        # modes a request may ask for but no leg ever comes back with.
        for travel_mode in ("Bogus", "Transit", "Intermodal", "car"):
            with self.subTest(travel_mode=travel_mode):
                leg = _typed_leg("Vehicle", travel_mode)
                self._assert_broken({"Routes": [{"Legs": [leg]}]})

    def test_a_leg_missing_its_type_or_travel_mode_fails_the_layer(self):
        for key in ("Type", "TravelMode"):
            with self.subTest(key=key):
                leg = _typed_leg("Vehicle")
                del leg[key]
                self._assert_broken({"Routes": [{"Legs": [leg]}]})

    def test_a_leg_total_no_long_can_hold_fails_the_layer(self):
        # The leg totals reach the attribute table, so a present but
        # impossible value fails the layer instead of being stored.
        self._assert_broken({"Routes": [{"Legs": [_vehicle_leg(LINE_A, -1.0)]}]})


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestRouteSummaryLayer(unittest.TestCase):
    """The summary table holds one geometry-less row per returned route."""

    def test_one_row_per_route_with_totals_and_leg_count(self):
        layer = routes_layers.build_route_summary_layer(
            {
                "Routes": [
                    {
                        "Summary": {"Distance": 3000, "Duration": 1500},
                        "MajorRoadLabels": [{"RoadName": {"Value": "Main St"}}],
                        "Legs": [_vehicle_leg(LINE_A), _vehicle_leg(LINE_B)],
                    },
                    {"Legs": [_vehicle_leg(LINE_A, 700.0, 400.0)]},
                ]
            }
        )

        assert not layer.isSpatial()
        features = list(layer.getFeatures())
        assert [feature["RouteIndex"] for feature in features] == [0, 1]
        assert [feature["LegCount"] for feature in features] == [2, 1]
        assert features[0]["RouteDistance"] == 3000.0
        assert features[0]["RouteDuration"] == 1500.0
        assert features[0]["RoadName"] == "Main St"
        assert features[1]["RouteDistance"] == 700.0
        assert features[1]["RouteDuration"] == 400.0
        assert features[1]["RoadName"] == ""

    def test_a_response_without_routes_produces_no_layer(self):
        assert routes_layers.build_route_summary_layer({"Routes": []}) is None


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestIsolineLayer(unittest.TestCase):
    """Isolines are drawn far to near so the smallest reachable area stays visible."""

    def test_larger_thresholds_are_inserted_first(self):
        layer = routes_layers.build_isoline_layer(
            [_isoline(300.0, [[SQUARE]]), _isoline(900.0, [[ISLAND]])],
            "Origin",
            "Car",
        )

        features = list(layer.getFeatures())
        assert [feature["ThresholdValue"] for feature in features] == [900.0, 300.0]
        assert [feature["Direction"] for feature in features] == ["Origin", "Origin"]
        assert [feature["ThresholdType"] for feature in features] == ["Time", "Time"]
        assert [feature["Unit"] for feature in features] == ["seconds", "seconds"]
        assert [feature["TravelMode"] for feature in features] == ["Car", "Car"]

    def test_distance_thresholds_are_labelled_in_meters(self):
        layer = routes_layers.build_isoline_layer(
            [_isoline(2000.0, [[SQUARE]], "Distance")], "Destination", "Truck"
        )

        feature = next(layer.getFeatures())
        assert feature["ThresholdType"] == "Distance"
        assert feature["Unit"] == "meters"
        assert feature["Direction"] == "Destination"
        assert feature["TravelMode"] == "Truck"

    def test_detached_areas_stay_in_one_multipolygon_feature(self):
        layer = routes_layers.build_isoline_layer(
            [_isoline(300.0, [[SQUARE], [ISLAND]])], "Origin", "Car"
        )

        assert layer.featureCount() == 1
        polygons = next(layer.getFeatures()).geometry().asMultiPolygon()
        assert len(polygons) == 2
        assert [len(polygon[0]) for polygon in polygons] == [len(SQUARE), len(ISLAND)]

    def test_renderer_categorizes_thresholds_from_green_to_red(self):
        layer = routes_layers.build_isoline_layer(
            [_isoline(300.0, [[SQUARE]]), _isoline(900.0, [[ISLAND]])],
            "Origin",
            "Car",
        )

        renderer = layer.renderer()
        assert isinstance(renderer, QgsCategorizedSymbolRenderer)
        assert renderer.classAttribute() == "ThresholdValue"
        near, far = renderer.categories()
        assert [near.value(), far.value()] == [300.0, 900.0]
        assert _rgb(near.symbol().color()) == (76, 175, 80)
        assert _rgb(far.symbol().color()) == (211, 47, 47)

    def test_no_isolines_produce_no_layer(self):
        assert routes_layers.build_isoline_layer([], "Origin", "Car") is None


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestSnapLayers(unittest.TestCase):
    """The snap layers keep the trace counts and the per-point confidence join."""

    def test_snap_line_layer_counts_the_trace_points_and_notices(self):
        layer = routes_layers.build_snap_line_layer(SNAPPED_LINE, 5, 2)

        feature = next(layer.getFeatures())
        assert feature["TracePointCount"] == 5
        assert feature["SnappedVertexCount"] == len(SNAPPED_LINE)
        assert feature["NoticeCount"] == 2
        assert _coordinates(feature.geometry()) == [
            (139.70, 35.60),
            (139.72, 35.62),
            (139.74, 35.64),
        ]

    def test_snap_points_layer_joins_the_sent_input_rows(self):
        sent = [
            {
                "id": "t1",
                "order": 1,
                "position": [139.70, 35.60],
                "timestamp": "2026-01-01T09:00:00+09:00",
                "heading": 90.0,
                "speed": 40.0,
            },
            {
                "id": "t2",
                "order": None,
                "position": [139.72, 35.62],
                "timestamp": None,
                "heading": None,
                "speed": None,
            },
        ]
        snapped = [
            {
                "index": 0,
                "confidence": 0.91,
                "snapped": [139.701, 35.601],
                "original": [139.70, 35.60],
            },
            {
                "index": 1,
                "confidence": 0.42,
                "snapped": [139.721, 35.621],
                "original": [139.72, 35.62],
            },
        ]

        layer = routes_layers.build_snap_points_layer(snapped, sent)

        features = list(layer.getFeatures())
        assert [feature["SourceID"] for feature in features] == ["t1", "t2"]
        assert [feature["SnappedIndex"] for feature in features] == [0, 1]
        assert features[0]["SourceOrder"] == "1"
        assert [feature["Confidence"] for feature in features] == [0.91, 0.42]
        assert features[0]["Timestamp"] == "2026-01-01T09:00:00+09:00"
        assert features[0]["Heading"] == 90.0
        assert features[0]["Speed"] == 40.0
        # An input row without an order keeps SourceOrder NULL; an empty
        # string would read as an order the user actually typed.
        assert _is_null(features[1]["SourceOrder"])
        assert _is_null(features[1]["Timestamp"])
        assert _is_null(features[1]["Heading"])
        point = features[0].geometry().asPoint()
        assert (point.x(), point.y()) == (139.701, 35.601)

    def test_no_snapped_points_produce_no_layer(self):
        assert routes_layers.build_snap_points_layer([], []) is None


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestMatrixLayers(unittest.TestCase):
    """The matrix table keeps every cell; only successful cells get an OD line."""

    def test_the_table_has_one_row_per_origin_destination_pair(self):
        rows = [
            [_cell(1000.0, 600.0), _cell(2000.0, 900.0), _cell(3000.0, 1200.0)],
            [_cell(1500.0, 700.0), _cell(2500.0, 1000.0), _cell(3500.0, 1300.0)],
        ]

        layers = routes_layers.build_matrix_layers(rows, ORIGINS, DESTINATIONS, False)

        assert len(layers) == 1
        table = layers[0]
        assert table.name() == routes_layers.LAYER_MATRIX
        assert not table.isSpatial()
        assert table.featureCount() == len(ORIGINS) * len(DESTINATIONS)
        features = list(table.getFeatures())
        assert [
            (feature["OriginIndex"], feature["DestinationIndex"])
            for feature in features
        ] == [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]
        assert [feature["OriginID"] for feature in features] == ["o1"] * 3 + ["o2"] * 3
        assert [feature["DestinationID"] for feature in features] == [
            "d1",
            "d2",
            "d3",
        ] * 2
        assert features[0]["Distance"] == 1000.0
        assert features[0]["Duration"] == 600.0

    def test_error_cells_keep_null_distance_and_duration(self):
        rows = [[_cell(1000.0, 600.0), _cell(error=MATRIX_ERROR)]]

        table, _lines = routes_layers.build_matrix_layers(
            rows, ORIGINS[:1], DESTINATIONS[:2], True
        )

        features = list(table.getFeatures())
        assert [feature["Error"] for feature in features] == ["", MATRIX_ERROR]
        assert features[0]["Distance"] == 1000.0
        assert _is_null(features[1]["Distance"])
        assert _is_null(features[1]["Duration"])

    def test_od_lines_are_built_only_for_successful_cells(self):
        rows = [[_cell(1000.0, 600.0), _cell(error=MATRIX_ERROR)]]

        table, lines = routes_layers.build_matrix_layers(
            rows, ORIGINS[:1], DESTINATIONS[:2], True
        )

        assert table.featureCount() == 2
        assert lines.name() == routes_layers.LAYER_MATRIX_LINES
        features = list(lines.getFeatures())
        assert len(features) == 1
        assert features[0]["DestinationID"] == "d1"
        assert _coordinates(features[0].geometry()) == [
            (139.70, 35.60),
            (139.80, 35.70),
        ]

    def test_only_failing_cells_leave_the_table_alone(self):
        layers = routes_layers.build_matrix_layers(
            [[_cell(error=MATRIX_ERROR)]], ORIGINS[:1], DESTINATIONS[:1], True
        )

        assert len(layers) == 1
        assert layers[0].name() == routes_layers.LAYER_MATRIX


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestLayerProvenance(unittest.TestCase):
    """A recorded layer stays recognizable after QGIS stringifies its properties."""

    @staticmethod
    def _layer():
        """Returns an unregistered point layer to record provenance on."""
        return QgsVectorLayer("Point?crs=EPSG:4326", "test", "memory")

    def test_recorded_layer_is_recognized_as_a_routes_layer(self):
        layer = self._layer()
        assert not routes_layers.is_routes_layer(layer)

        routes_layers.record_layer_source(layer, "CalculateRoutes")

        assert routes_layers.is_routes_layer(layer)
        assert (
            layer.customProperty(routes_layers.PROPERTY_SOURCE_OPERATION)
            == "CalculateRoutes"
        )
        assert (
            layer.customProperty(routes_layers.PROPERTY_UNITS)
            == routes_layers.UNITS_NOTE
        )

    def test_properties_read_back_as_text_are_still_recognized(self):
        layer = self._layer()
        routes_layers.record_layer_source(layer, "SnapToRoads")

        layer.setCustomProperty(routes_layers.PROPERTY_MARKER, "true")
        layer.setCustomProperty(routes_layers.PROPERTY_SCHEMA_VERSION, "1")

        assert routes_layers.is_routes_layer(layer)

    def test_another_schema_version_is_not_recognized(self):
        layer = self._layer()
        routes_layers.record_layer_source(layer, "CalculateRoutes")

        layer.setCustomProperty(
            routes_layers.PROPERTY_SCHEMA_VERSION, routes_layers.SCHEMA_VERSION + 1
        )

        assert not routes_layers.is_routes_layer(layer)

    def test_objects_that_are_not_routes_layers_are_rejected(self):
        for candidate in (None, object(), self._layer()):
            with self.subTest(candidate=type(candidate).__name__):
                assert not routes_layers.is_routes_layer(candidate)

    def test_attributions_reach_the_metadata_rights_and_the_custom_property(self):
        layer = self._layer()
        attributions = [
            {
                "type": "Transit",
                "description": "Tokyo Metro",
                "url": "https://example.test/metro",
            },
            {"type": "Taxi", "description": "", "url": "https://example.test/taxi"},
        ]

        routes_layers.record_layer_source(layer, "CalculateRoutes", attributions)

        expected = [
            "Tokyo Metro (https://example.test/metro)",
            "https://example.test/taxi",
        ]
        assert layer.metadata().rights() == expected
        assert layer.customProperty(routes_layers.PROPERTY_ATTRIBUTIONS) == "\n".join(
            expected
        )


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestFieldAliases(unittest.TestCase):
    """The attribute table spells out the stored units through field aliases."""

    @staticmethod
    def _aliases(layer):
        """Returns the layer's ``field name -> alias`` mapping."""
        return {field.name(): field.alias() for field in layer.fields()}

    def test_the_leg_layer_names_the_distance_and_duration_units(self):
        layer = routes_layers.build_route_leg_layer(
            {"Routes": [{"Legs": [_vehicle_leg(LINE_A)]}]}
        )

        aliases = self._aliases(layer)
        assert aliases["LegDistance"] == "LegDistance (m)"
        assert aliases["LegDuration"] == "LegDuration (s)"
        assert aliases["RouteDistance"] == "RouteDistance (m)"
        assert aliases["RouteDuration"] == "RouteDuration (s)"
        assert aliases["RoadName"] == ""

    def test_every_other_result_layer_aliases_its_unit_fields(self):
        table, _lines = routes_layers.build_matrix_layers(
            [[_cell(1000.0, 600.0)]], ORIGINS[:1], DESTINATIONS[:1], True
        )
        isolines = routes_layers.build_isoline_layer(
            [_isoline(300.0, [[SQUARE]])], "Origin", "Car"
        )
        sent = [
            {
                "id": "t1",
                "order": 1,
                "position": [139.70, 35.60],
                "timestamp": None,
                "heading": 90.0,
                "speed": 40.0,
            }
        ]
        points = routes_layers.build_snap_points_layer(
            [_snapped_point([139.70, 35.60])], sent
        )
        cases = (
            (table, "Distance", "Distance (m)"),
            (table, "Duration", "Duration (s)"),
            (isolines, "ThresholdValue", "ThresholdValue (s or m)"),
            (points, "Heading", "Heading (deg)"),
            (points, "Speed", "Speed (km/h)"),
        )
        for layer, name, alias in cases:
            with self.subTest(layer=layer.name(), field=name):
                assert self._aliases(layer)[name] == alias


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestPublishLayers(unittest.TestCase):
    """Publishing is all or nothing: a failure leaves the project as it was."""

    def setUp(self):
        self.project = QgsProject.instance()
        self.before = set(self.project.mapLayers())

    def test_valid_layers_are_added_together(self):
        first = QgsVectorLayer("LineString?crs=EPSG:4326", "first", "memory")
        second = QgsVectorLayer("None", "second", "memory")

        routes_layers.publish_layers([first, second])
        self.addCleanup(self.project.removeMapLayer, second.id())
        self.addCleanup(self.project.removeMapLayer, first.id())

        added = set(self.project.mapLayers()) - self.before
        assert added == {first.id(), second.id()}

    def test_an_invalid_layer_stops_the_whole_publish(self):
        valid = QgsVectorLayer("LineString?crs=EPSG:4326", "valid", "memory")
        invalid = QgsVectorLayer("/no/such/directory/missing.gpkg", "invalid", "ogr")
        assert not invalid.isValid()

        with self.assertRaisesRegex(RuntimeError, "valid result layer"):
            routes_layers.publish_layers([valid, invalid])

        assert set(self.project.mapLayers()) == self.before

    def test_a_refused_layer_rolls_back_the_layers_already_added(self):
        first = QgsVectorLayer("LineString?crs=EPSG:4326", "first", "memory")
        second = QgsVectorLayer("None", "second", "memory")
        project = _FakeProject(fail_at=2)

        with (
            patch.object(routes_layers.QgsProject, "instance", return_value=project),
            self.assertRaisesRegex(RuntimeError, "add a result layer"),
        ):
            routes_layers.publish_layers([first, second])

        assert project.added == [first.id(), second.id()]
        assert project.removed == [first.id()]
        assert set(self.project.mapLayers()) == self.before

    def test_a_layer_missing_from_the_registry_rolls_back_the_publish(self):
        first = QgsVectorLayer("LineString?crs=EPSG:4326", "first", "memory")
        second = QgsVectorLayer("None", "second", "memory")
        # The project reports the second layer as added but never registers
        # it, which addMapLayer alone cannot tell apart from a real success.
        project = _FakeProject(forget_at=2)

        with (
            patch.object(routes_layers.QgsProject, "instance", return_value=project),
            self.assertRaisesRegex(RuntimeError, "add a result layer"),
        ):
            routes_layers.publish_layers([first, second])

        assert project.added == [first.id(), second.id()]
        assert project.removed == [first.id(), second.id()]
        assert set(self.project.mapLayers()) == self.before


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestProviderPartialWrites(unittest.TestCase):
    """
    A provider that reports success but stores less fails the build.

    The memory provider used for the result layers accepts fields and
    features with a boolean, so the counts are compared as well: a layer
    with a missing column or a missing row must never be published.
    """

    @staticmethod
    def _route_data():
        """Returns a response with exactly one drawable leg."""
        return {"Routes": [{"Legs": [_vehicle_leg(LINE_A)]}]}

    @staticmethod
    def _line_layer():
        """Returns an empty line layer to stand in for the built one."""
        return QgsVectorLayer("LineString?crs=EPSG:4326", "partial", "memory")

    def test_fields_the_provider_dropped_fail_the_build(self):
        layer = self._line_layer()
        provider = Mock(wraps=layer.dataProvider())
        provider.addAttributes.return_value = True

        with (
            patch.object(layer, "dataProvider", return_value=provider),
            patch.object(routes_layers, "_memory_layer", return_value=layer),
            self.assertRaisesRegex(RuntimeError, "Could not add fields"),
        ):
            routes_layers.build_route_leg_layer(self._route_data())

    def test_features_the_provider_dropped_fail_the_build(self):
        layer = self._line_layer()
        provider = Mock(wraps=layer.dataProvider())
        # The fields are stored for real; only the feature is dropped.
        provider.addFeatures.return_value = (True, [])

        with (
            patch.object(layer, "dataProvider", return_value=provider),
            patch.object(routes_layers, "_memory_layer", return_value=layer),
            self.assertRaisesRegex(RuntimeError, "Could not add features"),
        ):
            routes_layers.build_route_leg_layer(self._route_data())

    def test_a_provider_that_refuses_the_fields_fails_the_build(self):
        layer = self._line_layer()
        provider = Mock(wraps=layer.dataProvider())
        provider.addAttributes.return_value = False

        with (
            patch.object(layer, "dataProvider", return_value=provider),
            patch.object(routes_layers, "_memory_layer", return_value=layer),
            self.assertRaisesRegex(RuntimeError, "Could not add fields"),
        ):
            routes_layers.build_route_leg_layer(self._route_data())


if __name__ == "__main__":
    unittest.main()
