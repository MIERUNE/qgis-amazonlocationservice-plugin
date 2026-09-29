import gc
import json
import unittest
import weakref
from contextlib import contextmanager
from unittest.mock import patch

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from qgis.core import (
        QgsFeature,
        QgsField,
        QgsGeometry,
        QgsPointXY,
        QgsProcessingContext,
        QgsProcessingFeedback,
        QgsProcessingOutputLayerDefinition,
        QgsProcessingUtils,
        QgsProject,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtCore import QDateTime, Qt, QVariant

    from location_service.functions import routes_layers
    from location_service.functions.places import PlacesFunctions
    from location_service.functions.routes_requests import (
        MAX_WAYPOINTS,
        ROUTES_TRAVEL_MODES,
    )
    from location_service.processing_provider.base import LayerStyle
    from location_service.processing_provider.inputs import iso_time_with_offset
    from location_service.processing_provider.places_algorithms import (
        GeocodeAlgorithm,
        GetPlaceAlgorithm,
        ReverseGeocodeAlgorithm,
        SearchNearbyAlgorithm,
        SearchTextAlgorithm,
    )
    from location_service.processing_provider.provider import (
        PROVIDER_ID,
        LocationServiceProvider,
    )
    from location_service.processing_provider.routes_algorithms import (
        TIME_CHOICES,
        CalculateIsolinesAlgorithm,
        CalculateRouteMatrixAlgorithm,
        CalculateRoutesAlgorithm,
        SnapToRoadsAlgorithm,
    )
    from location_service.processing_provider.runner import (
        AlgorithmRun,
        temporary_output,
        wgs84_point,
    )
    from location_service.utils.configuration_handler import (
        ConfigurationError,
        ConfigurationHandler,
    )
    from location_service.utils.external_api_handler import ApiError, ExternalApiHandler

    class _Feedback(QgsProcessingFeedback):
        """Collects the messages an algorithm reports."""

        def __init__(self):
            super().__init__()
            self.errors = []
            self.warnings = []
            self.infos = []

        def reportError(self, error, fatal_error=False):
            self.errors.append(error)

        def pushWarning(self, warning):
            self.warnings.append(warning)

        def pushInfo(self, info):
            self.infos.append(info)


CREDENTIALS = ("ap-northeast-1", "v1.public.test")  # pragma: allowlist secret
GRAB_CREDENTIALS = ("ap-southeast-1", "v1.public.test")  # pragma: allowlist secret
TOKYO = "139.7,35.6 [EPSG:4326]"
SHINJUKU = "139.8,35.7 [EPSG:4326]"


def _point_layer(points, fields=(), name="points"):
    """Returns a memory point layer with ``(x, y, {field: value})`` rows."""
    layer = QgsVectorLayer("Point?crs=EPSG:4326", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField(field, kind) for field, kind in fields])
    layer.updateFields()
    features = []
    for x, y, values in points:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        for key, value in values.items():
            feature[key] = value
        features.append(feature)
    provider.addFeatures(features)
    return layer


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class ProcessingTestCase(unittest.TestCase):
    """Runs algorithms synchronously with fake credentials and API replies."""

    def setUp(self):
        self.project = QgsProject.instance()
        self.context = QgsProcessingContext()
        self.context.setProject(self.project)
        self.feedback = _Feedback()
        self.posts = []
        self.gets = []
        self.responses = []
        self.credentials = CREDENTIALS
        self.layers = []

    def tearDown(self):
        for layer in self.layers:
            self.project.removeMapLayer(layer.id())

    def add_layer(self, layer):
        """Adds an input layer to the project for the duration of a test."""
        self.project.addMapLayer(layer)
        self.layers.append(layer)
        return layer

    def _post(self, url, body):
        self.posts.append((url, body))
        return self.responses.pop(0)

    def _get(self, url):
        self.gets.append(url)
        return self.responses.pop(0)

    @contextmanager
    def fake_service(self):
        """Replaces the credential lookup and the network calls."""
        with (
            patch.object(
                ConfigurationHandler,
                "get_credentials",
                side_effect=lambda: self.credentials,
            ),
            patch.object(
                ExternalApiHandler, "send_json_post_request", side_effect=self._post
            ),
            patch.object(
                ExternalApiHandler, "send_json_get_request", side_effect=self._get
            ),
        ):
            yield

    def run_algorithm(self, algorithm_class, parameters):
        """Runs the algorithm and returns ``(results, ok)``."""
        algorithm = algorithm_class().create()
        with self.fake_service():
            results, ok = algorithm.run(parameters, self.context, self.feedback)
        return results, ok

    def output(self, results, name="OUTPUT"):
        """Returns an output layer of a finished run."""
        return QgsProcessingUtils.mapLayerFromString(results[name], self.context)


