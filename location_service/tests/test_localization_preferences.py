import unittest
from unittest.mock import Mock, call, patch

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from qgis.PyQt.QtWidgets import QApplication

    from location_service.ui.maps import maps as maps_module
    from location_service.ui.maps.maps import MapsUi
    from location_service.utils import localization_preferences as preferences_module
    from location_service.utils.localization_preferences import LocalizationPreferences


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestLocalizationPreferences(unittest.TestCase):
    """Covers the shared QSettings values without changing real settings."""

    def test_load(self):
        settings = Mock()
        settings.value.side_effect = [" ja ", "IND"]

        with patch.object(preferences_module, "QSettings", return_value=settings):
            values = LocalizationPreferences().load()

        assert values == ("ja", "IND")
        settings.beginGroup.assert_called_once_with("/location-service")
        assert settings.value.call_args_list == [
            call("last_language", ""),
            call("last_political_view", ""),
        ]
        settings.endGroup.assert_called_once_with()

    def test_save(self):
        settings = Mock()

        with patch.object(preferences_module, "QSettings", return_value=settings):
            LocalizationPreferences().save(" en-US ", None)

        assert settings.setValue.call_args_list == [
            call("last_language", "en-US"),
            call("last_political_view", ""),
        ]
        settings.sync.assert_called_once_with()


@unittest.skipUnless(
    HAS_QGIS and isinstance(QApplication.instance(), QApplication),
    "A running QGIS application is required",
)
class TestMapsLocalizationPreferences(unittest.TestCase):
    """Covers preference restoration and successful Maps saves."""

    def _dialog(self, saved_values=("", "")):
        preferences = Mock()
        preferences.load.return_value = saved_values
        maps = Mock()
        with (
            patch.object(maps_module, "load_style"),
            patch.object(
                maps_module,
                "LocalizationPreferences",
                return_value=preferences,
            ),
            patch.object(maps_module, "MapsFunctions", return_value=maps),
        ):
            dialog = MapsUi()
        self.addCleanup(dialog.deleteLater)
        return dialog, preferences, maps

    def test_restores_supported_language_and_political_view(self):
        dialog, _, _ = self._dialog(("ja", "IND"))

        assert dialog.language_comboBox.currentText() == "ja"
        assert dialog.political_view_comboBox.currentData() == "IND"

    def test_unknown_shared_language_uses_default_without_changing_preferences(self):
        dialog, preferences, _ = self._dialog(("en-US", "IND"))

        assert dialog.language_comboBox.currentText() == "Default"
        assert dialog.language_comboBox.findText("en-US") == -1
        assert dialog.political_view_comboBox.currentData() == "IND"
        preferences.save.assert_not_called()

    def test_style_round_trip_restores_localization_selection(self):
        dialog, _, _ = self._dialog()
        dialog.language_comboBox.setCurrentText("ja")
        political_index = dialog.political_view_comboBox.findData("IND")
        dialog.political_view_comboBox.setCurrentIndex(political_index)

        dialog.style_comboBox.setCurrentText("Satellite")

        assert not dialog.language_comboBox.isEnabled()
        assert dialog.language_comboBox.currentText() == "Default"
        assert not dialog.political_view_comboBox.isEnabled()
        assert dialog.political_view_comboBox.currentData() == ""

        dialog.style_comboBox.setCurrentText("Standard")

        assert dialog.language_comboBox.isEnabled()
        assert dialog.language_comboBox.currentText() == "ja"
        assert dialog.political_view_comboBox.isEnabled()
        assert dialog.political_view_comboBox.currentData() == "IND"

    def test_saves_after_successful_map_addition(self):
        dialog, preferences, maps = self._dialog()
        dialog.language_comboBox.setCurrentText("ja")
        political_index = dialog.political_view_comboBox.findData("IND")
        dialog.political_view_comboBox.setCurrentIndex(political_index)

        with patch.object(maps_module, "push_message"):
            dialog._add()

        maps.add_xyz_tile_layer.assert_called_once()
        preferences.save.assert_called_once_with("ja", "IND")

    def test_does_not_save_when_map_addition_fails(self):
        dialog, preferences, maps = self._dialog()
        maps.add_xyz_tile_layer.side_effect = RuntimeError("failed")

        with patch.object(maps_module, "show_error"):
            dialog._add()

        preferences.save.assert_not_called()

    def test_disabled_satellite_options_do_not_clear_shared_values(self):
        dialog, preferences, _ = self._dialog(("ja", "IND"))
        dialog.style_comboBox.setCurrentText("Satellite")

        with patch.object(maps_module, "push_message"):
            dialog._add()

        preferences.save.assert_called_once_with("ja", "IND")

    def test_unknown_shared_language_survives_satellite_addition(self):
        dialog, preferences, _ = self._dialog(("en-US", "IND"))
        dialog.style_comboBox.setCurrentText("Satellite")

        with patch.object(maps_module, "push_message"):
            dialog._add()

        preferences.save.assert_called_once_with("en-US", "IND")

    def test_unknown_shared_language_survives_standard_addition(self):
        dialog, preferences, _ = self._dialog(("en-US", "IND"))

        with patch.object(maps_module, "push_message"):
            dialog._add()

        preferences.save.assert_called_once_with("en-US", "IND")

    def test_explicit_language_selection_replaces_unknown_shared_language(self):
        dialog, preferences, _ = self._dialog(("en-US", "IND"))
        dialog.language_comboBox.setCurrentText("ja")

        with patch.object(maps_module, "push_message"):
            dialog._add()

        preferences.save.assert_called_once_with("ja", "IND")

    def test_explicit_default_selection_clears_unknown_shared_language(self):
        dialog, preferences, _ = self._dialog(("en-US", "IND"))
        default_index = dialog.language_comboBox.findText("Default")
        dialog.language_comboBox.activated.emit(default_index)

        with patch.object(maps_module, "push_message"):
            dialog._add()

        preferences.save.assert_called_once_with("", "IND")


if __name__ == "__main__":
    unittest.main()
