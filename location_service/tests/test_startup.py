import unittest
from unittest.mock import patch

from location_service.tests import HAS_QGIS

qgis_iface = None
if HAS_QGIS:
    from qgis.core import QgsApplication
    from qgis.PyQt import sip
    from qgis.PyQt.QtCore import QEvent, Qt
    from qgis.PyQt.QtWidgets import QApplication, QToolBar
    from qgis.utils import iface as qgis_iface

    from location_service import classFactory
    from location_service.location_service import LocationService
    from location_service.processing_provider.provider import PROVIDER_ID

    STAY_ON_TOP = Qt.WindowType.WindowStaysOnTopHint
    TOOL_WINDOW = Qt.WindowType.Tool
    WINDOW_TYPE_MASK = Qt.WindowType.WindowType_Mask
    DEFERRED_DELETE = QEvent.Type.DeferredDelete

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
        # The QGIS test runner may already have loaded the plugin itself.
        baseline_provider = QgsApplication.processingRegistry().providerById(
            PROVIDER_ID
        )
        try:
            plugin = classFactory(qgis_iface)
            toolbar = plugin.toolbar
            plugin.initGui()
            assert len(plugin.actions) == 5
            actions = list(plugin.actions)
            for dialog in (plugin.config, plugin.maps):
                assert dialog.parentWidget() is plugin.main_window
                assert not dialog.windowFlags() & STAY_ON_TOP
                assert dialog.windowFlags() & WINDOW_TYPE_MASK != TOOL_WINDOW

            registry = QgsApplication.processingRegistry()
            assert registry.providerById(PROVIDER_ID) is not None
            if baseline_provider is None:
                assert registry.providerById(PROVIDER_ID) is plugin.provider
            menus = {
                action.text(): [item.text() for item in action.menu().actions()]
                for action in actions
                if action.menu() is not None
            }
            assert menus == {
                "Places": [
                    "SearchText",
                    "Geocode",
                    "ReverseGeocode",
                    "SearchNearby",
                    "GetPlace (add place details)",
                ],
                "Routes": [
                    "CalculateRoutes",
                    "CalculateIsolines",
                    "SnapToRoads",
                    "CalculateRouteMatrix",
                ],
            }

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
        registry = QgsApplication.processingRegistry()
        assert registry.providerById(PROVIDER_ID) is baseline_provider

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

    def test_algorithm_menu_opens_a_processing_dialog(self):
        plugin = classFactory(qgis_iface)
        try:
            plugin.initGui()
            places = next(
                action for action in plugin.actions if action.text() == "Places"
            )
            places.menu().actions()[0].trigger()
            dialog = plugin.algorithm_dialogs[-1]
            assert dialog.isVisible()
            assert dialog.algorithm().id() == f"{PROVIDER_ID}:searchtext"
        finally:
            plugin.unload()
            self.process_deferred_deletes()
        assert plugin.algorithm_dialogs == []

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
