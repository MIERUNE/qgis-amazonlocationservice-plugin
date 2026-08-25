import unittest
from urllib.parse import urlparse

from location_service.functions.places_requests import (
    build_geocode_body,
    build_reverse_geocode_body,
    build_search_nearby_body,
    build_search_text_body,
)
from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from location_service.functions.places import PlacesFunctions


class _FakeConfigurationHandler:
    """Returns fixed credentials without touching QSettings or auth storage."""

    @staticmethod
    def get_credentials():
        return "ap-northeast-1", "v1.public.test"  # pragma: allowlist secret


class _CapturingApiHandler:
    """Records the requested URL instead of performing a network call."""

    def __init__(self):
        self.url = ""

    def send_json_get_request(self, url):
        self.url = url
        return {}

    def send_json_post_request(self, url, body):
        self.url = url
        self.body = body
        return {}


def _places_without_network():
    """Builds a PlacesFunctions whose handlers never leave the process."""
    places = PlacesFunctions.__new__(PlacesFunctions)
    places.configuration_handler = _FakeConfigurationHandler()
    places.api_handler = _CapturingApiHandler()
    return places


class TestPlacesRequestBodies(unittest.TestCase):
    """QGIS-free tests for the Places V2 request-body builders."""

    def test_search_text_with_bias(self):
        assert build_search_text_body("tokyo", 10, [139.7, 35.6]) == {
            "QueryText": "tokyo",
            "MaxResults": 10,
            "BiasPosition": [139.7, 35.6],
        }

    def test_search_text_without_geographic_context_fails(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            build_search_text_body("tokyo", 10)

    def test_search_text_with_options(self):
        body = build_search_text_body(
            "tokyo",
            10,
            [139.7, 35.6],
            include_countries=["JPN"],
            travel_mode="Car",
            additional_features=["Contact", "TimeZone"],
            political_view="IND",
            language="ja",
            intended_use="Storage",
        )
        assert body == {
            "QueryText": "tokyo",
            "MaxResults": 10,
            "BiasPosition": [139.7, 35.6],
            "Filter": {"IncludeCountries": ["JPN"]},
            "TravelMode": "Car",
            "AdditionalFeatures": ["Contact", "TimeZone"],
            "PoliticalView": "IND",
            "Language": "ja",
            "IntendedUse": "Storage",
        }

    def test_search_text_with_bounding_box(self):
        body = build_search_text_body(
            "tokyo", 10, bounding_box=[139.0, 35.0, 140.0, 36.0]
        )
        assert body["Filter"]["BoundingBox"] == [139.0, 35.0, 140.0, 36.0]

    def test_search_text_with_circle(self):
        body = build_search_text_body(
            "tokyo", 10, circle={"Center": [139.7, 35.6], "Radius": 1000}
        )
        assert body["Filter"]["Circle"] == {
            "Center": [139.7, 35.6],
            "Radius": 1000,
        }

    def test_search_text_with_multiple_geographic_contexts_fails(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            build_search_text_body(
                "tokyo",
                10,
                [139.7, 35.6],
                bounding_box=[139.0, 35.0, 140.0, 36.0],
            )

    def test_search_text_with_next_token(self):
        body = build_search_text_body("tokyo", 10, [139.7, 35.6], next_token="page-2")
        assert body["NextToken"] == "page-2"

    def test_search_text_omits_empty_next_token(self):
        body = build_search_text_body("tokyo", 10, [139.7, 35.6], next_token="")
        assert "NextToken" not in body

    def test_geocode_body(self):
        assert build_geocode_body("1-1 Chiyoda", 5, None) == {
            "QueryText": "1-1 Chiyoda",
            "MaxResults": 5,
        }

    def test_geocode_with_options(self):
        body = build_geocode_body(
            "1-1 Chiyoda",
            5,
            None,
            include_countries=["JPN", "USA"],
            address_names_mode="Matched",
            postal_code_mode="EnumerateSpannedLocalities",
            intended_use="Storage",
        )
        assert body == {
            "QueryText": "1-1 Chiyoda",
            "MaxResults": 5,
            "Filter": {"IncludeCountries": ["JPN", "USA"]},
            "AddressNamesMode": "Matched",
            "PostalCodeMode": "EnumerateSpannedLocalities",
            "IntendedUse": "Storage",
        }

    def test_geocode_with_time_zone(self):
        body = build_geocode_body("1-1 Chiyoda", 5, additional_features=["TimeZone"])
        assert body["AdditionalFeatures"] == ["TimeZone"]

    def test_reverse_geocode_without_radius(self):
        assert build_reverse_geocode_body(139.7, 35.6, 1, None) == {
            "QueryPosition": [139.7, 35.6],
            "MaxResults": 1,
        }

    def test_reverse_geocode_with_radius(self):
        assert build_reverse_geocode_body(139.7, 35.6, 3, 500) == {
            "QueryPosition": [139.7, 35.6],
            "MaxResults": 3,
            "QueryRadius": 500,
        }

    def test_reverse_geocode_with_intended_use(self):
        body = build_reverse_geocode_body(139.7, 35.6, 1, None, intended_use="Storage")
        assert body == {
            "QueryPosition": [139.7, 35.6],
            "MaxResults": 1,
            "IntendedUse": "Storage",
        }

    def test_reverse_geocode_with_time_zone(self):
        body = build_reverse_geocode_body(139.7, 35.6, additional_features=["TimeZone"])
        assert body["AdditionalFeatures"] == ["TimeZone"]

    def test_search_nearby_body(self):
        assert build_search_nearby_body(139.7, 35.6, 1000, 20) == {
            "QueryPosition": [139.7, 35.6],
            "QueryRadius": 1000,
            "MaxResults": 20,
        }

    def test_search_nearby_with_options(self):
        body = build_search_nearby_body(
            139.7,
            35.6,
            1000,
            20,
            additional_features=["Contact", "TimeZone"],
            language="en-US",
            intended_use="Storage",
        )
        assert body["AdditionalFeatures"] == ["Contact", "TimeZone"]
        assert body["Language"] == "en-US"
        assert body["IntendedUse"] == "Storage"

    def test_search_nearby_with_next_token(self):
        body = build_search_nearby_body(139.7, 35.6, 1000, 20, next_token="page-2")
        assert body["NextToken"] == "page-2"

    def test_search_nearby_omits_empty_next_token(self):
        body = build_search_nearby_body(139.7, 35.6, 1000, 20, next_token="")
        assert "NextToken" not in body

    def test_invalid_position_fails(self):
        with self.assertRaisesRegex(ValueError, "WGS 84"):
            build_search_text_body("tokyo", 10, [181.0, 35.6])

    def test_unrepresentable_position_fails(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            build_reverse_geocode_body(10**1000, 35.6)

    def test_invalid_bounding_box_fails(self):
        with self.assertRaisesRegex(ValueError, "south"):
            build_search_text_body("tokyo", 10, bounding_box=[139.0, 36.0, 140.0, 35.0])

    def test_invalid_circle_radius_fails(self):
        with self.assertRaisesRegex(ValueError, "between"):
            build_search_text_body(
                "tokyo", 10, circle={"Center": [139.7, 35.6], "Radius": 0}
            )

    def test_language_none_is_omitted(self):
        body = build_search_text_body("tokyo", 10, [139.7, 35.6], language=None)
        assert "Language" not in body

    def test_political_view_none_is_omitted(self):
        body = build_search_text_body("tokyo", 10, [139.7, 35.6], political_view=None)
        assert "PoliticalView" not in body

    def test_query_text_must_be_between_one_and_200_characters(self):
        with self.assertRaisesRegex(ValueError, "filled in"):
            build_search_text_body("", 10, [139.7, 35.6])
        with self.assertRaisesRegex(ValueError, "200"):
            build_geocode_body("x" * 201, 10)

    def test_include_countries_accepts_at_most_100_codes(self):
        with self.assertRaisesRegex(ValueError, "at most 100"):
            build_search_text_body(
                "tokyo",
                10,
                [139.7, 35.6],
                include_countries=["JPN"] * 101,
            )


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestBuildEndpoint(unittest.TestCase):
    """Tests for the shared endpoint builder used by every operation."""

    def test_interpolates_region(self):
        url = _places_without_network().build_endpoint("v2/search-text")
        assert url == (
            "https://places.geo.ap-northeast-1.amazonaws.com/v2/search-text"
            "?key=v1.public.test"
        )

    def test_encodes_key(self):
        places = _places_without_network()
        # A key containing "&" or "#" must not alter the query structure.
        places.configuration_handler.get_credentials = lambda: ("us-east-1", "a&b#c")
        url = places.build_endpoint("v2/geocode")
        assert url.endswith("/v2/geocode?key=a%26b%23c")

    def test_appends_query(self):
        url = _places_without_network().build_endpoint("v2/place/x", "language=ja")
        assert url.endswith("/v2/place/x?key=v1.public.test&language=ja")

    def test_search_accepts_a_captured_credentials_pair(self):
        places = _places_without_network()

        def unexpected_credentials_read():
            raise AssertionError("The captured credentials should be used.")

        places.configuration_handler.get_credentials = unexpected_credentials_read
        places.search_text(
            "tokyo",
            lon=139.7,
            lat=35.6,
            credentials=("us-west-2", "a&b"),
        )

        parsed = urlparse(places.api_handler.url)
        assert parsed.hostname == "places.geo.us-west-2.amazonaws.com"
        assert places.api_handler.url.endswith("?key=a%26b")


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestGetPlaceUrl(unittest.TestCase):
    """Tests for the GetPlace query string."""

    def _url(self, place_id="place-1", **kwargs):
        places = _places_without_network()
        places.get_place(place_id, **kwargs)
        return places.api_handler.url

    def test_repeats_additional_features(self):
        url = self._url(additional_features=["Contact", "TimeZone"])
        # A list of enum values must be repeated keys, not one joined value.
        assert "additional-features=Contact&additional-features=TimeZone" in url
        assert "Contact%2CTimeZone" not in url
        assert "Contact,TimeZone" not in url

    def test_includes_optional_parameters(self):
        url = self._url(
            additional_features=["Contact"],
            political_view="IND",
            language="ja",
            intended_use="Storage",
        )
        assert "political-view=IND" in url
        assert "language=ja" in url
        assert "intended-use=Storage" in url

    def test_omits_empty_query(self):
        url = self._url()
        assert url.endswith("v2/place/place-1?key=v1.public.test")

    def test_encodes_place_id(self):
        url = self._url(place_id="a/b c+d")
        assert "v2/place/a%2Fb%20c%2Bd?" in url

    def test_geocode_rejects_partial_bias_position(self):
        places = _places_without_network()
        with self.assertRaisesRegex(ValueError, "both longitude and latitude"):
            places.geocode("Tokyo", lon=139.7, lat=None)


if __name__ == "__main__":
    unittest.main()
