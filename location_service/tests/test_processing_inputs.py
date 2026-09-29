import unittest

from location_service.functions.places_requests import parse_country_codes
from location_service.functions.routes_requests import parse_thresholds
from location_service.ui.places.constants import parse_language


class TestParseThresholds(unittest.TestCase):
    """QGIS-free checks of the isoline threshold text parameter."""

    def test_converts_minutes_to_seconds(self):
        assert parse_thresholds("5, 10,15", "Time") == (300, 600, 900)

    def test_converts_kilometers_to_meters(self):
        assert parse_thresholds("0.5, 2", "Distance") == (500, 2000)

    def test_rejects_invalid_values(self):
        for text in ("", " , ", "five", "nan", "inf", "0.001"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_thresholds(text, "Time")

    def test_rejects_unknown_type(self):
        with self.assertRaisesRegex(ValueError, "Threshold type"):
            parse_thresholds("5", "Speed")


class TestParseCountryCodes(unittest.TestCase):
    """QGIS-free checks of the Countries text parameter."""

    def test_uppercases_codes(self):
        assert parse_country_codes(" jp, USA ,") == ["JP", "USA"]

    def test_empty_value_is_none(self):
        assert parse_country_codes("") is None
        assert parse_country_codes(None) is None

    def test_rejects_invalid_codes(self):
        for text in ("J", "JAPN", "日本", "J1"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_country_codes(text)


class TestParseLanguage(unittest.TestCase):
    """QGIS-free checks of the Language text parameter."""

    def test_default_is_none(self):
        for text in ("", "  ", "Default", None):
            with self.subTest(text=text):
                assert parse_language(text) is None

    def test_accepts_bcp47_codes(self):
        for text in ("ja", "en-US", "en-US-u-ca-gregory"):
            with self.subTest(text=text):
                assert parse_language(f" {text} ") == text

    def test_rejects_invalid_codes(self):
        for text in ("j", "not a language", "en-" + "a" * 40):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_language(text)


if __name__ == "__main__":
    unittest.main()