class TestProvider(ProcessingTestCase):
    """Checks the provider and algorithm metadata."""

    def test_provider_lists_every_operation(self):
        provider = LocationServiceProvider()
        provider.loadAlgorithms()
        ids = sorted(algorithm.name() for algorithm in provider.algorithms())
        assert provider.id() == PROVIDER_ID
        assert ids == [
            "calculateisolines",
            "calculateroutematrix",
            "calculateroutes",
            "geocode",
            "getplace",
            "reversegeocode",
            "searchnearby",
            "searchtext",
            "snaptoroads",
        ]
        groups = {algorithm.groupId() for algorithm in provider.algorithms()}
        assert groups == {"places", "routes"}

    def test_missing_configuration_fails_before_any_request(self):
        algorithm = SearchTextAlgorithm().create()
        with (
            patch.object(
                ConfigurationHandler,
                "get_credentials",
                side_effect=ConfigurationError("Missing region"),
            ),
            patch.object(ExternalApiHandler, "send_json_post_request") as post,
        ):
            _results, ok = algorithm.run(
                {"QUERY": "cafe", "BIAS_POSITION": TOKYO, "OUTPUT": "memory:"},
                self.context,
                self.feedback,
            )
        assert not ok
        assert "Config menu" in " ".join(self.feedback.errors)
        post.assert_not_called()


