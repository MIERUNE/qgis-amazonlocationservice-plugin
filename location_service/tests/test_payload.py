import unittest

from location_service.functions.payload import prune_payload


class TestPrunePayload(unittest.TestCase):
    """Tests for the QGIS-free request-body pruning helper."""

    def test_drops_none_and_empty(self):
        body = {
            "QueryText": "tokyo",
            "MaxResults": 10,
            "BiasPosition": None,
            "Empty": "",
            "EmptyList": [],
            "EmptyDict": {},
        }
        assert prune_payload(body) == {"QueryText": "tokyo", "MaxResults": 10}

    def test_keeps_zero_and_false(self):
        body = {"Count": 0, "Flag": False, "Skip": None}
        assert prune_payload(body) == {"Count": 0, "Flag": False}

    def test_keeps_non_empty_position(self):
        body = {"BiasPosition": [139.7, 35.6]}
        assert prune_payload(body) == {"BiasPosition": [139.7, 35.6]}

    def test_prunes_nested_dicts(self):
        body = {"Avoid": {"TollRoads": True, "Empty": None}, "Drop": {}}
        assert prune_payload(body) == {"Avoid": {"TollRoads": True}}

    def test_drops_fully_empty_nested_dict(self):
        body = {"Avoid": {"A": None, "B": ""}, "Keep": 1}
        assert prune_payload(body) == {"Keep": 1}


if __name__ == "__main__":
    unittest.main()
