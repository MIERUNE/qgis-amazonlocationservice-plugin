import unittest
from unittest.mock import patch

from location_service.tests import HAS_QGIS

qgis_iface = None
if HAS_QGIS:
    from qgis.PyQt import sip
    from qgis.PyQt.QtCore import QEvent, Qt
    from qgis.PyQt.QtGui import QPalette
    from qgis.PyQt.QtWidgets import QApplication, QToolBar
    from qgis.utils import iface as qgis_iface

    from location_service import classFactory
    from location_service.location_service import LocationService

    STAY_ON_TOP = Qt.WindowType.WindowStaysOnTopHint
    TOOL_WINDOW = Qt.WindowType.Tool
    WINDOW_TYPE_MASK = Qt.WindowType.WindowType_Mask
    DEFERRED_DELETE = QEvent.Type.DeferredDelete
    PALETTE_TEXT = QPalette.ColorRole.Text

HAS_IFACE = qgis_iface is not None


@unittest.skipUnless(HAS_IFACE, "QGIS test interface is required")
class TestPluginStartup(unittest.TestCase):
    """Covers plugin startup, UI error handling, and cleanup in QGIS."""

    @staticmethod
    def process_deferred_deletes():
        """Processes Qt objects queued by deleteLater()."""
        QApplication.sendPostedEvents(None, DEFERRED_DELETE)
        QApplication.processEvents()

    @staticmethod
    def location_service_toolbars():
        """Returns live custom toolbars created by this plugin."""
        return [
            toolbar
            for toolbar in qgis_iface.mainWindow().findChildren(QToolBar)
            if toolbar.objectName() == LocationService.MAIN_NAME
        ]

    def test_class_factory_init_gui_and_unload(self):
        plugin = None
        toolbar = None
        actions = []
        try:
            plugin = classFactory(qgis_iface)
            toolbar = plugin.toolbar
            plugin.initGui()
            assert len(plugin.actions) == 5
            actions = list(plugin.actions)
            normal_dialogs = (plugin.config, plugin.maps)
            tool_dialogs = (plugin.places, plugin.routes)
            for dialog in (*normal_dialogs, *tool_dialogs):
                assert dialog.parentWidget() is plugin.main_window
                assert not dialog.windowFlags() & STAY_ON_TOP
            for dialog in normal_dialogs:
                assert dialog.windowFlags() & WINDOW_TYPE_MASK != TOOL_WINDOW
            for dialog in tool_dialogs:
                assert dialog.windowFlags() & WINDOW_TYPE_MASK == TOOL_WINDOW

            maps_style = plugin.maps.styleSheet()
            assert maps_style
            styled_maps_widgets = (
                plugin.maps.scrollArea,
                plugin.maps.scrollArea.viewport(),
                plugin.maps.scrollAreaContents,
                plugin.maps.basic_groupBox,
                plugin.maps.style_comboBox,
                plugin.maps.transit_checkBox,
            )
            assert all(
                widget.styleSheet() == maps_style for widget in styled_maps_widgets
            )

            # QGIS may polish children again when a window is first shown.
            plugin.maps.basic_groupBox.setStyleSheet("")
            plugin.show_maps()
            QApplication.processEvents()
            assert plugin.maps.basic_groupBox.styleSheet() == maps_style
            plugin.maps.hide()
        finally:
            if plugin is not None:
                plugin.unload()
            self.process_deferred_deletes()

        assert toolbar is not None
        assert sip.isdeleted(toolbar)
        assert all(sip.isdeleted(action) for action in actions)

    def test_reloading_does_not_accumulate_toolbars_or_actions(self):
        baseline = len(self.location_service_toolbars())

        for _ in range(2):
            plugin = classFactory(qgis_iface)
            toolbar = plugin.toolbar
            try:
                plugin.initGui()
                actions = list(plugin.actions)
                assert len(actions) == 5
                assert len(self.location_service_toolbars()) == baseline + 1
            finally:
                plugin.unload()
                self.process_deferred_deletes()

            assert sip.isdeleted(toolbar)
            assert all(sip.isdeleted(action) for action in actions)
            assert len(self.location_service_toolbars()) == baseline

    def test_saved_config_refreshes_open_places_region_capabilities(self):
        plugin = classFactory(qgis_iface)
        try:
            plugin.show_places()
            QApplication.processEvents()

            with patch.object(
                plugin.places, "_configured_region", return_value="ap-southeast-1"
            ):
                plugin.config.settings_saved.emit()
            assert not plugin.places.button_enrich.isEnabled()

            with patch.object(
                plugin.places, "_configured_region", return_value="us-east-1"
            ):
                plugin.config.settings_saved.emit()
            assert plugin.places.button_enrich.isEnabled()
        finally:
            plugin.unload()
            self.process_deferred_deletes()

    def test_places_footer_fits_at_minimum_width(self):
        plugin = classFactory(qgis_iface)
        try:
            dialog = plugin.places
            dialog.resize(dialog.minimumWidth(), dialog.height())
            plugin.show_places()
            QApplication.processEvents()

            top_buttons = (dialog.button_enrich, dialog.button_load_more)
            bottom_buttons = (dialog.button_search, dialog.button_cancel)
            top_positions = [
                button.mapTo(dialog, button.rect().topLeft()) for button in top_buttons
            ]
            bottom_positions = [
                button.mapTo(dialog, button.rect().topLeft())
                for button in bottom_buttons
            ]

            assert dialog.width() == dialog.minimumWidth() == 500
            content_width = dialog.scrollArea.widget().width()
            viewport_width = dialog.scrollArea.viewport().width()
            assert content_width <= viewport_width, (
                f"Places content is {content_width}px wide but its viewport is "
                f"only {viewport_width}px wide."
            )
            top_bottom = max(
                position.y() + button.height()
                for position, button in zip(top_positions, top_buttons)
            )
            assert top_bottom <= min(position.y() for position in bottom_positions)
            for button in (*top_buttons, *bottom_buttons):
                position = button.mapTo(dialog, button.rect().topLeft())
                assert position.x() >= 0
                assert position.x() + button.width() <= dialog.width()
                assert button.width() >= button.sizeHint().width()
        finally:
            plugin.unload()
            self.process_deferred_deletes()

    def test_places_language_popup_uses_dark_text(self):
        plugin = classFactory(qgis_iface)
        try:
            plugin.show_places()
            dialog = plugin.places
            dialog.language_comboBox.showPopup()
            QApplication.processEvents()

            text_color = dialog.language_comboBox.view().palette().color(PALETTE_TEXT)
            assert text_color.name().lower() == "#16191f"
        finally:
            plugin.places.language_comboBox.hidePopup()
            plugin.unload()
            self.process_deferred_deletes()

    def test_terms_browser_failure_shows_error(self):
        plugin = classFactory(qgis_iface)
        try:
            with (
                patch(
                    "location_service.ui.terms.terms.QDesktopServices.openUrl",
                    return_value=False,
                ),
                patch("location_service.location_service.show_error") as show_error,
            ):
                plugin.show_terms()

            show_error.assert_called_once_with(
                plugin.main_window,
                "Error",
                "Failed to open the AWS Service Terms page in the default browser.",
            )
        finally:
            plugin.unload()
            self.process_deferred_deletes()


if __name__ == "__main__":
    unittest.main()