class TestPlacesAlgorithms(ProcessingTestCase):
    """Covers the Places algorithms end to end without the network."""

    def test_search_text_sends_a_storage_request_and_writes_points(self):
        self.responses = [
            {
                "ResultItems": [
                    {"Title": "Cafe", "PlaceId": "p1", "Position": [139.7, 35.6]},
                    {"Title": "No position", "PlaceId": "p2"},
                ]
            }
        ]
        results, ok = self.run_algorithm(
            SearchTextAlgorithm,
            {
                "QUERY": " cafe ",
                "BIAS_POSITION": TOKYO,
                "COUNTRIES": "jp, JPN",
                "TRAVEL_MODE": 2,
                "LANGUAGE": "ja",
                "POLITICAL_VIEW": 0,
                "OUTPUT": "memory:",
            },
        )
        assert ok, self.feedback.errors
        url, body = self.posts[0]
        assert url.startswith("https://places.geo.ap-northeast-1.amazonaws.com/")
        assert body["QueryText"] == "cafe"
        assert body["BiasPosition"] == [139.7, 35.6]
        assert body["Filter"] == {"IncludeCountries": ["JP", "JPN"]}
        assert body["TravelMode"] == "Scooter"
        assert body["Language"] == "ja"
        assert body["IntendedUse"] == "Storage"
        assert body["AdditionalFeatures"] == ["Contact", "TimeZone"]
        layer = self.output(results)
        assert [feature["Title"] for feature in layer.getFeatures()] == ["Cafe"]
        assert layer.fields().indexOf(PlacesFunctions.FIELD_PHONE) >= 0

    def test_bias_position_is_transformed_to_wgs84(self):
        self.responses = [{"ResultItems": []}]
        _results, ok = self.run_algorithm(
            SearchTextAlgorithm,
            {
                "QUERY": "cafe",
                "BIAS_POSITION": "15551000,4254000 [EPSG:3857]",
                "OUTPUT": "memory:",
            },
        )
        assert ok, self.feedback.errors
        lon, lat = self.posts[0][1]["BiasPosition"]
        assert abs(lon - 139.7) < 0.01
        assert abs(lat - 35.6) < 0.1
        assert any("no drawable results" in text for text in self.feedback.warnings)

    def test_pages_are_appended_without_duplicate_place_ids(self):
        self.responses = [
            {
                "ResultItems": [
                    {"Title": "A", "PlaceId": "a", "Position": [139.7, 35.6]}
                ],
                "NextToken": "token-1",
            },
            {
                "ResultItems": [
                    {"Title": "A again", "PlaceId": "a", "Position": [139.7, 35.6]},
                    {"Title": "B", "PlaceId": "b", "Position": [139.8, 35.6]},
                ],
            },
        ]
        results, ok = self.run_algorithm(
            SearchNearbyAlgorithm,
            {
                "POSITION": TOKYO,
                "QUERY_RADIUS": 500,
                "PAGES": 3,
                "OUTPUT": "memory:",
            },
        )
        assert ok, self.feedback.errors
        assert len(self.posts) == 2
        assert "NextToken" not in self.posts[0][1]
        assert self.posts[1][1]["NextToken"] == "token-1"
        titles = [feature["Title"] for feature in self.output(results).getFeatures()]
        assert titles == ["A", "B"]

    def test_limited_region_rejects_geocode_before_sending(self):
        self.credentials = GRAB_CREDENTIALS
        _results, ok = self.run_algorithm(
            GeocodeAlgorithm, {"QUERY": "Singapore", "OUTPUT": "memory:"}
        )
        assert not ok
        assert "Geocode is not supported" in " ".join(self.feedback.errors)
        assert self.posts == []

    def test_geocode_bias_position_is_optional(self):
        self.responses = [{"ResultItems": []}]
        _results, ok = self.run_algorithm(
            GeocodeAlgorithm,
            {"QUERY": "Tokyo Station", "POSTAL_CODE_MODE": 1, "OUTPUT": "memory:"},
        )
        assert ok, self.feedback.errors
        body = self.posts[0][1]
        assert "BiasPosition" not in body
        assert body["PostalCodeMode"] == "MergeAllSpannedLocalities"

    def test_reverse_geocode_radius_zero_is_unset(self):
        self.responses = [{"ResultItems": []}]
        _results, ok = self.run_algorithm(
            ReverseGeocodeAlgorithm,
            {"POSITION": TOKYO, "QUERY_RADIUS": 0, "OUTPUT": "memory:"},
        )
        assert ok, self.feedback.errors
        body = self.posts[0][1]
        assert "QueryRadius" not in body
        assert body["MaxResults"] == 1

    def test_invalid_language_is_rejected(self):
        _results, ok = self.run_algorithm(
            SearchTextAlgorithm,
            {
                "QUERY": "cafe",
                "BIAS_POSITION": TOKYO,
                "LANGUAGE": "not a language",
                "OUTPUT": "memory:",
            },
        )
        assert not ok
        assert "BCP 47" in " ".join(self.feedback.errors)
        assert self.posts == []

    def test_get_place_copies_the_input_with_details(self):
        layer = self.add_layer(
            _point_layer(
                [
                    (139.7, 35.6, {"PlaceId": "p1", "Phone": "old"}),
                    (139.8, 35.6, {"PlaceId": "p1", "Phone": "old"}),
                    (139.9, 35.6, {"PlaceId": "p2", "Phone": "old"}),
                ],
                (("PlaceId", QVariant.String), ("Phone", QVariant.String)),
            )
        )
        self.responses = [
            {
                "Contacts": {"Phones": [{"Value": "03-1111"}]},
                "TimeZone": {"Name": "Asia/Tokyo"},
            },
            {"Contacts": {"Websites": [{"Value": "https://two.example.com"}]}},
        ]
        results, ok = self.run_algorithm(
            GetPlaceAlgorithm, {"INPUT": layer.id(), "OUTPUT": "memory:"}
        )
        assert ok, self.feedback.errors
        assert len(self.gets) == 2
        assert all("intended-use=Storage" in url for url in self.gets)
        rows = [
            (feature["PlaceId"], feature["Phone"], feature["Website"])
            for feature in self.output(results).getFeatures()
        ]
        assert rows == [
            ("p1", "03-1111", ""),
            ("p1", "03-1111", ""),
            ("p2", "", "https://two.example.com"),
        ]
        assert [feature["Phone"] for feature in layer.getFeatures()] == ["old"] * 3

    def test_get_place_caps_the_billed_requests(self):
        count = PlacesFunctions.MAX_ENRICH_FEATURES + 1
        layer = self.add_layer(
            _point_layer(
                [(139.7, 35.6, {"PlaceId": f"p{index}"}) for index in range(count)],
                (("PlaceId", QVariant.String),),
            )
        )
        _results, ok = self.run_algorithm(
            GetPlaceAlgorithm, {"INPUT": layer.id(), "OUTPUT": "memory:"}
        )
        assert not ok
        assert f"{count} unique places" in " ".join(self.feedback.errors)
        assert self.gets == []

    def test_get_place_rejects_a_missing_place_id(self):
        layer = self.add_layer(
            _point_layer(
                [(139.7, 35.6, {"PlaceId": "p1"}), (139.8, 35.6, {})],
                (("PlaceId", QVariant.String),),
            )
        )
        _results, ok = self.run_algorithm(
            GetPlaceAlgorithm, {"INPUT": layer.id(), "OUTPUT": "memory:"}
        )
        assert not ok
        assert "no PlaceId" in " ".join(self.feedback.errors)
        assert self.gets == []

    def test_cancelled_run_sends_nothing(self):
        self.feedback.cancel()
        _results, ok = self.run_algorithm(
            SearchTextAlgorithm,
            {"QUERY": "cafe", "BIAS_POSITION": TOKYO, "OUTPUT": "memory:"},
        )
        assert not ok
        assert self.posts == []


def _route_response():
    return {
        "Routes": [
            {
                "Legs": [
                    {
                        "Type": "Vehicle",
                        "TravelMode": "Car",
                        "Geometry": {"LineString": [[139.7, 35.6], [139.8, 35.7]]},
                        "VehicleLegDetails": {
                            "Summary": {"Overview": {"Distance": 1000, "Duration": 60}}
                        },
                    }
                ],
                "Summary": {"Distance": 1000, "Duration": 60},
            }
        ]
    }


