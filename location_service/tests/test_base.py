import unittest
from unittest.mock import patch

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY, QgsProject
    from qgis.gui import QgsMapCanvas, QgsMapToolPan
    from qgis.PyQt.QtCore import QPoint
    from qgis.PyQt.QtWidgets import QLineEdit

    from location_service.utils import click_handler
    from location_service.utils.click_handler import (
        MapClickCoordinateUpdater,
        parse_lonlat,
        transform_to_wgs84,
    )
    from location_service.utils.configuration_handler import (
        ConfigurationError,
        ConfigurationHandler,
    )

    class _ReleaseEvent:
        """Minimal mouse-release event shared by the PyQt5 and PyQt6 tests."""

        def __init__(self, button):
            self._button = button

        def button(self):
            return self._button

        @staticmethod
        def pos():
            return QPoint(0, 0)

    class _FixedPointUpdater(MapClickCoordinateUpdater):
        """Map picker returning a stable point independent of canvas dimensions."""

        def toMapCoordinates(self, _position):
            return QgsPointXY(10.0, 20.0)


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestRegionValidation(unittest.TestCase):
    """Region validation prevents host injection through stored settings."""

    def test_accepts_real_regions(self):
        pattern = ConfigurationHandler.REGION_PATTERN
        for region in (
            "ap-northeast-1",
            "us-east-1",
            "eu-central-1",
            "us-gov-west-1",
            "ap-southeast-5",
        ):
            assert pattern.match(region), region

    def test_rejects_injection(self):
        pattern = ConfigurationHandler.REGION_PATTERN
        for region in (
            "evil.com/",
            "x@evil.com",
            "ap-northeast-1#",
            "us-east-1.evilhost.com",
            "ap-northeast-1:8080",
            "ap-northeast-1\n",
            "ap-northeast-\uff11",
            "ap-northeast-\u0967",
            "",
        ):
            assert not pattern.match(region), region


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestGetCredentials(unittest.TestCase):
    """Validates normalization and error handling for stored credentials."""

    def setUp(self):
        self.handler = ConfigurationHandler()
        self._original_settings = self.handler.get_settings()
        self._original_synced = self.handler._apikey_synced
        # Preserve the staticmethod descriptor so tearDown restores it correctly.
        self._original_manager = ConfigurationHandler.__dict__["_auth_manager"]
        # Keep the tests away from the real authentication database.
        ConfigurationHandler._auth_manager = staticmethod(lambda: None)

    def tearDown(self):
        self.handler._settings = self._original_settings
        self.handler._apikey_synced = self._original_synced
        ConfigurationHandler._auth_manager = self._original_manager

    def _set(self, region, apikey):
        self.handler._settings = {
            ConfigurationHandler.KEY_REGION: region,
            ConfigurationHandler.KEY_APIKEY: apikey,
        }
        self.handler._apikey_synced = False

    def test_strips_stored_values(self):
        self._set(" ap-northeast-1 ", " some-key \n")
        assert self.handler.get_credentials() == ("ap-northeast-1", "some-key")

    def test_rejects_missing_region(self):
        self._set("", "some-key")
        with self.assertRaises(ConfigurationError):
            self.handler.get_credentials()

    def test_rejects_invalid_region(self):
        self._set("us-east1", "some-key")
        with self.assertRaises(ConfigurationError):
            self.handler.get_credentials()

    def test_rejects_missing_apikey(self):
        self._set("ap-northeast-1", "")
        with self.assertRaises(ConfigurationError):
            self.handler.get_credentials()


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestParseLonLat(unittest.TestCase):
    """Coordinates must be finite and within the WGS84 longitude/latitude bounds."""

    def test_accepts_valid(self):
        assert parse_lonlat("139.7", "35.6") == [139.7, 35.6]
        assert parse_lonlat(" -180 ", " -90 ") == [-180.0, -90.0]

    def test_rejects_empty_and_non_numeric(self):
        assert parse_lonlat("", "35.6") is None
        assert parse_lonlat("abc", "35.6") is None

    def test_rejects_non_finite(self):
        # NaN and Infinity are not valid JSON values.
        for text in ("nan", "inf", "-inf", "1e400"):
            assert parse_lonlat(text, "35.6") is None, text

    def test_rejects_out_of_range(self):
        assert parse_lonlat("180.1", "35.6") is None
        assert parse_lonlat("139.7", "90.1") is None


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestCoordinateTransform(unittest.TestCase):
    """Validates conversion from the canvas CRS to WGS84."""

    def test_uses_the_canvas_crs_instead_of_the_project_crs(self):
        project = QgsProject.instance()
        original_crs = project.crs()
        project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        try:
            point = QgsPointXY(111319.49079327357, 0)
            result = transform_to_wgs84(
                point, QgsCoordinateReferenceSystem("EPSG:3857")
            )
        finally:
            project.setCrs(original_crs)

        assert abs(result.x() - 1.0) < 1e-6
        assert abs(result.y()) < 1e-6


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestMapClickCoordinateUpdater(unittest.TestCase):
    """The coordinate picker restores the map tool active before selection."""

    def setUp(self):
        self.canvas = QgsMapCanvas()
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        self.original_tool = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self.original_tool)

    def tearDown(self):
        current_tool = self.canvas.mapTool()
        if current_tool is not None:
            self.canvas.unsetMapTool(current_tool)
        self.canvas.deleteLater()

    def test_left_click_writes_coordinates_and_restores_original_tool(self):
        lon_edit = QLineEdit()
        lat_edit = QLineEdit()
        picker = _FixedPointUpdater(self.canvas, lon_edit, lat_edit)

        picker.arm()
        assert self.canvas.mapTool() is picker

        result = QgsPointXY(139.7671, 35.6812)
        with patch(
            "location_service.utils.click_handler.transform_to_wgs84",
            return_value=result,
        ):
            picker.canvasReleaseEvent(_ReleaseEvent(click_handler._LEFT_BUTTON))

        assert float(lon_edit.text()) == result.x()
        assert float(lat_edit.text()) == result.y()
        assert self.canvas.mapTool() is self.original_tool

    def test_replacing_picker_then_cancelling_restores_original_tool(self):
        start_lon_edit = QLineEdit()
        start_lat_edit = QLineEdit()
        end_lon_edit = QLineEdit()
        end_lat_edit = QLineEdit()
        start_picker = MapClickCoordinateUpdater(
            self.canvas, start_lon_edit, start_lat_edit
        )
        end_picker = MapClickCoordinateUpdater(self.canvas, end_lon_edit, end_lat_edit)

        start_picker.arm()
        assert self.canvas.mapTool() is start_picker
        end_picker.arm()
        assert self.canvas.mapTool() is end_picker

        end_picker.canvasReleaseEvent(_ReleaseEvent(click_handler._RIGHT_BUTTON))

        assert self.canvas.mapTool() is self.original_tool
        assert start_lon_edit.text() == ""
        assert start_lat_edit.text() == ""
        assert end_lon_edit.text() == ""
        assert end_lat_edit.text() == ""


if __name__ == "__main__":
    unittest.main()
