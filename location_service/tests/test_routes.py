import unittest
from unittest.mock import Mock

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    import qgis.utils

    from location_service.functions.routes import RoutesFunctions, major_road_names

    HAS_IFACE = qgis.utils.iface is not None
else:
    HAS_IFACE = False


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestMajorRoadNames(unittest.TestCase):
    """Road labels may contain both fields; RoadName takes precedence."""

    def test_prefers_road_name_and_falls_back_to_route_number(self):
        route = {
            "MajorRoadLabels": [
                {"RouteNumber": {"Value": "I-95"}},
                {"RoadName": {"Value": "Main St"}},
            ]
        }
        assert major_road_names(route) == "I-95, Main St"

    def test_prefers_road_name_when_a_label_has_both(self):
        route = {
            "MajorRoadLabels": [
                {"RoadName": {"Value": "Main St"}, "RouteNumber": {"Value": "I-95"}}
            ]
        }
        assert major_road_names(route) == "Main St"

    def test_route_number_only(self):
        route = {"MajorRoadLabels": [{"RouteNumber": {"Value": "E15"}}]}
        assert major_road_names(route) == "E15"

    def test_skips_empty_labels(self):
        assert major_road_names({"MajorRoadLabels": [{}, {}]}) == ""

    def test_handles_null_values(self):
        assert major_road_names({}) == ""
        assert major_road_names({"MajorRoadLabels": None}) == ""
        assert major_road_names({"MajorRoadLabels": [{"RoadName": None}]}) == ""
        assert major_road_names({"MajorRoadLabels": [None]}) == ""


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestRoutesRequests(unittest.TestCase):
    """Tests the route request boundary without network access."""

    def test_uses_captured_credentials(self):
        routes = RoutesFunctions.__new__(RoutesFunctions)
        routes.configuration_handler = Mock()
        routes.api_handler = Mock()
        routes.api_handler.send_json_post_request.return_value = {"Routes": []}

        result = routes.calculate_routes(
            139.7,
            35.6,
            139.8,
            35.7,
            credentials=("ap-northeast-1", "v1.public.test"),
        )

        assert result == {"Routes": []}
        routes.configuration_handler.get_credentials.assert_not_called()
        url = routes.api_handler.send_json_post_request.call_args.args[0]
        assert url.startswith(
            "https://routes.geo.ap-northeast-1.amazonaws.com/v2/routes?key="
        )
        assert url.endswith("v1.public.test")


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
            return "ap-northeast-1", "v1.public.test"

        routes.configuration_handler.get_credentials.side_effect = (
            cancel_during_credentials
        )
        self.dialog.routes = routes

        self.dialog._search()

        routes.calculate_routes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