class TestRoutesAlgorithms(ProcessingTestCase):
    """Covers the Routes algorithms end to end without the network."""

    def test_calculate_routes_uses_ordered_waypoints(self):
        waypoints = self.add_layer(
            _point_layer(
                [
                    (139.75, 35.65, {"seq": 2}),
                    (139.72, 35.62, {"seq": 1}),
                ],
                (("seq", QVariant.Int),),
            )
        )
        self.responses = [_route_response()]
        results, ok = self.run_algorithm(
            CalculateRoutesAlgorithm,
            {
                "ORIGIN": TOKYO,
                "DESTINATION": SHINJUKU,
                "WAYPOINTS": waypoints.id(),
                "WAYPOINTS_ORDER_FIELD": "seq",
                "AVOID": [0, 1],
                "OUTPUT": QgsProcessingOutputLayerDefinition("memory:", self.project),
                "OUTPUT_SUMMARY": "memory:",
            },
        )
        assert ok, self.feedback.errors
        url, body = self.posts[0]
        assert url.startswith("https://routes.geo.ap-northeast-1.amazonaws.com/")
        assert body["Waypoints"] == [
            {"Position": [139.72, 35.62]},
            {"Position": [139.75, 35.65]},
        ]
        assert body["Avoid"] == {"TollRoads": True, "Ferries": True}
        legs = self.output(results)
        assert legs.featureCount() == 1
        assert self.output(results, "OUTPUT_SUMMARY").featureCount() == 1

        # The loaded output is named and styled like the v4.x layer.
        details = self.context.layerToLoadOnCompletionDetails(results["OUTPUT"])
        assert details.name == "CalculateRoutes"
        details.postProcessor().postProcessLayer(legs, self.context, self.feedback)
        assert legs.customProperty("als_routes/source_operation") == "CalculateRoutes"
        assert legs.fields().field("LegDistance").alias() == "LegDistance (m)"

    def test_transit_ignores_waypoints_and_avoid(self):
        waypoints = self.add_layer(_point_layer([(139.75, 35.65, {})]))
        self.responses = [_route_response()]
        results, ok = self.run_algorithm(
            CalculateRoutesAlgorithm,
            {
                "ORIGIN": TOKYO,
                "DESTINATION": SHINJUKU,
                "TRAVEL_MODE": 4,
                "WAYPOINTS": waypoints.id(),
                "AVOID": [0],
                "OUTPUT": "memory:",
            },
        )
        assert ok, self.feedback.errors
        body = self.posts[0][1]
        assert body["TravelMode"] == "Transit"
        assert "Waypoints" not in body
        assert "Avoid" not in body
        assert "OUTPUT_SUMMARY" not in results
        assert len(self.feedback.warnings) >= 2

    def test_departure_time_requires_a_value(self):
        _results, ok = self.run_algorithm(
            CalculateRoutesAlgorithm,
            {
                "ORIGIN": TOKYO,
                "DESTINATION": SHINJUKU,
                "TIME_CHOICE": 2,
                "OUTPUT": "memory:",
            },
        )
        assert not ok
        assert self.posts == []

    def test_grab_region_rejects_isolines(self):
        self.credentials = GRAB_CREDENTIALS
        _results, ok = self.run_algorithm(
            CalculateIsolinesAlgorithm,
            {"CENTER": TOKYO, "THRESHOLDS": "5", "OUTPUT": "memory:"},
        )
        assert not ok
        assert "not supported" in " ".join(self.feedback.errors)
        assert self.posts == []

    def test_isolines_convert_minutes_and_write_polygons(self):
        ring = [[139.7, 35.6], [139.8, 35.6], [139.8, 35.7], [139.7, 35.6]]
        self.responses = [
            {
                "Isolines": [
                    {"TimeThreshold": 300, "Geometries": [{"Polygon": [ring]}]},
                    {"TimeThreshold": 600, "Geometries": [{"Polygon": [ring]}]},
                ]
            }
        ]
        results, ok = self.run_algorithm(
            CalculateIsolinesAlgorithm,
            {"CENTER": TOKYO, "THRESHOLDS": "5, 10", "OUTPUT": "memory:"},
        )
        assert ok, self.feedback.errors
        assert self.posts[0][1]["Thresholds"] == {"Time": [300, 600]}
        assert self.output(results).featureCount() == 2

    def test_snap_to_roads_writes_line_and_confidence_points(self):
        trace = self.add_layer(
            _point_layer(
                [
                    (139.71, 35.61, {"seq": 2, "name": "b"}),
                    (139.70, 35.60, {"seq": 1, "name": "a"}),
                ],
                (("seq", QVariant.Int), ("name", QVariant.String)),
            )
        )
        self.responses = [
            {
                "SnappedGeometry": {"LineString": [[139.70, 35.60], [139.71, 35.61]]},
                "SnappedTracePoints": [
                    {
                        "OriginalPosition": [139.70, 35.60],
                        "SnappedPosition": [139.70, 35.60],
                        "Confidence": 0.9,
                    },
                    {
                        "OriginalPosition": [139.71, 35.61],
                        "SnappedPosition": [139.71, 35.61],
                        "Confidence": 0.8,
                    },
                ],
            }
        ]
        results, ok = self.run_algorithm(
            SnapToRoadsAlgorithm,
            {
                "INPUT": trace.id(),
                "ID_FIELD": "name",
                "ORDER_FIELD": "seq",
                "OUTPUT": "memory:",
                "OUTPUT_POINTS": "memory:",
            },
        )
        assert ok, self.feedback.errors
        positions = [point["Position"] for point in self.posts[0][1]["TracePoints"]]
        assert positions == [[139.70, 35.60], [139.71, 35.61]]
        assert self.output(results).featureCount() == 1
        points = self.output(results, "OUTPUT_POINTS")
        assert [feature["SourceID"] for feature in points.getFeatures()] == ["a", "b"]

    def test_route_matrix_writes_table_and_optional_lines(self):
        origins = self.add_layer(_point_layer([(139.7, 35.6, {})]))
        destinations = self.add_layer(
            _point_layer([(139.8, 35.7, {}), (139.9, 35.8, {})])
        )
        self.responses = [
            {
                "RouteMatrix": [
                    [
                        {"Distance": 100, "Duration": 10},
                        {"Distance": 0, "Duration": 0, "Error": "NoRoute"},
                    ]
                ]
            }
        ]
        results, ok = self.run_algorithm(
            CalculateRouteMatrixAlgorithm,
            {
                "ORIGINS": origins.id(),
                "DESTINATIONS": destinations.id(),
                "OUTPUT": "memory:",
                "OUTPUT_LINES": "memory:",
            },
        )
        assert ok, self.feedback.errors
        table = self.output(results)
        assert table.featureCount() == 2
        assert self.output(results, "OUTPUT_LINES").featureCount() == 1
        assert any("1 cell error" in text for text in self.feedback.warnings)

    def test_route_matrix_rejects_too_many_cells_before_sending(self):
        origins = self.add_layer(
            _point_layer([(139.7 + index * 0.001, 35.6, {}) for index in range(11)])
        )
        destinations = self.add_layer(
            _point_layer([(139.8 + index * 0.001, 35.7, {}) for index in range(10)])
        )
        _results, ok = self.run_algorithm(
            CalculateRouteMatrixAlgorithm,
            {
                "ORIGINS": origins.id(),
                "DESTINATIONS": destinations.id(),
                "OUTPUT": "memory:",
            },
        )
        assert not ok
        assert self.posts == []


