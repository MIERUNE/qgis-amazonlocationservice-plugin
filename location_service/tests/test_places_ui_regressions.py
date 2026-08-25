import unittest
from unittest.mock import Mock, patch

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    import qgis.utils
    from qgis.PyQt.QtGui import QShowEvent

    from location_service.ui.places import places as places_module
    from location_service.ui.places.places import PlacesUi

    HAS_IFACE = qgis.utils.iface is not None
else:
    HAS_IFACE = False


@unittest.skipUnless(HAS_IFACE, "A running QGIS iface is required")
class TestPlacesUiRegressions(unittest.TestCase):
    """Covers Places UI state changes that occur during nested Qt event loops."""

    def setUp(self):
        self.dialog = PlacesUi()
        self.dialog.language_comboBox.setEditText("Default")
        self.dialog.political_view_comboBox.setCurrentIndex(0)

    def tearDown(self):
        self.dialog.deleteLater()

    @staticmethod
    def _result(title="Place", country_code="USA", token=None):
        country = {} if country_code is None else {"Code3": country_code}
        result = {
            "ResultItems": [
                {
                    "Title": title,
                    "PlaceId": "place-2",
                    "Position": [139.7, 35.6],
                    "Address": {"Country": country},
                }
            ]
        }
        if token is not None:
            result["NextToken"] = token
        return result

    @staticmethod
    def _search_request(language=None):
        credentials = ("ap-southeast-1", "v1.public.test")
        return {
            "function": "SearchText",
            "position": [139.7, 35.6],
            "max_results": 10,
            "language": language,
            "political_view": None,
            "political_view_enabled": False,
            "text": "coffee",
            "countries": None,
            "travel_mode": None,
            "additional_features": ["TimeZone"],
            "region": credentials[0],
            "credentials": credentials,
        }

    def _prepare_limited_region_search(self, saved_language):
        preferences = Mock()
        preferences.load.return_value = (saved_language, "")
        self.dialog.localization_preferences = preferences
        self.dialog._restore_localization_preferences()
        self.dialog._configured_region = Mock(return_value="ap-southeast-1")
        self.dialog.refresh_region_capabilities()

        self.dialog.text_lineEdit.setText("coffee")
        self.dialog._parse_position = Mock(return_value=[139.7, 35.6])
        self.dialog.places.configuration_handler = Mock()
        self.dialog.places.configuration_handler.get_credentials.return_value = (
            "ap-southeast-1",
            "v1.public.test",
        )
        self.dialog._send_request = Mock(return_value=self._result())
        self.dialog.places.add_point_layer = Mock(return_value=Mock())
        self.dialog._set_pagination = Mock()
        return preferences

    def test_settings_refresh_cancels_an_active_search(self):
        request = self._search_request()
        self.dialog._parse_position = Mock(return_value=[139.7, 35.6])
        self.dialog._build_request = Mock(return_value=request)
        self.dialog._apply_region_capabilities = Mock()
        self.dialog.places.api_handler = Mock()
        self.dialog.places.add_point_layer = Mock()

        def refresh_settings(*_args):
            self.dialog.refresh_region_capabilities()
            return {
                "ResultItems": [
                    {
                        "PlaceId": "old-place",
                        "Position": [139.7, 35.6],
                    }
                ],
                "NextToken": "old-page-2",
            }

        self.dialog._send_request = Mock(side_effect=refresh_settings)

        self.dialog._search()

        self.dialog.places.api_handler.abort.assert_called_once_with()
        self.dialog.places.add_point_layer.assert_not_called()
        assert self.dialog._pagination_request is None
        assert self.dialog._next_token is None
        assert not self.dialog.button_load_more.isEnabled()

    def test_search_does_not_send_after_credentials_are_cancelled(self):
        request = self._search_request()
        self.dialog._parse_position = Mock(return_value=[139.7, 35.6])

        def cancel_during_credentials(*_args):
            self.dialog._cancelled = True
            return request

        self.dialog._build_request = Mock(side_effect=cancel_during_credentials)
        self.dialog._send_request = Mock()

        self.dialog._search()

        self.dialog._send_request.assert_not_called()
        assert not self.dialog._busy

    def test_limited_region_search_preserves_automatically_replaced_language(self):
        preferences = self._prepare_limited_region_search("ja")
        assert self.dialog.language_comboBox.currentText() == "Default"

        with (
            patch.object(places_module, "iface"),
            patch.object(places_module, "push_message"),
        ):
            self.dialog._search()

        preferences.save.assert_called_once_with("ja", "")

    def test_limited_region_search_saves_an_explicit_supported_language(self):
        preferences = self._prepare_limited_region_search("ja")
        self.dialog.language_comboBox.setCurrentText("en")

        with (
            patch.object(places_module, "iface"),
            patch.object(places_module, "push_message"),
        ):
            self.dialog._search()

        preferences.save.assert_called_once_with("en", "")

    def test_limited_region_search_saves_an_explicit_default_language(self):
        preferences = self._prepare_limited_region_search("ja")
        default_index = self.dialog.language_comboBox.findText("Default")
        self.dialog.language_comboBox.activated.emit(default_index)

        with (
            patch.object(places_module, "iface"),
            patch.object(places_module, "push_message"),
        ):
            self.dialog._search()

        preferences.save.assert_called_once_with(None, "")

    def test_unsupported_limited_region_language_keeps_previous_pagination(self):
        self._prepare_limited_region_search("ja")
        self.dialog.language_comboBox.setEditText("ja")
        request = {"function": "SearchText"}
        layer = Mock()
        self.dialog._pagination_request = request
        self.dialog._pagination_layer = layer
        self.dialog._pagination_place_ids = {"place-1"}
        self.dialog._next_token = "page-2"
        self.dialog.button_load_more.setEnabled(True)
        self.dialog.places.search_text = Mock()

        with patch.object(places_module, "show_error") as error:
            self.dialog._search()

        self.dialog._send_request.assert_not_called()
        self.dialog.places.search_text.assert_not_called()
        error.assert_called_once()
        assert error.call_args.args[1] == "Input Error"
        assert "not supported" in error.call_args.args[2]
        assert self.dialog._pagination_request is request
        assert self.dialog._pagination_layer is layer
        assert self.dialog._pagination_place_ids == {"place-1"}
        assert self.dialog._next_token == "page-2"
        assert self.dialog.button_load_more.isEnabled()

    def test_invalid_new_search_keeps_previous_pagination(self):
        request = {"function": "SearchText"}
        layer = Mock()
        self.dialog._pagination_request = request
        self.dialog._pagination_layer = layer
        self.dialog._pagination_place_ids = {"place-1"}
        self.dialog._next_token = "page-2"
        self.dialog.button_load_more.setEnabled(True)
        self.dialog._parse_position = Mock(return_value=[139.7, 35.6])
        self.dialog.text_lineEdit.clear()

        with patch.object(places_module, "show_error"):
            self.dialog._search()

        assert self.dialog._pagination_request is request
        assert self.dialog._pagination_layer is layer
        assert self.dialog._pagination_place_ids == {"place-1"}
        assert self.dialog._next_token == "page-2"
        assert self.dialog.button_load_more.isEnabled()

    def test_search_adds_japan_and_unknown_country_results(self):
        request = self._search_request()
        self.dialog._parse_position = Mock(return_value=[139.7, 35.6])
        self.dialog._build_request = Mock(return_value=request)
        self.dialog.localization_preferences = Mock()
        self.dialog.localization_preferences.load.return_value = ("", "")
        self.dialog.places.add_point_layer = Mock(return_value=Mock())
        self.dialog._set_pagination = Mock()
        fake_iface = Mock()

        for country_code in ("JPN", None):
            with self.subTest(country_code=country_code):
                self.dialog.places.add_point_layer.reset_mock()
                self.dialog._send_request = Mock(
                    return_value=self._result(
                        "Stored result", country_code=country_code
                    )
                )
                with (
                    patch.object(places_module, "iface", fake_iface),
                    patch.object(places_module, "show_warning") as warning,
                    patch.object(places_module, "show_error") as error,
                    patch.object(places_module, "push_message"),
                ):
                    self.dialog._search()

                error.assert_not_called()
                warning.assert_not_called()
                self.dialog._send_request.assert_called_once_with(request, "Storage")
                self.dialog.places.add_point_layer.assert_called_once_with(
                    self.dialog._send_request.return_value,
                    "SearchText",
                    intended_use="Storage",
                )

    def test_load_more_clears_a_request_with_an_old_api_key(self):
        handler = Mock()
        handler.get_credentials.return_value = ("us-east-1", "new-key")
        self.dialog.places.configuration_handler = handler
        self.dialog._pagination_request = {
            "function": "SearchText",
            "region": "us-east-1",
            "credentials": ("us-east-1", "old-key"),
        }
        layer = Mock()
        layer.isEditable.return_value = False
        self.dialog._pagination_layer = layer
        self.dialog._pagination_place_ids = {"place-1"}
        self.dialog._next_token = "page-2"
        self.dialog._pagination_layer_is_available = Mock(return_value=True)
        self.dialog._layer_place_ids = Mock(return_value={"place-1"})
        self.dialog._send_request = Mock()

        with patch.object(places_module, "show_warning") as warning:
            self.dialog._load_more()

        self.dialog._send_request.assert_not_called()
        assert self.dialog._pagination_request is None
        assert self.dialog._next_token is None
        assert "region or API key changed" in warning.call_args.args[2]

    def test_load_more_does_not_send_after_credentials_are_cancelled(self):
        credentials = ("us-east-1", "test-key")
        handler = Mock()

        def cancel_during_credentials():
            self.dialog._cancelled = True
            return credentials

        handler.get_credentials.side_effect = cancel_during_credentials
        self.dialog.places.configuration_handler = handler
        self.dialog._pagination_request = {
            "function": "SearchText",
            "region": "us-east-1",
            "credentials": credentials,
        }
        layer = Mock()
        layer.isEditable.return_value = False
        self.dialog._pagination_layer = layer
        self.dialog._pagination_place_ids = {"place-1"}
        self.dialog._next_token = "page-2"
        self.dialog._pagination_layer_is_available = Mock(return_value=True)
        self.dialog._layer_place_ids = Mock(return_value={"place-1"})
        self.dialog._send_request = Mock()

        self.dialog._load_more()

        self.dialog._send_request.assert_not_called()
        assert not self.dialog._busy

    def test_cancelled_load_more_clears_pagination(self):
        request = self._search_request()
        layer = Mock()
        layer.isEditable.return_value = False
        self.dialog.places.configuration_handler = Mock()
        self.dialog.places.configuration_handler.get_credentials.return_value = request[
            "credentials"
        ]
        self.dialog._pagination_request = request
        self.dialog._pagination_layer = layer
        self.dialog._pagination_place_ids = {"place-1"}
        self.dialog._next_token = "page-2"
        self.dialog.button_load_more.setEnabled(True)
        self.dialog._pagination_layer_is_available = Mock(return_value=True)
        self.dialog._layer_place_ids = Mock(return_value={"place-1"})

        def cancel_request(*_args):
            self.dialog._cancelled = True
            raise RuntimeError("request cancelled")

        self.dialog._send_request = Mock(side_effect=cancel_request)

        with patch.object(places_module, "show_error") as error:
            self.dialog._load_more()
            self.dialog._load_more()

        self.dialog._send_request.assert_called_once()
        error.assert_not_called()
        assert self.dialog._pagination_request is None
        assert self.dialog._pagination_layer is None
        assert self.dialog._next_token is None
        assert self.dialog._pagination_place_ids == set()
        assert not self.dialog.button_load_more.isEnabled()
        assert not self.dialog._busy

    def test_load_more_adds_japan_and_unknown_country_results(self):
        request = self._search_request()
        layer = Mock()
        layer.isEditable.return_value = False
        self.dialog.places.configuration_handler = Mock()
        self.dialog.places.configuration_handler.get_credentials.return_value = request[
            "credentials"
        ]
        self.dialog.places.add_features = Mock()
        self.dialog._pagination_layer_is_available = Mock(return_value=True)
        self.dialog._layer_place_ids = Mock(return_value={"place-1"})

        for country_code in ("JPN", None):
            with self.subTest(country_code=country_code):
                self.dialog._pagination_request = request
                self.dialog._pagination_layer = layer
                self.dialog._pagination_place_ids = {"place-1"}
                self.dialog._next_token = "page-2"
                self.dialog.button_load_more.setEnabled(True)
                self.dialog._send_request = Mock(
                    return_value=self._result(
                        "Stored next page", country_code, "page-3"
                    )
                )
                self.dialog.places.add_features.reset_mock()

                with (
                    patch.object(places_module, "show_warning") as warning,
                    patch.object(places_module, "show_error") as error,
                    patch.object(places_module, "push_message") as message,
                ):
                    self.dialog._load_more()

                error.assert_not_called()
                warning.assert_not_called()
                self.dialog._send_request.assert_called_once()
                self.dialog.places.add_features.assert_called_once()
                assert self.dialog._pagination_request is request
                assert self.dialog._next_token == "page-3"
                assert self.dialog.button_load_more.isEnabled()
                assert any(
                    call.args and call.args[0] == places_module.SUCCESS
                    for call in message.call_args_list
                )

    def test_load_more_warns_if_the_layer_disappears_during_the_request(self):
        request = self._search_request()
        layer = Mock()
        layer.isEditable.return_value = False
        self.dialog.places.configuration_handler = Mock()
        self.dialog.places.configuration_handler.get_credentials.return_value = request[
            "credentials"
        ]
        self.dialog._pagination_request = request
        self.dialog._pagination_layer = layer
        self.dialog._pagination_place_ids = {"place-1"}
        self.dialog._next_token = "page-2"
        self.dialog.button_load_more.setEnabled(True)
        self.dialog._pagination_layer_is_available = Mock(side_effect=(True, False))
        self.dialog._layer_place_ids = Mock(return_value={"place-1"})
        self.dialog._send_request = Mock(return_value=self._result(token="page-3"))
        self.dialog._append_result_page = Mock()

        with patch.object(places_module, "show_warning") as warning:
            self.dialog._load_more()

        warning.assert_called_once()
        assert warning.call_args.args[1] == "Load More"
        assert "no longer available" in warning.call_args.args[2]
        self.dialog._append_result_page.assert_not_called()
        assert self.dialog._pagination_request is None
        assert self.dialog._next_token is None

    def test_search_validates_query_before_reading_credentials(self):
        handler = Mock()
        self.dialog.places.configuration_handler = handler
        self.dialog.text_lineEdit.clear()

        with self.assertRaisesRegex(ValueError, "QueryText"):
            self.dialog._build_request("SearchText", [139.7, 35.6], 10)

        handler.get_credentials.assert_not_called()

    def test_get_details_validates_layer_before_reading_credentials(self):
        service = Mock()
        error = ValueError("The active layer is not a Places result layer.")
        service.enrichment_targets.side_effect = error
        self.dialog.places = service
        self.dialog._report_enrich_error = Mock()
        self.dialog._apply_region_capabilities = Mock()
        fake_iface = Mock()
        fake_iface.activeLayer.return_value = object()

        with patch.object(places_module, "iface", fake_iface):
            self.dialog._enrich()

        service.configuration_handler.get_credentials.assert_not_called()
        self.dialog._report_enrich_error.assert_called_once_with(error)

    def test_mixed_result_items_are_ignored_consistently(self):
        valid = {
            "Title": "Valid place",
            "PlaceId": "place-1",
            "Position": [139.7, 35.6],
        }
        result = {"ResultItems": [None, "not an object", valid, {"Position": []}]}

        assert self.dialog._has_drawable_results(result)
        assert self.dialog._new_page_items(result) == ([valid], {"place-1"})

    def test_spontaneous_show_keeps_unsent_localization_values(self):
        class SpontaneousShowEvent(QShowEvent):
            def spontaneous(self):
                return True

        self.dialog._apply_style = Mock()
        self.dialog._restore_localization_preferences = Mock()
        self.dialog._apply_region_capabilities = Mock()

        self.dialog.showEvent(SpontaneousShowEvent())

        self.dialog._apply_style.assert_called_once_with()
        self.dialog._restore_localization_preferences.assert_not_called()
        self.dialog._apply_region_capabilities.assert_not_called()


if __name__ == "__main__":
    unittest.main()
