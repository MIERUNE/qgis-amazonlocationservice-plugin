import unittest

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from location_service.functions.routes import major_road_names


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


if __name__ == "__main__":
    unittest.main()