def _snap_response(with_line=True):
    """Returns a SnapToRoads reply for (139.70, 35.60) and (139.71, 35.61)."""
    response = {
        "SnappedTracePoints": [
            {
                "OriginalPosition": [139.70, 35.60],
                "SnappedPosition": [139.70, 35.60],
                "Confidence": 0.9,
            },
            {
                "OriginalPosition": [139.71, 35.61],
                "SnappedPosition": [139.71, 35.61],
                "Confidence": 0.8,
            },
        ]
    }
    if with_line:
        response["SnappedGeometry"] = {"LineString": [[139.70, 35.60], [139.71, 35.61]]}
    return response


class TestRoutesInputsAndOutputs(ProcessingTestCase):
    """Time zones, ignored inputs, empty outputs and output names (Routes)."""

    DEPART_NOW = [value for _label, value in TIME_CHOICES].index("depart_now")
    DEPARTURE = [value for _label, value in TIME_CHOICES].index("departure_time")
    TRANSIT = ROUTES_TRAVEL_MODES.index("Transit")
    INTERMODAL = ROUTES_TRAVEL_MODES.index("Intermodal")

    def run_routes(self, extra, response=None):
        """Runs CalculateRoutes from Tokyo to Shinjuku with extra parameters."""
        self.responses = [_route_response() if response is None else response]
        parameters = {"ORIGIN": TOKYO, "DESTINATION": SHINJUKU, "OUTPUT": "memory:"}
        parameters.update(extra)
        return self.run_algorithm(CalculateRoutesAlgorithm, parameters)

    def test_a_departure_time_keeps_the_offset_of_the_text(self):
        for text, sent in (
            ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00+00:00"),
            ("2025-12-31T19:00:00-05:00", "2025-12-31T19:00:00-05:00"),
        ):
            with self.subTest(text=text):
                self.posts = []
                _results, ok = self.run_routes(
                    {"TIME_CHOICE": self.DEPARTURE, "TIME": text}
                )
                assert ok, self.feedback.errors
                assert self.posts[0][1]["DepartureTime"] == sent

    def test_a_time_without_a_choice_is_ignored_with_a_warning(self):
        for choice in (0, self.DEPART_NOW):
            with self.subTest(choice=choice):
                self.posts = []
                self.feedback.warnings.clear()
                _results, ok = self.run_routes(
                    {"TIME_CHOICE": choice, "TIME": "2026-01-01T00:00:00Z"}
                )
                assert ok, self.feedback.errors
                body = self.posts[0][1]
                assert "DepartureTime" not in body
                assert "ArrivalTime" not in body
                assert any(
                    "time was ignored" in text for text in self.feedback.warnings
                )

    def test_a_field_name_matches_like_qgis_does(self):
        waypoints = self.add_layer(
            _point_layer(
                [(139.75, 35.65, {"seq": 2}), (139.72, 35.62, {"seq": 1})],
                (("seq", QVariant.Int),),
            )
        )
        _results, ok = self.run_routes(
            {"WAYPOINTS": waypoints.id(), "WAYPOINTS_ORDER_FIELD": "SEQ"}
        )
        assert ok, self.feedback.errors
        positions = [point["Position"] for point in self.posts[0][1]["Waypoints"]]
        assert positions == [[139.72, 35.62], [139.75, 35.65]]

    def test_datetime_timestamps_keep_their_instant(self):
        start = QDateTime.fromString("2026-01-01T00:00:00Z", Qt.DateFormat.ISODate)
        trace = self.add_layer(
            _point_layer(
                [
                    (139.70, 35.60, {"t": start}),
                    (139.71, 35.61, {"t": start.addSecs(60)}),
                ],
                (("t", QVariant.DateTime),),
            )
        )
        self.responses = [_snap_response()]
        _results, ok = self.run_algorithm(
            SnapToRoadsAlgorithm,
            {"INPUT": trace.id(), "TIMESTAMP_FIELD": "t", "OUTPUT": "memory:"},
        )
        assert ok, self.feedback.errors
        sent = [
            QDateTime.fromString(point["Timestamp"], Qt.DateFormat.ISODate)
            for point in self.posts[0][1]["TracePoints"]
        ]
        assert sent == [start, start.addSecs(60)]

    def test_a_datetime_order_field_sorts_chronologically(self):
        start = QDateTime.fromString("2026-09-29T09:05:00Z", Qt.DateFormat.ISODate)
        waypoints = self.add_layer(
            _point_layer(
                [
                    (139.72, 35.62, {"t": start.addSecs(55 * 60)}),
                    (139.70, 35.60, {"t": start}),
                    (139.71, 35.61, {"t": start.addSecs(5 * 60)}),
                ],
                (("t", QVariant.DateTime),),
            )
        )
        _results, ok = self.run_routes(
            {"WAYPOINTS": waypoints.id(), "WAYPOINTS_ORDER_FIELD": "t"}
        )
        assert ok, self.feedback.errors
        positions = [point["Position"] for point in self.posts[0][1]["Waypoints"]]
        assert positions == [[139.70, 35.60], [139.71, 35.61], [139.72, 35.62]]

    def test_an_unknown_field_name_is_reported_before_sending(self):
        waypoints = self.add_layer(_point_layer([(139.75, 35.65, {})]))
        _results, ok = self.run_routes(
            {"WAYPOINTS": waypoints.id(), "WAYPOINTS_ORDER_FIELD": "missing"}
        )
        assert not ok
        assert any("no field 'missing'" in text for text in self.feedback.errors)
        assert self.posts == []

    def test_transit_does_not_read_the_ignored_waypoint_layer(self):
        too_many = self.add_layer(
            _point_layer(
                [(139.70 + i * 0.001, 35.60, {}) for i in range(MAX_WAYPOINTS + 1)]
            )
        )
        _results, ok = self.run_routes(
            {"TRAVEL_MODE": self.TRANSIT, "WAYPOINTS": too_many.id()}
        )
        assert ok, self.feedback.errors
        assert "Waypoints" not in self.posts[0][1]
        assert any("Waypoints do not apply" in text for text in self.feedback.warnings)

    def test_intermodal_reports_the_ignored_transit_filter_and_optimize_for(self):
        _results, ok = self.run_routes(
            {
                "TRAVEL_MODE": self.INTERMODAL,
                "TRANSIT_FILTER": 1,
                "TRANSIT_MODES": [0],
                "OPTIMIZE_FOR": 1,
            }
        )
        assert ok, self.feedback.errors
        warnings = " ".join(self.feedback.warnings)
        assert "transit mode filter" in warnings
        assert "Optimize for" in warnings

    def test_empty_routes_still_write_the_requested_summary(self):
        results, ok = self.run_routes(
            {"OUTPUT_SUMMARY": "memory:"}, response={"Routes": []}
        )
        assert ok, self.feedback.errors
        summary = self.output(results, "OUTPUT_SUMMARY")
        assert summary.featureCount() == 0
        expected = [name for name, _kind in routes_layers.ROUTE_SUMMARY_FIELDS]
        assert summary.fields().names() == expected

    def test_an_unrequested_summary_is_still_omitted(self):
        results, ok = self.run_routes({}, response={"Routes": []})
        assert ok, self.feedback.errors
        assert "OUTPUT_SUMMARY" not in results

    def test_snap_without_a_line_still_writes_the_requested_points(self):
        trace = self.add_layer(_point_layer([(139.70, 35.60, {}), (139.71, 35.61, {})]))
        self.responses = [_snap_response(with_line=False)]
        results, ok = self.run_algorithm(
            SnapToRoadsAlgorithm,
            {"INPUT": trace.id(), "OUTPUT": "memory:", "OUTPUT_POINTS": "memory:"},
        )
        assert ok, self.feedback.errors
        assert self.output(results).featureCount() == 0
        assert self.output(results, "OUTPUT_POINTS").featureCount() == 2
        assert any("no snapped line" in text for text in self.feedback.warnings)

    def test_a_chosen_output_name_is_kept(self):
        chosen = QgsProcessingOutputLayerDefinition("memory:", self.project)
        chosen.destinationName = "Commute legs"
        results, ok = self.run_routes({"OUTPUT": chosen})
        assert ok, self.feedback.errors
        details = self.context.layerToLoadOnCompletionDetails(results["OUTPUT"])
        assert details.name == "Commute legs"

    def test_a_default_output_name_becomes_the_layer_name(self):
        results, ok = self.run_routes(
            {"OUTPUT": QgsProcessingOutputLayerDefinition("memory:", self.project)}
        )
        assert ok, self.feedback.errors
        details = self.context.layerToLoadOnCompletionDetails(results["OUTPUT"])
        assert details.name == routes_layers.LAYER_ROUTES


