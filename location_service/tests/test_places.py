import unittest

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from qgis.core import QgsVectorLayer

    from location_service.functions.places import PlacesFunctions


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestPlacesFeatures(unittest.TestCase):
    """Validates conversion of place results into QGIS features."""

    def test_skips_item_without_position_and_keeps_valid_item(self):
        places = PlacesFunctions.__new__(PlacesFunctions)
        layer = QgsVectorLayer("Point?crs=EPSG:4326", "test", "memory")
        places.add_attributes(layer)

        places.add_features(
            layer,
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
            },
        )

        features = list(layer.getFeatures())
        assert len(features) == 1
        assert features[0][PlacesFunctions.FIELD_TITLE] == "Tokyo Station"
        point = features[0].geometry().asPoint()
        assert point.x() == 139.7671
        assert point.y() == 35.6812


if __name__ == "__main__":
    unittest.main()
