import unittest

from location_service.functions.places_storage import normalize_country_code
from location_service.ui.places import constants


class TestPlacesRegionCapabilities(unittest.TestCase):
    """QGIS-free checks for region-specific Places restrictions."""

    def test_global_region_accepts_full_options(self):
        constants.validate_region_options(
            "ap-northeast-1",
            "Geocode",
            language="ja",
            political_view="JPN",
            additional_features=["Contact", "TimeZone"],
            intended_use="Storage",
            query_radius=1_000_000,
        )

    def test_limited_region_rejects_geocode(self):
        with self.assertRaisesRegex(ValueError, "Geocode"):
            constants.validate_region_options("ap-southeast-1", "Geocode")

    def test_limited_region_accepts_supported_search_options(self):
        constants.validate_region_options(
            "ap-southeast-5",
            "SearchNearby",
            language="en",
            additional_features=["TimeZone"],
            intended_use="Storage",
            query_radius=100_000,
        )

    def test_limited_region_rejects_unsupported_options(self):
        cases = (
            {"political_view": "IND"},
            {"language": "ja"},
            {"additional_features": ["Contact"]},
            {"query_radius": 100_001},
        )
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                constants.validate_region_options(
                    "ap-southeast-1", "SearchNearby", **options
                )

    def test_limited_region_rejects_get_place_intended_use(self):
        for intended_use in ("SingleUse", "Storage"):
            with (
                self.subTest(intended_use=intended_use),
                self.assertRaisesRegex(ValueError, "IntendedUse"),
            ):
                constants.validate_region_options(
                    "ap-southeast-1", "GetPlace", intended_use=intended_use
                )

    def test_limited_region_accepts_get_place_without_intended_use(self):
        constants.validate_region_options("ap-southeast-1", "GetPlace")

    def test_global_searches_request_contact_and_time_zone(self):
        for operation in ("SearchText", "SearchNearby"):
            with self.subTest(operation=operation):
                assert constants.automatic_additional_features(
                    "ap-northeast-1", operation
                ) == ["Contact", "TimeZone"]

    def test_global_geocoding_requests_time_zone(self):
        for operation in ("Geocode", "ReverseGeocode"):
            with self.subTest(operation=operation):
                assert constants.automatic_additional_features(
                    "ap-northeast-1", operation
                ) == ["TimeZone"]

    def test_limited_regions_request_only_time_zone(self):
        for operation in constants.FUNCTIONS:
            with self.subTest(operation=operation):
                assert constants.automatic_additional_features(
                    "ap-southeast-1", operation
                ) == ["TimeZone"]


class TestPlacesStorageHelpers(unittest.TestCase):
    """Checks country-code normalization used by result attributes."""

    def test_normalizes_country_codes(self):
        assert normalize_country_code(" jp ") == "JP"
        assert normalize_country_code("usa") == "USA"

    def test_rejects_invalid_country_codes(self):
        for value in (None, "", "123", "\uff2a\uff30", "日本国", "USA1"):
            with self.subTest(value=value):
                assert normalize_country_code(value) is None


if __name__ == "__main__":
    unittest.main()