class TestLayerPostProcessors(ProcessingTestCase):
    """Checks that pending outputs keep their styles until QGIS loads them."""

    def create_output(self):
        self.responses = [
            {
                "ResultItems": [
                    {"Title": "Cafe", "PlaceId": "p1", "Position": [139.7, 35.6]}
                ]
            }
        ]
        results, ok = self.run_algorithm(
            SearchTextAlgorithm,
            {
                "QUERY": "cafe",
                "BIAS_POSITION": TOKYO,
                "OUTPUT": QgsProcessingOutputLayerDefinition("memory:", self.project),
            },
        )
        assert ok, self.feedback.errors
        return results

    def test_styles_all_outputs_when_more_than_100_are_pending(self):
        outputs = [self.create_output() for _ in range(101)]
        gc.collect()

        for results in outputs:
            layer = self.output(results)
            details = self.context.layerToLoadOnCompletionDetails(results["OUTPUT"])
            details.postProcessor().postProcessLayer(layer, self.context, self.feedback)
            assert layer.labelsEnabled()
            assert layer.customProperty(PlacesFunctions.PROPERTY_SOURCE_OPERATION) == (
                "SearchText"
            )

    def test_releases_post_processor_when_cancelled_outputs_are_discarded(self):
        results = self.create_output()
        processor = weakref.ref(
            self.context.layerToLoadOnCompletionDetails(
                results["OUTPUT"]
            ).postProcessor()
        )

        self.feedback.cancel()
        self.context.setLayersToLoadOnCompletion({})
        gc.collect()

        assert processor() is None

    def test_keeps_post_processor_when_results_move_to_another_context(self):
        results = self.create_output()
        processor = weakref.ref(
            self.context.layerToLoadOnCompletionDetails(
                results["OUTPUT"]
            ).postProcessor()
        )
        original_context = self.context
        self.context = QgsProcessingContext()
        self.context.setProject(self.project)
        self.context.takeResultsFrom(original_context)
        del original_context
        gc.collect()

        layer = self.output(results)
        assert processor() is not None
        self.context.layerToLoadOnCompletionDetails(
            results["OUTPUT"]
        ).postProcessor().postProcessLayer(layer, self.context, self.feedback)
        assert layer.labelsEnabled()
        assert layer.customProperty(PlacesFunctions.PROPERTY_SOURCE_OPERATION) == (
            "SearchText"
        )

        self.context = None
        gc.collect()
        assert processor() is None


