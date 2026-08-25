import unittest
from unittest.mock import Mock, patch

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from qgis.PyQt.QtGui import QShowEvent
    from qgis.PyQt.QtWidgets import QApplication

    from location_service.ui.maps import maps as maps_module
    from location_service.ui.maps.maps import MapsUi

    class _SpontaneousShowEvent(QShowEvent):
        """A show event matching a window restored by the window manager."""

        def spontaneous(self):
            return True


@unittest.skipUnless(
    HAS_QGIS and isinstance(QApplication.instance(), QApplication),
    "A running QGIS application is required",
)
class TestMapsShowEvent(unittest.TestCase):
    """Covers preference restoration when the Maps dialog is shown."""

    def _dialog(self):
        preferences = Mock()
        preferences.load.return_value = ("en", "ARG")
        with (
            patch.object(maps_module, "load_style"),
            patch.object(maps_module, "MapsFunctions"),
            patch.object(
                maps_module,
                "LocalizationPreferences",
                return_value=preferences,
            ),
        ):
            dialog = MapsUi()
        self.addCleanup(dialog.deleteLater)
        return dialog, preferences

    @staticmethod
    def _select_unsaved_values(dialog):
        dialog.language_comboBox.setCurrentText("ja")
        political_index = dialog.political_view_comboBox.findData("IND")
        dialog.political_view_comboBox.setCurrentIndex(political_index)

    def test_spontaneous_show_preserves_unsaved_values(self):
        dialog, preferences = self._dialog()
        self._select_unsaved_values(dialog)
        preferences.load.reset_mock()
        dialog._apply_style = Mock()

        dialog.showEvent(_SpontaneousShowEvent())

        dialog._apply_style.assert_called_once_with()
        preferences.load.assert_not_called()
        assert dialog.language_comboBox.currentText() == "ja"
        assert dialog.political_view_comboBox.currentData() == "IND"

    def test_normal_show_restores_saved_values(self):
        dialog, preferences = self._dialog()
        self._select_unsaved_values(dialog)
        preferences.load.reset_mock()
        dialog._apply_style = Mock()

        dialog.showEvent(QShowEvent())

        dialog._apply_style.assert_called_once_with()
        preferences.load.assert_called_once_with()
        assert dialog.language_comboBox.currentText() == "en"
        assert dialog.political_view_comboBox.currentData() == "ARG"


if __name__ == "__main__":
    unittest.main()
