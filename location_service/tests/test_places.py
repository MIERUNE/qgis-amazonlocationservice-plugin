import unittest

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    import qgis.utils
    from qgis.core import QgsProject, QgsVectorLayer
    from qgis.PyQt.QtCore import QVariant

    from location_service.functions.places import (
        PlacesFunctions,
        category_names,
        first_contact,
        opening_hours,
        result_position,
        time_zone_name,
    )

    HAS_IFACE = qgis.utils.iface is not None
else:
    HAS_IFACE = False


def _places_with_layer(data=None):
    """Builds a PlacesFunctions (without handlers) and a populated layer."""
    places = PlacesFunctions.__new__(PlacesFunctions)
    layer = QgsVectorLayer("Point?crs=EPSG:4326", "test", "memory")
    places.add_attributes(layer)
    if data is not None:
        places.add_features(layer, data, "SearchText")
    places.record_layer_source(layer, "SearchText", "Storage")
    return places, layer


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestPlacesFeatures(unittest.TestCase):
    """Validates conversion of place results into QGIS features."""

    def test_skips_item_without_position_and_keeps_valid_item(self):
        _, layer = _places_with_layer(
            {
                "ResultItems": [
                    {
                        "Title": "Area without a representative point",
                        "Address": {"Label": "Area"},
                    },
                    {
                        "Title": "Tokyo Station",
                        "Position": [139.7671, 35.6812],
                        "Address": {
                            "Region": {"Name": "Tokyo"},
                            "Locality": "Chiyoda",
                            "Label": "Tokyo Station",
                        },
                    },
                ]
            }
        )

        features = list(layer.getFeatures())
        assert len(features) == 1
        assert features[0][PlacesFunctions.FIELD_TITLE] == "Tokyo Station"
        point = features[0].geometry().asPoint()
        assert point.x() == 139.7671
        assert point.y() == 35.6812

    def test_layer_schema(self):
        _, layer = _places_with_layer()
        names = [field.name() for field in layer.fields()]
        assert names == [
            "Title",
            "PlaceId",
            "PlaceType",
            "Region",
            "Locality",
            "Label",
            "CountryCode",
            "PostalCode",
            "SourceOperation",
            "Distance",
            "Categories",
            "Phone",
            "Website",
            "OpeningHours",
            "TimeZone",
        ]
        distance = layer.fields().field(PlacesFunctions.FIELD_DISTANCE)
        assert distance.type() == QVariant.Double

    def test_fills_all_attributes(self):
        _, layer = _places_with_layer(
            {
                "ResultItems": [
                    {
                        "Title": "Tokyo Station",
                        "PlaceId": "place-1",
                        "PlaceType": "PointOfInterest",
                        "Position": [139.7671, 35.6812],
                        "Distance": 1234.5,
                        "Categories": [{"Name": "Restaurant"}, {"Name": "Cafe"}],
                        "Address": {
                            "Region": {"Name": "Tokyo"},
                            "Locality": "Chiyoda",
                            "Label": "Tokyo Station",
                            "Country": {"Code2": "JP", "Code3": "JPN"},
                            "PostalCode": "100-0005",
                        },
                    }
                ]
            }
        )

        feature = next(layer.getFeatures())
        assert feature["PlaceId"] == "place-1"
        assert feature["PlaceType"] == "PointOfInterest"
        assert feature["CountryCode"] == "JPN"
        assert feature["PostalCode"] == "100-0005"
        assert feature["SourceOperation"] == "SearchText"
        assert feature["Distance"] == 1234.5
        assert feature["Categories"] == "Restaurant; Cafe"

    def test_normalizes_country_code(self):
        _, layer = _places_with_layer(
            {
                "ResultItems": [
                    {
                        "Title": "Tokyo Station",
                        "Position": [139.7671, 35.6812],
                        "Address": {"Country": {"Code3": " jpn "}},
                    }
                ]
            }
        )

        feature = next(layer.getFeatures())
        assert feature["CountryCode"] == "JPN"

    def test_uses_code2_when_code3_is_invalid(self):
        _, layer = _places_with_layer(
            {
                "ResultItems": [
                    {
                        "Title": "Seattle",
                        "Position": [-122.3321, 47.6062],
                        "Address": {"Country": {"Code2": " us ", "Code3": "US"}},
                    }
                ]
            }
        )

        feature = next(layer.getFeatures())
        assert feature["CountryCode"] == "US"

    def test_fills_detail_attributes_from_search_response(self):
        _, layer = _places_with_layer(
            {
                "ResultItems": [
                    {
                        "Title": "Tokyo Station",
                        "Position": [139.7671, 35.6812],
                        "Contacts": {
                            "Phones": [{"Value": "03-1234-5678"}],
                            "Websites": [{"Value": "https://example.com"}],
                        },
                        "OpeningHours": [{"Display": ["Mo-Fr: 09:00-18:00"]}],
                        "TimeZone": {"Name": "Asia/Tokyo"},
                    }
                ]
            },
        )

        feature = next(layer.getFeatures())
        assert feature["Phone"] == "03-1234-5678"
        assert feature["Website"] == "https://example.com"
        assert feature["OpeningHours"] == "Mo-Fr: 09:00-18:00"
        assert feature["TimeZone"] == "Asia/Tokyo"

    def test_adds_features_to_an_editable_layer(self):
        places, layer = _places_with_layer()
        assert layer.startEditing()
        try:
            count = places.add_features(
                layer,
                {
                    "ResultItems": [
                        {
                            "Title": "Editable result",
                            "Position": [-73.9, 40.7],
                            "Address": {"Country": {"Code3": "USA"}},
                        }
                    ]
                },
                "SearchText",
            )
            assert count == 1
            assert len(list(layer.getFeatures())) == 1
        finally:
            layer.rollBack()

    def test_survives_null_values(self):
        # Amazon Location can return explicit nulls; none of them may crash.
        _, layer = _places_with_layer(
            {
                "ResultItems": [
                    {
                        "Title": None,
                        "Position": [139.7, 35.6],
                        "Address": None,
                        "Categories": None,
                    },
                    {
                        "Position": [139.8, 35.7],
                        "Address": {"Region": None, "Country": None},
                        "Categories": [None, {"Name": None}],
                    },
                ]
            }
        )
        assert len(list(layer.getFeatures())) == 2

    def test_survives_null_result_items(self):
        _, layer = _places_with_layer({"ResultItems": None})
        assert list(layer.getFeatures()) == []

    def test_skips_unusable_positions(self):
        _, layer = _places_with_layer(
            {
                "ResultItems": [
                    {"Title": "short", "Position": [139.7]},
                    {"Title": "nulls", "Position": [None, None]},
                    {"Title": "strings", "Position": ["a", "b"]},
                    {"Title": "null", "Position": None},
                ]
            }
        )
        assert list(layer.getFeatures()) == []

    def test_rejects_single_use_layer_creation(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        with self.assertRaisesRegex(ValueError, "Storage request"):
            places.build_result_layer({"ResultItems": []}, "SearchText")

    def test_built_layer_is_styled_and_not_registered(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        layer = places.build_result_layer(
            {"ResultItems": [{"Title": "A", "Position": [139.7, 35.6]}]},
            "SearchText",
            intended_use="Storage",
        )
        assert layer.name() == "SearchText"
        assert layer.featureCount() == 1
        assert layer.labelsEnabled()
        assert layer.customProperty(PlacesFunctions.PROPERTY_INTENDED_USE) == "Storage"
        assert QgsProject.instance().mapLayer(layer.id()) is None

    def test_allows_japan_layer_creation(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        result = {
            "ResultItems": [
                {
                    "Position": [139.7, 35.6],
                    "Address": {"Country": {"Code3": "JPN"}},
                }
            ]
        }
        layer = places.build_result_layer(result, "SearchText", intended_use="Storage")
        features = list(layer.getFeatures())
        assert len(features) == 1
        assert features[0][PlacesFunctions.FIELD_COUNTRY_CODE] == "JPN"

    def test_allows_unknown_country_layer_creation(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        result = {"ResultItems": [{"Position": [139.7, 35.6]}]}
        layer = places.build_result_layer(result, "SearchText", intended_use="Storage")
        assert len(list(layer.getFeatures())) == 1

    def test_ignores_non_drawable_items_during_layer_creation(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        result = {
            "ResultItems": [
                {"Title": "No point", "Address": None},
                {
                    "Title": "Stored point",
                    "Position": [-73.9, 40.7],
                    "Address": {"Country": {"Code3": "USA"}},
                },
            ]
        }

        layer = places.build_result_layer(result, "SearchText", intended_use="Storage")
        assert len(list(layer.getFeatures())) == 1


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestResultHelpers(unittest.TestCase):
    """Tests for the null-safe response parsing helpers."""

    def test_result_position(self):
        assert result_position({"Position": [139.7, 35.6]}) == [139.7, 35.6]
        assert result_position({"Position": [139.7]}) is None
        assert result_position({"Position": None}) is None
        assert result_position({"Position": ["a", "b"]}) is None
        assert result_position({}) is None

    def test_result_position_rejects_unusable_numbers(self):
        assert result_position({"Position": [float("nan"), 35.6]}) is None
        assert result_position({"Position": [139.7, float("inf")]}) is None
        assert result_position({"Position": [True, False]}) is None
        assert result_position({"Position": [999.0, 35.6]}) is None
        assert result_position({"Position": [139.7, 99.0]}) is None
        assert result_position({"Position": 5}) is None
        assert result_position({"Position": {"lon": 139.7}}) is None

    def test_category_names(self):
        result = {"Categories": [{"Name": "Restaurant"}, None, {"Name": None}]}
        assert category_names(result) == "Restaurant"
        assert category_names({"Categories": None}) == ""

    def test_first_contact(self):
        detail = {"Contacts": {"Phones": [None, {"Value": "03-1234"}]}}
        assert first_contact(detail, "Phones") == "03-1234"
        assert first_contact({"Contacts": None}, "Phones") == ""
        assert first_contact({"Contacts": {"Phones": None}}, "Phones") == ""

    def test_opening_hours(self):
        detail = {"OpeningHours": [{"Display": ["Mo: 09-18"]}, None]}
        assert opening_hours(detail) == "Mo: 09-18"
        assert opening_hours({"OpeningHours": None}) == ""

    def test_time_zone_name(self):
        assert time_zone_name({"TimeZone": {"Name": "Asia/Tokyo"}}) == "Asia/Tokyo"
        assert time_zone_name({"TimeZone": None}) == ""
