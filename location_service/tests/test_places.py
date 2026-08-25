import unittest
from typing import ClassVar
from unittest.mock import Mock, patch

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    import qgis.utils
    from qgis.core import QgsField, QgsProject, QgsVectorLayer
    from qgis.PyQt.QtCore import QVariant

    from location_service.functions.places import (
        PlacesFunctions,
        PlacesOperationCancelledError,
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
        layer = QgsVectorLayer("Point?crs=EPSG:4326", "test", "memory")
        with self.assertRaisesRegex(ValueError, "Storage request"):
            places.setup_layer(layer, {"ResultItems": []}, "SearchText")
        assert layer.fields().isEmpty()

    def test_allows_japan_layer_creation(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        layer = QgsVectorLayer("Point?crs=EPSG:4326", "test", "memory")
        result = {
            "ResultItems": [
                {
                    "Position": [139.7, 35.6],
                    "Address": {"Country": {"Code3": "JPN"}},
                }
            ]
        }
        places.setup_layer(layer, result, "SearchText", intended_use="Storage")
        try:
            features = list(layer.getFeatures())
            assert len(features) == 1
            assert features[0][PlacesFunctions.FIELD_COUNTRY_CODE] == "JPN"
        finally:
            QgsProject.instance().removeMapLayer(layer.id())

    def test_allows_unknown_country_layer_creation(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        layer = QgsVectorLayer("Point?crs=EPSG:4326", "test", "memory")
        result = {"ResultItems": [{"Position": [139.7, 35.6]}]}
        places.setup_layer(layer, result, "SearchText", intended_use="Storage")
        try:
            assert len(list(layer.getFeatures())) == 1
        finally:
            QgsProject.instance().removeMapLayer(layer.id())

    def test_ignores_non_drawable_items_during_layer_creation(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        layer = QgsVectorLayer("Point?crs=EPSG:4326", "test", "memory")
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

        places.setup_layer(layer, result, "SearchText", intended_use="Storage")
        try:
            assert len(list(layer.getFeatures())) == 1
        finally:
            QgsProject.instance().removeMapLayer(layer.id())


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


class _FakeCredentials:
    """Returns fixed credentials without touching QSettings or auth storage."""

    @staticmethod
    def get_credentials():
        return "ap-northeast-1", "v1.public.test"  # pragma: allowlist secret


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestEnrichSelectedFeatures(unittest.TestCase):
    """Tests for the GetPlace enrichment of selected features."""

    DETAILS: ClassVar[dict] = {
        "place-1": {
            "Address": {"Country": {"Code3": "USA"}},
            "Contacts": {
                "Phones": [{"Value": "03-1111"}],
                "Websites": [{"Value": "https://one.example.com"}],
            },
            "OpeningHours": [{"Display": ["Mo-Fr: 09:00-18:00"]}],
            "TimeZone": {"Name": "Asia/Tokyo"},
        },
        "place-2": {
            "Address": {"Country": {"Code3": "USA"}},
            "Contacts": {"Phones": [{"Value": "03-2222"}]},
        },
    }

    def _selected_layer(self, count=2):
        result_items = [
            {
                "Title": f"Place {index}",
                "PlaceId": f"place-{index}",
                "Position": [139.7 + index * 0.01, 35.6],
                "Address": {"Country": {"Code3": "USA"}},
            }
            for index in range(1, count + 1)
        ]
        places, layer = _places_with_layer({"ResultItems": result_items})
        places.configuration_handler = _FakeCredentials()
        places.get_place = self._fake_get_place
        layer.selectAll()
        return places, layer

    def _fake_get_place(self, place_id, *args, **kwargs):
        self.fetched.append(place_id)
        return self.DETAILS.get(place_id, {})

    @staticmethod
    def _remove_detail_fields(layer):
        """Makes a current result layer look like a layer from an older version."""
        indexes = [
            layer.fields().indexOf(name) for name in PlacesFunctions.DETAIL_FIELDS
        ]
        assert layer.dataProvider().deleteAttributes(indexes)
        layer.updateFields()

    def setUp(self):
        self.fetched = []

    def test_enriches_selected_features(self):
        places, layer = self._selected_layer()
        count = places.enrich_selected_features(layer, intended_use="Storage")
        assert count == 2
        phones = {f["PlaceId"]: f["Phone"] for f in layer.getFeatures()}
        assert phones == {"place-1": "03-1111", "place-2": "03-2222"}
        websites = {f["PlaceId"]: f["Website"] for f in layer.getFeatures()}
        assert websites["place-1"] == "https://one.example.com"
        assert websites["place-2"] == ""

    def test_rejects_layer_without_place_id(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        layer = QgsVectorLayer("Point?crs=EPSG:4326", "other", "memory")
        with self.assertRaises(ValueError):
            places.enrich_selected_features(layer, intended_use="Storage")

    def test_rejects_unmarked_layer_with_place_id(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        layer = QgsVectorLayer("Point?crs=EPSG:4326", "other", "memory")
        places.add_attributes(layer)
        with self.assertRaises(ValueError):
            places.enrich_selected_features(layer, intended_use="Storage")

    def test_rejects_unknown_layer_schema(self):
        places, layer = self._selected_layer()
        layer.setCustomProperty(PlacesFunctions.PROPERTY_SCHEMA_VERSION, 999)
        with self.assertRaises(ValueError):
            places.enrich_selected_features(layer, intended_use="Storage")
        assert self.fetched == []

    def test_rejects_layer_missing_a_required_result_field(self):
        for field_name in (
            PlacesFunctions.FIELD_TITLE,
            PlacesFunctions.FIELD_COUNTRY_CODE,
        ):
            with self.subTest(field_name=field_name):
                places, layer = self._selected_layer()
                index = layer.fields().indexOf(field_name)
                assert layer.dataProvider().deleteAttributes([index])
                layer.updateFields()
                assert not places.is_places_layer(layer)

    def test_rejects_non_vector_layer(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        with self.assertRaises(ValueError):
            places.enrich_selected_features(None, intended_use="Storage")

    def test_deduplicates_place_ids(self):
        result_items = [
            {
                "Title": f"Branch {index}",
                "PlaceId": "place-1",
                "Position": [139.7, 35.6],
                "Address": {"Country": {"Code3": "USA"}},
            }
            for index in range(2)
        ]
        places, layer = _places_with_layer({"ResultItems": result_items})
        places.configuration_handler = _FakeCredentials()
        places.get_place = self._fake_get_place
        layer.selectAll()

        count = places.enrich_selected_features(layer, intended_use="Storage")

        # Both features share one request in this direct Storage pass.
        assert count == 2
        assert self.fetched == ["place-1"]
        phones = [str(f["Phone"]) for f in layer.getFeatures()]
        assert phones == ["03-1111", "03-1111"]

    def test_enriches_an_existing_edit_session(self):
        places, layer = self._selected_layer()
        assert layer.startEditing()
        try:
            assert places.enrich_selected_features(layer, intended_use="Storage") == 2
            assert layer.isEditable()
            assert layer.fields().indexOf(PlacesFunctions.FIELD_PHONE) >= 0
        finally:
            layer.rollBack()
        assert set(self.fetched) == {"place-1", "place-2"}

    def test_rejects_empty_selection(self):
        places, layer = self._selected_layer()
        layer.removeSelection()
        with self.assertRaises(ValueError):
            places.enrich_selected_features(layer, intended_use="Storage")

    def test_rejects_oversized_selection(self):
        places, layer = self._selected_layer(PlacesFunctions.MAX_ENRICH_FEATURES + 1)
        with self.assertRaisesRegex(ValueError, "billable Storage request"):
            places.enrich_selected_features(layer, intended_use="Storage")
        assert self.fetched == []

    def test_allows_many_features_with_one_unique_place_id(self):
        result_items = [
            {
                "Title": f"Branch {index}",
                "PlaceId": "place-1",
                "Position": [139.7, 35.6],
                "Address": {"Country": {"Code3": "USA"}},
            }
            for index in range(PlacesFunctions.MAX_ENRICH_FEATURES + 1)
        ]
        places, layer = _places_with_layer({"ResultItems": result_items})
        places.configuration_handler = _FakeCredentials()
        places.get_place = self._fake_get_place
        layer.selectAll()

        assert places.enrich_selected_features(layer, intended_use="Storage") == len(
            result_items
        )
        assert self.fetched == ["place-1"]

    def test_cancel_does_not_change_layer(self):
        places, layer = self._selected_layer()
        with self.assertRaises(PlacesOperationCancelledError):
            places.enrich_selected_features(
                layer,
                intended_use="Storage",
                should_cancel=lambda: len(self.fetched) >= 1,
            )
        assert len(self.fetched) == 1
        assert layer.fields().indexOf(PlacesFunctions.FIELD_PHONE) >= 0
        assert not layer.isEditable()

    def test_place_id_change_stops_before_the_next_request(self):
        places, layer = self._selected_layer()
        targets = places.enrichment_targets(layer)
        changed_feature_id = targets[-1][0]

        def change_place_id(place_id, *args, **kwargs):
            self.fetched.append(place_id)
            if len(self.fetched) == 1:
                assert layer.startEditing()
                field = layer.fields().indexOf(PlacesFunctions.FIELD_PLACE_ID)
                assert layer.changeAttributeValue(
                    changed_feature_id, field, "changed-place"
                )
            return self.DETAILS[place_id]

        places.get_place = change_place_id
        try:
            with self.assertRaisesRegex(
                PlacesOperationCancelledError, "PlaceId changed"
            ):
                places.fetch_selected_feature_details(
                    layer,
                    intended_use="Storage",
                    targets=targets,
                    credentials=("us-east-1", "v1.public.test"),
                )
        finally:
            layer.rollBack()
        assert len(self.fetched) == 1

    def test_wrong_detail_field_type_is_rejected_before_requesting(self):
        places, layer = self._selected_layer()
        self._remove_detail_fields(layer)
        assert layer.dataProvider().addAttributes(
            [QgsField(PlacesFunctions.FIELD_PHONE, QVariant.Int)]
        )
        layer.updateFields()

        with self.assertRaisesRegex(ValueError, "Phone field must be a text field"):
            places.fetch_selected_feature_details(
                layer,
                intended_use="Storage",
                credentials=("us-east-1", "v1.public.test"),
            )
        assert self.fetched == []

    def test_detail_field_type_change_stops_before_the_next_request(self):
        places, layer = self._selected_layer()
        self._remove_detail_fields(layer)
        targets = places.enrichment_targets(layer)

        def add_wrong_detail_field(place_id, *args, **kwargs):
            self.fetched.append(place_id)
            if len(self.fetched) == 1:
                assert layer.startEditing()
                assert layer.addAttribute(
                    QgsField(PlacesFunctions.FIELD_PHONE, QVariant.Int)
                )
                layer.updateFields()
            return self.DETAILS[place_id]

        places.get_place = add_wrong_detail_field
        try:
            with self.assertRaisesRegex(ValueError, "Phone field must be a text field"):
                places.fetch_selected_feature_details(
                    layer,
                    intended_use="Storage",
                    targets=targets,
                    credentials=("us-east-1", "v1.public.test"),
                )
        finally:
            layer.rollBack()
        assert len(self.fetched) == 1

    def test_add_detail_fields_rejects_an_existing_wrong_type(self):
        places, layer = self._selected_layer()
        self._remove_detail_fields(layer)
        assert layer.startEditing()
        assert layer.addAttribute(QgsField(PlacesFunctions.FIELD_PHONE, QVariant.Int))
        layer.updateFields()
        try:
            with self.assertRaisesRegex(ValueError, "Phone field must be a text field"):
                places._add_detail_fields(layer)
        finally:
            layer.rollBack()

    def test_rejects_single_use_details_before_fetching(self):
        places, layer = self._selected_layer()
        with self.assertRaisesRegex(ValueError, "Storage request"):
            places.enrich_selected_features(layer)
        assert self.fetched == []

    def test_details_can_be_undone_as_one_edit_command(self):
        places, layer = self._selected_layer()
        self._remove_detail_fields(layer)
        assert places.enrich_selected_features(layer, intended_use="Storage") == 2
        assert layer.undoStack().canUndo()
        assert layer.fields().indexOf(PlacesFunctions.FIELD_PHONE) >= 0

        layer.undoStack().undo()

        assert layer.fields().indexOf(PlacesFunctions.FIELD_PHONE) < 0
        layer.rollBack()

    def test_allows_japan_details(self):
        result = {
            "ResultItems": [
                {
                    "Title": "Tokyo",
                    "PlaceId": "place-1",
                    "Position": [139.7, 35.6],
                    "Address": {"Country": {"Code3": "JPN"}},
                }
            ]
        }
        places, layer = _places_with_layer(result)
        places.configuration_handler = _FakeCredentials()
        places.get_place = self._fake_get_place
        layer.selectAll()
        assert places.enrich_selected_features(layer, intended_use="Storage") == 1
        assert self.fetched == ["place-1"]

    def test_allows_unknown_country_details(self):
        result = {
            "ResultItems": [
                {
                    "Title": "Unknown country",
                    "PlaceId": "place-1",
                    "Position": [139.7, 35.6],
                }
            ]
        }
        places, layer = _places_with_layer(result)
        places.configuration_handler = _FakeCredentials()
        places.get_place = self._fake_get_place
        layer.selectAll()
        assert places.enrich_selected_features(layer, intended_use="Storage") == 1
        assert self.fetched == ["place-1"]

    def test_allows_japan_get_place_response(self):
        places, layer = self._selected_layer()

        def japan_get_place(place_id, *args, **kwargs):
            self.fetched.append(place_id)
            return {
                "Address": {"Country": {"Code3": "JPN"}},
                "Contacts": {"Phones": [{"Value": "03-1234"}]},
            }

        places.get_place = japan_get_place
        assert places.enrich_selected_features(layer, intended_use="Storage") == 2
        assert len(self.fetched) == 2
        assert {feature["Phone"] for feature in layer.getFeatures()} == {"03-1234"}

    def test_allows_unknown_country_get_place_response(self):
        places, layer = self._selected_layer()

        def unknown_country_get_place(place_id, *args, **kwargs):
            self.fetched.append(place_id)
            return {
                "Address": None,
                "Contacts": {"Phones": [{"Value": "03-5678"}]},
            }

        places.get_place = unknown_country_get_place
        assert places.enrich_selected_features(layer, intended_use="Storage") == 2
        assert len(self.fetched) == 2
        assert {feature["Phone"] for feature in layer.getFeatures()} == {"03-5678"}

    def test_get_details_uses_one_credentials_snapshot(self):
        places, layer = self._selected_layer()

        class ChangingCredentials:
            def __init__(self):
                self.calls = 0

            def get_credentials(self):
                self.calls += 1
                if self.calls == 1:
                    return "us-east-1", "v1.public.snapshot"
                return "eu-west-1", "v1.public.changed"

        class CapturingApi:
            def __init__(self):
                self.urls = []

            def send_json_get_request(self, url):
                self.urls.append(url)
                return {"Address": {"Country": {"Code3": "USA"}}}

        credentials = ChangingCredentials()
        api = CapturingApi()
        places.configuration_handler = credentials
        places.api_handler = api
        places.get_place = PlacesFunctions.get_place.__get__(places, PlacesFunctions)

        values = places.fetch_selected_feature_details(layer, intended_use="Storage")

        assert len(values) == 2
        assert credentials.calls == 1
        assert len(api.urls) == 2
        assert all("places.geo.us-east-1.amazonaws.com" in url for url in api.urls)
        assert all("key=v1.public.snapshot" in url for url in api.urls)

    def test_get_details_accepts_a_credentials_snapshot_from_the_caller(self):
        places, layer = self._selected_layer()
        places.configuration_handler = Mock()
        places.api_handler = Mock()
        places.api_handler.send_json_get_request.return_value = {
            "Address": {"Country": {"Code3": "USA"}}
        }
        places.get_place = PlacesFunctions.get_place.__get__(places, PlacesFunctions)

        places.fetch_selected_feature_details(
            layer,
            intended_use="Storage",
            credentials=("us-west-2", "v1.public.captured"),
        )

        places.configuration_handler.get_credentials.assert_not_called()
        urls = [
            call.args[0]
            for call in places.api_handler.send_json_get_request.call_args_list
        ]
        assert len(urls) == 2
        assert all("places.geo.us-west-2.amazonaws.com" in url for url in urls)

    def test_rejects_missing_feature_before_apply(self):
        places, layer = self._selected_layer()
        values = {
            999: {
                PlacesFunctions.FIELD_PHONE: "03-1111",
                PlacesFunctions.FIELD_WEBSITE: "",
                PlacesFunctions.FIELD_OPENING_HOURS: "",
                PlacesFunctions.FIELD_TIMEZONE: "Asia/Tokyo",
            }
        }
        with self.assertRaises(PlacesOperationCancelledError):
            places.apply_feature_details(layer, values)
        assert layer.fields().indexOf(PlacesFunctions.FIELD_PHONE) >= 0
        assert not layer.isEditable()


@unittest.skipUnless(HAS_IFACE, "A running QGIS iface is required")
class TestPlacesDialog(unittest.TestCase):
    """Tests the Places dialog behavior that needs a live QGIS."""

    def setUp(self):
        from location_service.ui.places import places as places_module
        from location_service.ui.places.places import PlacesUi

        self.places_module = places_module
        self.dialog = PlacesUi()
        self.dialog.language_comboBox.setEditText("Default")
        self.dialog.political_view_comboBox.setCurrentIndex(0)

    def tearDown(self):
        self.dialog.deleteLater()

    def test_function_switch_updates_page_and_defaults(self):
        self.dialog.places_comboBox.setCurrentText("ReverseGeocode")
        assert self.dialog.params_stackedWidget.currentIndex() == 2
        assert self.dialog.position_label.text() == "QueryPosition (required)"
        assert self.dialog.maxresults_spinBox.value() == 1
        assert self.dialog.button_search.text() == "Reverse geocode"

    def test_details_are_automatic_instead_of_a_checkbox(self):
        assert not hasattr(self.dialog, "st_details_checkBox")
        assert not hasattr(self.dialog, "nb_details_checkBox")
        assert self.dialog.button_enrich.text() == "Update Selected Details"

    def test_search_text_requires_position(self):
        self.dialog.places_comboBox.setCurrentText("SearchText")
        assert self.dialog.position_label.text() == "BiasPosition (required)"

    def test_function_switch_preserves_each_max_results_value(self):
        self.dialog.places_comboBox.setCurrentText("SearchText")
        self.dialog.maxresults_spinBox.setValue(37)
        self.dialog.places_comboBox.setCurrentText("Geocode")
        self.dialog.maxresults_spinBox.setValue(23)

        self.dialog.places_comboBox.setCurrentText("SearchText")
        assert self.dialog.maxresults_spinBox.value() == 37
        self.dialog.places_comboBox.setCurrentText("Geocode")
        assert self.dialog.maxresults_spinBox.value() == 23

    def test_language_default_is_none(self):
        assert self.dialog._language() is None

    def test_language_accepts_edited_code(self):
        self.dialog.language_comboBox.setEditText("en-US")
        assert self.dialog._language() == "en-US"

    def test_language_accepts_extension_subtags(self):
        self.dialog.language_comboBox.setEditText("en-US-u-ca-gregory")
        assert self.dialog._language() == "en-US-u-ca-gregory"

    def test_language_rejects_invalid_code(self):
        self.dialog.language_comboBox.setEditText("not a code")
        with self.assertRaises(ValueError):
            self.dialog._language()

    def test_language_rejects_overlong_code(self):
        self.dialog.language_comboBox.setEditText("en" + "-abcdefgh" * 5)
        with self.assertRaises(ValueError):
            self.dialog._language()

    def test_clear_position(self):
        self.dialog.lon_lineEdit.setText("139.7")
        self.dialog.lat_lineEdit.setText("35.6")
        self.dialog._clear_position()
        assert self.dialog.lon_lineEdit.text() == ""
        assert self.dialog.lat_lineEdit.text() == ""
        assert self.dialog._parse_position() is None

    def test_partial_or_invalid_position_is_rejected(self):
        cases = (("139.7", ""), ("", "35.6"), ("abc", "35.6"), ("181", "0"))
        for longitude, latitude in cases:
            with self.subTest(longitude=longitude, latitude=latitude):
                self.dialog.lon_lineEdit.setText(longitude)
                self.dialog.lat_lineEdit.setText(latitude)
                with self.assertRaisesRegex(ValueError, "valid longitude"):
                    self.dialog._parse_position()

    def test_political_view_default_is_none(self):
        assert self.dialog._political_view() is None

    def test_global_region_restores_shared_political_view(self):
        self.dialog.localization_preferences = Mock()
        self.dialog.localization_preferences.load.return_value = ("ja", "IND")
        self.dialog._configured_region = Mock(return_value="ap-southeast-1")
        self.dialog.refresh_region_capabilities()
        assert not self.dialog.political_view_comboBox.isEnabled()

        self.dialog._configured_region.return_value = "ap-northeast-1"
        self.dialog.refresh_region_capabilities()

        assert self.dialog.political_view_comboBox.isEnabled()
        assert self.dialog.political_view_comboBox.currentData() == "IND"
        assert self.dialog.language_comboBox.currentText() == "ja"

    def test_global_region_keeps_shared_language(self):
        self.dialog.localization_preferences = Mock()
        self.dialog.localization_preferences.load.return_value = ("ja", "IND")
        self.dialog._restore_localization_preferences()
        self.dialog._configured_region = Mock(return_value="ap-northeast-1")

        self.dialog.refresh_region_capabilities()

        assert self.dialog.language_comboBox.currentText() == "ja"
        assert self.dialog.political_view_comboBox.currentData() == "IND"

    def test_limited_region_resets_unsupported_shared_language(self):
        self.dialog.localization_preferences = Mock()
        self.dialog.localization_preferences.load.return_value = ("ja", "")
        self.dialog._restore_localization_preferences()
        self.dialog._configured_region = Mock(return_value="ap-southeast-1")

        self.dialog.refresh_region_capabilities()

        assert self.dialog.language_comboBox.currentText() == "Default"
        ja_index = self.dialog.language_comboBox.findText("ja")
        assert not self.dialog.language_comboBox.model().item(ja_index).isEnabled()

    def test_settings_refresh_clears_pagination(self):
        self.dialog._pagination_request = {"function": "SearchText"}
        self.dialog._next_token = "page-2"
        self.dialog.button_load_more.setEnabled(True)

        self.dialog.refresh_region_capabilities()

        assert self.dialog._pagination_request is None
        assert self.dialog._next_token is None
        assert not self.dialog.button_load_more.isEnabled()

    def test_reverse_geocode_radius_zero_is_unset(self):
        self.dialog.places.configuration_handler = Mock()
        self.dialog.places.configuration_handler.get_credentials.return_value = (
            "ap-northeast-1",
            "v1.public.test",
        )
        self.dialog.rg_radius_spinBox.setValue(0)

        request = self.dialog._build_request("ReverseGeocode", [139.7, 35.6], 1)

        assert request["query_radius"] is None
        assert request["additional_features"] == ["TimeZone"]

    def test_geocode_sends_time_zone_with_the_storage_request(self):
        credentials = ("ap-northeast-1", "v1.public.test")
        self.dialog.places.configuration_handler = Mock()
        self.dialog.places.configuration_handler.get_credentials.return_value = (
            credentials
        )
        self.dialog.places.geocode = Mock(return_value={})
        self.dialog.gc_text_lineEdit.setText("Tokyo Station")

        request = self.dialog._build_request("Geocode", None, 10)
        self.dialog._send_request(request, "Storage")

        assert request["additional_features"] == ["TimeZone"]
        assert self.dialog.places.geocode.call_args.kwargs["additional_features"] == [
            "TimeZone"
        ]

    def test_parse_countries(self):
        assert self.dialog._parse_countries("jp, USA") == ["JP", "USA"]
        assert self.dialog._parse_countries("") is None
        with self.assertRaises(ValueError):
            self.dialog._parse_countries("Japan")

    def test_request_rejects_changed_credentials(self):
        self.dialog.places.configuration_handler = Mock()
        self.dialog.places.configuration_handler.get_credentials.return_value = (
            "us-west-2",
            "v1.public.new",
        )
        request = {
            "function": "SearchText",
            "position": [-73.9, 40.7],
            "credentials": ("us-east-1", "v1.public.old"),
        }

        with self.assertRaisesRegex(ValueError, "region or API key changed"):
            self.dialog._send_request(request, "Storage")

    @staticmethod
    def _result(place_id="place-1", title="Place", token=None):
        result = {
            "ResultItems": [
                {
                    "Title": title,
                    "PlaceId": place_id,
                    "Position": [139.7, 35.6],
                    "Address": {"Country": {"Code3": "USA"}},
                }
            ]
        }
        if token:
            result["NextToken"] = token
        return result

    def test_search_sends_one_storage_request_and_adds_the_layer(self):
        request = {
            "function": "SearchText",
            "region": "ap-northeast-1",
            "language": None,
            "political_view": None,
            "political_view_enabled": True,
        }
        result = self._result(title="Stored")
        layer = Mock()
        self.dialog._parse_position = Mock(return_value=[139.7, 35.6])
        self.dialog._build_request = Mock(return_value=request)
        self.dialog._send_request = Mock(return_value=result)
        self.dialog.places.add_point_layer = Mock(return_value=layer)
        self.dialog._set_pagination = Mock()
        self.dialog.localization_preferences = Mock()
        fake_iface = Mock()

        with (
            patch.object(self.places_module, "iface", fake_iface),
            patch.object(self.places_module, "push_message"),
        ):
            self.dialog._search()

        self.dialog._send_request.assert_called_once_with(request, "Storage")
        self.dialog.places.add_point_layer.assert_called_once_with(
            result,
            "SearchText",
            intended_use="Storage",
        )
        fake_iface.setActiveLayer.assert_called_once_with(layer)

    def test_failed_new_search_keeps_previous_pagination(self):
        previous_request = {"function": "SearchText"}
        self.dialog._pagination_request = previous_request
        self.dialog._next_token = "previous-page"
        self.dialog.button_load_more.setEnabled(True)
        request = {
            "function": "SearchText",
            "region": "ap-northeast-1",
            "language": None,
            "political_view": None,
            "political_view_enabled": True,
        }
        self.dialog._parse_position = Mock(return_value=[139.7, 35.6])
        self.dialog._build_request = Mock(return_value=request)
        self.dialog._send_request = Mock(side_effect=RuntimeError("network error"))
        self.dialog.localization_preferences = Mock()

        with patch.object(self.places_module, "show_error"):
            self.dialog._search()

        assert self.dialog._pagination_request is previous_request
        assert self.dialog._next_token == "previous-page"
        assert self.dialog.button_load_more.isEnabled()

    def test_pagination_tooltip_names_layer_and_storage_request(self):
        places, layer = _places_with_layer(self._result())
        self.dialog.places = places
        self.dialog._set_pagination(
            {"function": "SearchText"},
            layer,
            self._result(token="page-2"),
        )

        tooltip = self.dialog.button_load_more.toolTip()
        assert layer.name() in tooltip
        assert "Storage request" in tooltip

    def test_append_page_deduplicates_only_drawable_results(self):
        places, layer = _places_with_layer(self._result("place-1"))
        self.dialog.places = places
        self.dialog._pagination_place_ids = {"place-1"}
        self.dialog._next_token = "page-2"
        result = {
            "ResultItems": [
                {
                    "PlaceId": "place-1",
                    "Position": [140.0, 36.0],
                    "Address": {"Country": {"Code3": "USA"}},
                },
                {
                    "PlaceId": "place-2",
                    "Position": None,
                    "Address": {"Country": {"Code3": "USA"}},
                },
                {
                    "PlaceId": "place-2",
                    "Position": [140.1, 36.1],
                    "Address": {"Country": {"Code3": "USA"}},
                },
                {
                    "PlaceId": "place-3",
                    "Position": [140.2, 36.2],
                    "Address": {"Country": {"Code3": "USA"}},
                },
                {
                    "PlaceId": "place-3",
                    "Position": [140.3, 36.3],
                    "Address": {"Country": {"Code3": "USA"}},
                },
            ],
            "NextToken": "page-3",
        }

        added = self.dialog._append_result_page(
            layer,
            {"function": "SearchText"},
            result,
        )

        assert added == 2
        assert len(list(layer.getFeatures())) == 3
        assert self.dialog._pagination_place_ids == {
            "place-1",
            "place-2",
            "place-3",
        }
        assert self.dialog._next_token == "page-3"

    def test_failed_page_append_clears_pagination(self):
        places, layer = _places_with_layer(self._result("place-1"))
        places.add_features = Mock(side_effect=RuntimeError("write failed"))
        self.dialog.places = places
        self.dialog._pagination_place_ids = {"place-1"}
        self.dialog._next_token = "page-2"

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            self.dialog._append_result_page(
                layer,
                {"function": "SearchText"},
                self._result("place-2", token="page-3"),
            )

        assert self.dialog._pagination_place_ids == set()
        assert self.dialog._next_token is None

    def test_load_more_does_not_request_while_layer_is_editable(self):
        places, layer = _places_with_layer(self._result(token="page-2"))
        credentials = ("ap-northeast-1", "v1.public.test")
        places.configuration_handler = Mock()
        places.configuration_handler.get_credentials.return_value = credentials
        QgsProject.instance().addMapLayer(layer)
        self.addCleanup(QgsProject.instance().removeMapLayer, layer.id())
        self.dialog.places = places
        self.dialog._pagination_request = {
            "function": "SearchText",
            "region": "ap-northeast-1",
            "credentials": credentials,
        }
        self.dialog._pagination_layer = layer
        self.dialog._next_token = "page-2"
        self.dialog._send_request = Mock()
        assert layer.startEditing()
        self.addCleanup(layer.rollBack)

        with patch.object(self.places_module, "show_warning"):
            self.dialog._load_more()

        places.configuration_handler.get_credentials.assert_not_called()
        self.dialog._send_request.assert_not_called()
        assert self.dialog._next_token == "page-2"

    def test_load_more_does_not_append_if_editing_starts_during_request(self):
        places, layer = _places_with_layer(self._result(token="page-2"))
        credentials = ("ap-northeast-1", "v1.public.test")
        places.configuration_handler = Mock()
        places.configuration_handler.get_credentials.return_value = credentials
        QgsProject.instance().addMapLayer(layer)
        self.addCleanup(QgsProject.instance().removeMapLayer, layer.id())
        self.dialog.places = places
        self.dialog._pagination_request = {
            "function": "SearchText",
            "region": "ap-northeast-1",
            "credentials": credentials,
        }
        self.dialog._pagination_layer = layer
        self.dialog._next_token = "page-2"
        self.dialog._pagination_place_ids = {"place-1"}

        def start_editing(*_args):
            assert layer.startEditing()
            return self._result("place-2", token="page-3")

        self.dialog._send_request = Mock(side_effect=start_editing)
        self.dialog._append_result_page = Mock()
        self.addCleanup(layer.rollBack)

        with patch.object(self.places_module, "show_warning"):
            self.dialog._load_more()

        self.dialog._append_result_page.assert_not_called()
        assert self.dialog._pagination_request is None
        assert self.dialog._next_token is None
        assert not self.dialog.button_load_more.isEnabled()

        assert layer.rollBack()
        self.dialog._load_more()
        self.dialog._send_request.assert_called_once()

    def test_load_more_does_not_retry_after_page_append_fails(self):
        places, layer = _places_with_layer(self._result(token="page-2"))
        credentials = ("ap-northeast-1", "v1.public.test")
        places.configuration_handler = Mock()
        places.configuration_handler.get_credentials.return_value = credentials
        places.add_features = Mock(side_effect=RuntimeError("write failed"))
        QgsProject.instance().addMapLayer(layer)
        self.addCleanup(QgsProject.instance().removeMapLayer, layer.id())
        self.dialog.places = places
        self.dialog._pagination_request = {
            "function": "SearchText",
            "region": "ap-northeast-1",
            "credentials": credentials,
        }
        self.dialog._pagination_layer = layer
        self.dialog._next_token = "page-2"
        self.dialog._pagination_place_ids = {"place-1"}
        self.dialog._send_request = Mock(
            return_value=self._result("place-2", token="page-3")
        )
        self.dialog._report_search_error = Mock()

        self.dialog._load_more()
        self.dialog._load_more()

        self.dialog._send_request.assert_called_once()
        self.dialog._report_search_error.assert_called_once()
        assert self.dialog._pagination_request is None
        assert self.dialog._next_token is None
        assert not self.dialog.button_load_more.isEnabled()

    def test_load_more_can_retry_after_network_failure(self):
        places, layer = _places_with_layer(self._result(token="page-2"))
        credentials = ("ap-northeast-1", "v1.public.test")
        places.configuration_handler = Mock()
        places.configuration_handler.get_credentials.return_value = credentials
        QgsProject.instance().addMapLayer(layer)
        self.addCleanup(QgsProject.instance().removeMapLayer, layer.id())
        self.dialog.places = places
        self.dialog._pagination_request = {
            "function": "SearchText",
            "region": "ap-northeast-1",
            "credentials": credentials,
        }
        self.dialog._pagination_layer = layer
        self.dialog._next_token = "page-2"
        self.dialog._pagination_place_ids = {"place-1"}
        self.dialog._send_request = Mock(side_effect=RuntimeError("network failed"))
        self.dialog._report_search_error = Mock()

        self.dialog._load_more()

        assert self.dialog._next_token == "page-2"
        assert self.dialog.button_load_more.isEnabled()

        self.dialog._send_request.side_effect = None
        self.dialog._send_request.return_value = self._result("place-2", token="page-3")
        self.dialog._load_more()

        assert self.dialog._send_request.call_count == 2
        assert self.dialog._next_token == "page-3"

    def test_load_more_stops_if_place_ids_change_during_request(self):
        places, layer = _places_with_layer(self._result(token="page-2"))
        credentials = ("ap-northeast-1", "v1.public.test")
        places.configuration_handler = Mock()
        places.configuration_handler.get_credentials.return_value = credentials
        QgsProject.instance().addMapLayer(layer)
        self.addCleanup(QgsProject.instance().removeMapLayer, layer.id())
        self.dialog.places = places
        self.dialog._pagination_request = {
            "function": "SearchText",
            "region": "ap-northeast-1",
            "credentials": credentials,
        }
        self.dialog._pagination_layer = layer
        self.dialog._next_token = "page-2"
        self.dialog._pagination_place_ids = {"place-1"}

        def change_place_id(*_args):
            feature = next(layer.getFeatures())
            field = layer.fields().indexOf(PlacesFunctions.FIELD_PLACE_ID)
            assert layer.startEditing()
            assert layer.changeAttributeValue(feature.id(), field, "changed-place")
            assert layer.commitChanges()
            return self._result("place-2", token="page-3")

        self.dialog._send_request = Mock(side_effect=change_place_id)
        self.dialog._append_result_page = Mock()

        with patch.object(self.places_module, "show_warning"):
            self.dialog._load_more()

        self.dialog._append_result_page.assert_not_called()
        assert self.dialog._next_token is None

    def test_load_more_stops_if_settings_change_during_request(self):
        places, layer = _places_with_layer(self._result(token="page-2"))
        credentials = ("ap-northeast-1", "v1.public.test")
        places.configuration_handler = Mock()
        places.configuration_handler.get_credentials.return_value = credentials
        QgsProject.instance().addMapLayer(layer)
        self.addCleanup(QgsProject.instance().removeMapLayer, layer.id())
        self.dialog.places = places
        self.dialog._pagination_request = {
            "function": "SearchText",
            "region": "ap-northeast-1",
            "credentials": credentials,
        }
        self.dialog._pagination_layer = layer
        self.dialog._next_token = "page-2"
        self.dialog._pagination_place_ids = {"place-1"}

        def change_settings(*_args):
            self.dialog._clear_pagination()
            return self._result("place-2", token="page-3")

        self.dialog._send_request = Mock(side_effect=change_settings)
        self.dialog._append_result_page = Mock()

        self.dialog._load_more()

        self.dialog._append_result_page.assert_not_called()
        assert self.dialog._next_token is None

    def test_get_details_state_change_is_reported(self):
        error = PlacesOperationCancelledError("A selected PlaceId changed.")
        with patch.object(self.places_module, "show_warning") as warning:
            self.dialog._report_enrich_error(error)
        warning.assert_called_once_with(self.dialog, "Update Details", str(error))

    def test_get_details_ui_cancel_remains_silent(self):
        self.dialog._cancelled = True
        with (
            patch.object(self.places_module, "show_warning") as warning,
            patch.object(self.places_module, "show_error") as error,
        ):
            self.dialog._report_enrich_error(
                PlacesOperationCancelledError("The Places operation was cancelled.")
            )
        warning.assert_not_called()
        error.assert_not_called()

    def test_get_details_keeps_limited_region_button_disabled(self):
        _, layer = _places_with_layer(self._result())
        layer.selectAll()
        feature_id = next(layer.getFeatures()).id()
        current_region = ["ap-northeast-1"]

        service = Mock()
        service.configuration_handler.get_setting.side_effect = (
            lambda _key: current_region[0]
        )
        service.configuration_handler.get_credentials.return_value = (
            "ap-northeast-1",
            "v1.public.test",
        )
        service.enrichment_targets.return_value = [(feature_id, "place-1")]
        service.apply_feature_details.return_value = 1

        def change_region(*_args, **_kwargs):
            current_region[0] = "ap-southeast-1"
            self.dialog.refresh_region_capabilities()
            return {feature_id: {PlacesFunctions.FIELD_PHONE: "0123"}}

        service.fetch_selected_feature_details.side_effect = change_region
        self.dialog.places = service
        self.dialog.localization_preferences = Mock()
        fake_iface = Mock()
        fake_iface.activeLayer.return_value = layer

        with (
            patch.object(self.places_module, "iface", fake_iface),
            patch.object(self.places_module, "push_message"),
        ):
            self.dialog._enrich()

        assert not self.dialog.button_enrich.isEnabled()

    def test_get_details_uses_one_storage_pass(self):
        _, layer = _places_with_layer(self._result())
        layer.selectAll()
        feature_id = next(layer.getFeatures()).id()
        targets = [(feature_id, "place-1")]
        stored_values = {feature_id: {PlacesFunctions.FIELD_PHONE: "stored-phone"}}

        service = Mock()
        credentials = ("ap-northeast-1", "v1.public.test")
        service.configuration_handler.get_credentials.return_value = credentials
        service.enrichment_targets.return_value = targets
        service.fetch_selected_feature_details.return_value = stored_values
        service.apply_feature_details.return_value = 1
        self.dialog.places = service
        self.dialog.localization_preferences = Mock()
        fake_iface = Mock()
        fake_iface.activeLayer.return_value = layer

        with (
            patch.object(self.places_module, "iface", fake_iface),
            patch.object(self.places_module, "push_message"),
        ):
            self.dialog._enrich()

        service.fetch_selected_feature_details.assert_called_once()
        call = service.fetch_selected_feature_details.call_args
        assert call.kwargs["intended_use"] == "Storage"
        assert call.kwargs["targets"] == targets
        assert call.kwargs["credentials"] == credentials
        service.apply_feature_details.assert_called_once_with(layer, stored_values)


if __name__ == "__main__":
    unittest.main()