class TestAlgorithmRun(ProcessingTestCase):
    """Checks the synchronous runner the dialogs use."""

    def run_directly(self, algorithm_class, parameters):
        """Runs through AlgorithmRun with the fake service."""
        run = AlgorithmRun(algorithm_class)
        with self.fake_service():
            return run, run.run(parameters)

    def test_reraises_the_original_error(self):
        self.credentials = GRAB_CREDENTIALS
        with self.assertRaisesRegex(ValueError, "Geocode is not supported"):
            self.run_directly(
                GeocodeAlgorithm, {"QUERY": "Singapore", "OUTPUT": temporary_output()}
            )
        assert self.posts == []

    def test_reraises_a_configuration_error(self):
        run = AlgorithmRun(SearchTextAlgorithm)
        with (
            patch.object(
                ConfigurationHandler,
                "get_credentials",
                side_effect=ConfigurationError("Missing region"),
            ),
            self.assertRaisesRegex(ConfigurationError, "Missing region"),
        ):
            run.run(
                {"QUERY": "cafe", "BIAS_POSITION": TOKYO, "OUTPUT": temporary_output()}
            )

    def test_keeps_the_http_status_and_the_pricing_bucket(self):
        def forbidden(handler, url, body):
            handler.last_pricing_bucket = "PlacesCore"
            raise ApiError("denied", status_code=403)

        run = AlgorithmRun(SearchTextAlgorithm)
        with (
            patch.object(
                ConfigurationHandler, "get_credentials", return_value=CREDENTIALS
            ),
            patch.object(ExternalApiHandler, "send_json_post_request", forbidden),
            self.assertRaises(ApiError) as caught,
        ):
            run.run(
                {"QUERY": "cafe", "BIAS_POSITION": TOKYO, "OUTPUT": temporary_output()}
            )
        assert caught.exception.status_code == 403
        assert run.pricing_bucket == "PlacesCore"

    def test_takes_a_styled_unregistered_layer_and_the_next_token(self):
        self.responses = [
            {
                "ResultItems": [
                    {"Title": "A", "PlaceId": "a", "Position": [139.7, 35.6]}
                ],
                "NextToken": "token-2",
            }
        ]
        run, results = self.run_directly(
            SearchNearbyAlgorithm,
            {
                "POSITION": wgs84_point((139.7, 35.6)),
                "NEXT_TOKEN": "token-1",
                "OUTPUT": temporary_output(),
            },
        )
        assert self.posts[0][1]["NextToken"] == "token-1"
        assert results["NEXT_PAGE_TOKEN"] == "token-2"
        layer = run.take_layer(results, "OUTPUT")
        assert layer.name() == "SearchNearby"
        assert layer.labelsEnabled()
        assert PlacesFunctions().is_places_layer(layer)
        assert self.project.mapLayer(layer.id()) is None

    def test_routes_outputs_carry_attributions_and_notices(self):
        response = _route_response()
        response["Routes"][0]["Legs"][0]["VehicleLegDetails"]["Notices"] = [
            {"Code": "TollsDataUnavailable", "Impact": "Low"}
        ]
        self.responses = [response]
        _run, results = self.run_directly(
            CalculateRoutesAlgorithm,
            {
                "ORIGIN": wgs84_point((139.7, 35.6)),
                "DESTINATION": wgs84_point((139.8, 35.7)),
                "OUTPUT": temporary_output(),
            },
        )
        notices = json.loads(results["NOTICES"])
        assert [notice["code"] for notice in notices] == ["TollsDataUnavailable"]
        assert json.loads(results["ATTRIBUTIONS"]) == []
        assert results["PRICING_BUCKET"] == ""

    def test_iso_time_keeps_the_offset_of_the_value(self):
        value = QDateTime.fromString("2026-03-01T09:30:00+09:00", Qt.DateFormat.ISODate)
        assert iso_time_with_offset(value) == "2026-03-01T09:30:00+09:00"


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestLayerStyle(unittest.TestCase):
    """Checks that output layers receive the built layer's presentation."""

    def test_applies_renderer_labels_aliases_and_properties(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        built = places.build_result_layer(
            {"ResultItems": [{"Title": "A", "Position": [139.7, 35.6]}]},
            "SearchText",
            intended_use="Storage",
        )
        built.setFieldAlias(0, "Name")
        built.setCustomProperty("als_routes/attributions", "Example (https://x)")
        metadata = built.metadata()
        metadata.setRights(["Example"])
        built.setMetadata(metadata)
        style = LayerStyle(built)

        target = QgsVectorLayer("Point?crs=EPSG:4326", "target", "memory")
        places.add_attributes(target)
        style.apply(target)

        assert target.renderer().type() == built.renderer().type()
        assert target.labelsEnabled()
        assert target.fields().field(0).alias() == "Name"
        assert target.customProperty(PlacesFunctions.PROPERTY_SOURCE_OPERATION) == (
            "SearchText"
        )
        assert target.metadata().rights() == ["Example"]


if __name__ == "__main__":
    unittest.main()
