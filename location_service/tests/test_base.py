import unittest

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from location_service.utils.configuration_handler import (
        ConfigurationError,
        ConfigurationHandler,
    )


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
