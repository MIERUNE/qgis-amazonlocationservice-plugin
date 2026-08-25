import unittest

from location_service.functions.places_storage import (
    drawable_result_items,
    result_position,
)


class TestDrawableResults(unittest.TestCase):
    """Covers safe filtering of Places search results."""

    def test_non_dict_position_is_not_drawable(self):
        for value in (None, "invalid", [139.7, 35.6]):
            with self.subTest(value=value):
                assert result_position(value) is None

    def test_filters_mixed_result_items(self):
        valid = {"PlaceId": "place-1", "Position": [139.7, 35.6]}
        response = {
            "ResultItems": [
                "invalid",
                None,
                {"PlaceId": "missing-position"},
                valid,
            ]
        }

        assert drawable_result_items(response) == [valid]

    def test_malformed_result_items_is_empty(self):
        for response in (None, {}, {"ResultItems": None}, {"ResultItems": {}}):
            with self.subTest(response=response):
                assert drawable_result_items(response) == []


if __name__ == "__main__":
    unittest.main()
