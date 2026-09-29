import unittest
from unittest.mock import Mock
from urllib.parse import urlparse

from location_service.functions.routes_requests import (
    IsolineOptions,
    MatrixOptions,
    RouteOptions,
    SnapOptions,
    TracePoint,
)
from location_service.tests import HAS_QGIS

if HAS_QGIS:
    import qgis.utils

    from location_service.functions.routes import RoutesFunctions, major_road_names
    from location_service.functions.routes_results import (
        major_road_names as parse_major_road_names,
    )

    HAS_IFACE = qgis.utils.iface is not None
else:
    HAS_IFACE = False

CREDENTIALS = ("ap-northeast-1", "v1.public.test")  # pragma: allowlist secret
# ap-southeast-1 is served by GrabMaps, which offers a smaller feature set.
GRAB_CREDENTIALS = ("ap-southeast-1", "v1.public.test")  # pragma: allowlist secret

ORIGIN = (139.7, 35.6)
DESTINATION = (139.8, 35.7)
ARRIVAL_TIME = "2026-08-26T18:00:00+09:00"
ISOLINE_OPTIONS = IsolineOptions(center=ORIGIN, thresholds=(600,))
SNAP_OPTIONS = SnapOptions(
    trace_points=(
        TracePoint(position=ORIGIN),
        TracePoint(position=(139.71, 35.61)),
    )
)


def _route_options(**overrides):
    """Returns RouteOptions for the shared origin and destination pair."""
    return RouteOptions(origin=ORIGIN, destination=DESTINATION, **overrides)


def _matrix_options(**overrides):
    """Returns a one-by-one MatrixOptions with the given overrides."""
    return MatrixOptions(origins=(ORIGIN,), destinations=(DESTINATION,), **overrides)


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestMajorRoadNamesExport(unittest.TestCase):
    """The v4.4 helper remains available from the Routes facade module."""

    def test_reexports_the_response_parser(self):
        assert major_road_names is parse_major_road_names


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestRoutesRequests(unittest.TestCase):
    """Tests the route request boundary without network access."""

    def test_uses_captured_credentials(self):
        routes = RoutesFunctions.__new__(RoutesFunctions)
        routes.configuration_handler = Mock()
        routes.api_handler = Mock()
        routes.api_handler.send_json_post_request.return_value = {"Routes": []}

        result = routes.calculate_routes(
            139.7, 35.6, 139.8, 35.7, credentials=CREDENTIALS
        )

        assert result == {"Routes": []}
        routes.configuration_handler.get_credentials.assert_not_called()
        url = routes.api_handler.send_json_post_request.call_args.args[0]
        parsed = urlparse(url)
        assert parsed.hostname == "routes.geo.ap-northeast-1.amazonaws.com"
        assert parsed.path == "/v2/routes"
        assert parsed.query == "key=v1.public.test"


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestRegionPreflight(unittest.TestCase):
    """
    Every request is checked against its region right before it is sent.

    The dialog runs the same checks to enable and disable its controls, but
    the facade is the last gate: a caller that never opens the dialog must
    not be able to send a billed request the region cannot serve.
    """

    @staticmethod
    def _routes():
        """Returns a RoutesFunctions whose request never leaves the process."""
        routes = RoutesFunctions.__new__(RoutesFunctions)
        routes.configuration_handler = Mock()
        routes.api_handler = Mock()
        routes.api_handler.send_json_post_request.return_value = {}
        return routes

    def _assert_refused(self, method_name, options, credentials, message):
        """Asserts the request failed and that nothing was sent."""
        routes = self._routes()

        with self.assertRaisesRegex(ValueError, message):
            getattr(routes, method_name)(options, credentials=credentials)

        routes.api_handler.send_json_post_request.assert_not_called()

    def test_grab_regions_refuse_the_operations_they_do_not_serve(self):
        cases = (
            (
                "request_isolines",
                ISOLINE_OPTIONS,
                "CalculateIsolines is not supported",
            ),
            ("request_snap_to_roads", SNAP_OPTIONS, "SnapToRoads is not supported"),
        )
        for method_name, options, message in cases:
            with self.subTest(method=method_name):
                self._assert_refused(method_name, options, GRAB_CREDENTIALS, message)

    def test_grab_regions_refuse_the_truck_travel_mode(self):
        cases = (
            ("request_routes", _route_options(travel_mode="Truck")),
            ("request_route_matrix", _matrix_options(travel_mode="Truck")),
        )
        for method_name, options in cases:
            with self.subTest(method=method_name):
                self._assert_refused(
                    method_name,
                    options,
                    GRAB_CREDENTIALS,
                    "Travel mode 'Truck' is not supported",
                )

    def test_grab_regions_refuse_the_route_options_they_cannot_serve(self):
        cases = (
            ({"arrival_time": ARRIVAL_TIME}, "ArrivalTime is not supported"),
            ({"max_alternatives": 4}, "MaxAlternatives must be at most 3"),
            ({"avoid": ("Tunnels",)}, "Avoid options not supported"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=sorted(overrides)):
                self._assert_refused(
                    "request_routes",
                    _route_options(**overrides),
                    GRAB_CREDENTIALS,
                    message,
                )

    def test_standard_regions_refuse_a_matrix_wider_than_the_axis_limit(self):
        # Sixteen origins by one destination is only sixteen billed cells,
        # so the plugin cell cap lets it through and the AWS axis limit is
        # what refuses it.
        options = MatrixOptions(origins=(ORIGIN,) * 16, destinations=(DESTINATION,))

        self._assert_refused(
            "request_route_matrix",
            options,
            CREDENTIALS,
            "Origins must contain at most 15",
        )

    def test_a_supported_request_still_reaches_the_api(self):
        routes = self._routes()

        routes.request_routes(_route_options(), credentials=GRAB_CREDENTIALS)

        routes.api_handler.send_json_post_request.assert_called_once()


@unittest.skipUnless(HAS_IFACE, "A running QGIS iface is required")
class TestRoutesUi(unittest.TestCase):
    """Tests route UI behavior around nested credential prompts."""

    def setUp(self):
        from location_service.ui.routes.routes import RoutesUi

        self.dialog = RoutesUi()

    def tearDown(self):
        self.dialog.deleteLater()

    def test_does_not_send_after_credentials_are_cancelled(self):
        self.dialog.st_lon_lineEdit.setText("139.7")
        self.dialog.st_lat_lineEdit.setText("35.6")
        self.dialog.ed_lon_lineEdit.setText("139.8")
        self.dialog.ed_lat_lineEdit.setText("35.7")
        routes = Mock()

        def cancel_during_credentials():
            self.dialog._cancelled = True
            return CREDENTIALS

        routes.configuration_handler.get_credentials.side_effect = (
            cancel_during_credentials
        )
        self.dialog.routes = routes

        self.dialog._run()

        routes.configuration_handler.get_credentials.assert_called_once()
        routes.request_routes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
