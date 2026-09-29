import os
from typing import Callable, ClassVar, Optional

from qgis.core import QgsApplication
from qgis.gui import QgisInterface
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QWidget

from .processing_provider.provider import LocationServiceProvider
from .ui.config.config import ConfigUi
from .ui.maps.maps import MapsUi
from .ui.places.places import PlacesUi
from .ui.routes.routes import RoutesUi
from .ui.terms.terms import TermsUi
from .utils.feedback import show_error

_TOOL_WINDOW = Qt.WindowType.Tool


class LocationService:
    """QGIS plugin entry point."""

    MAIN_NAME = "Amazon Location Service"

    COMPONENT_HELP: ClassVar[dict[str, str]] = {
        "config": "Set your AWS region and API key.",
        "maps": "Add an Amazon Location basemap.",
        "places": "Search, geocode, and reverse-geocode places.",
        "routes": "Routing, isolines, snap-to-roads, and a route matrix.",
        "terms": "Open the AWS Service Terms page.",
    }

    def __init__(self, iface: Optional[QgisInterface]) -> None:
        """
        Stores the interface and prepares empty plugin state.

        qgis_process passes ``iface=None`` and only calls ``initProcessing``,
        so nothing here may touch the QGIS window. The toolbar and dialogs
        are created in ``initGui``.
        """
        self.iface = iface
        self.plugin_directory = os.path.dirname(__file__)
        self.actions = []
        self.provider: Optional[LocationServiceProvider] = None
        self.main_window = None
        self.toolbar = None
        self.config = None
        self.maps = None
        self.places = None
        self.routes = None
        self.terms = TermsUi()

    def add_action(
        self,
        icon_path: str,
        text: str,
        callback: Callable,
        enabled_flag: bool = True,
        add_to_menu: bool = True,
        add_to_toolbar: bool = True,
        status_tip: Optional[str] = None,
        whats_this: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> QAction:
        """Creates an action and adds it to the requested QGIS locations."""
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)
        if status_tip is not None:
            action.setStatusTip(status_tip)
        if whats_this is not None:
            action.setWhatsThis(whats_this)
        if add_to_menu:
            self.iface.addPluginToMenu(self.MAIN_NAME, action)
        if add_to_toolbar:
            self.toolbar.addAction(action)
        self.actions.append(action)
        return action

    def initProcessing(self) -> None:
        """Registers the Processing provider once."""
        if self.provider is not None:
            return
        provider = LocationServiceProvider()
        # QGIS deletes a provider whose id is already registered, so keep
        # only one that was actually added.
        if QgsApplication.processingRegistry().addProvider(provider):
            self.provider = provider

    def initGui(self) -> None:
        """Creates the toolbar and dialogs and adds the plugin actions."""
        self.initProcessing()
        self.main_window = self.iface.mainWindow()
        self.toolbar = self.iface.addToolBar(self.MAIN_NAME)
        self.toolbar.setObjectName(self.MAIN_NAME)
        self.config = ConfigUi(self.main_window)
        self.maps = MapsUi(self.main_window)
        self.places = PlacesUi(self.main_window)
        self.routes = RoutesUi(self.main_window)
        self.config.settings_saved.connect(self._refresh_region_capabilities)
        # Point-picking dialogs stay above their parent QGIS window while the
        # map canvas remains interactive. Unlike WindowStaysOnTopHint, Tool
        # windows do not need to stay above unrelated applications.
        self.places.setWindowFlag(_TOOL_WINDOW, True)
        self.routes.setWindowFlag(_TOOL_WINDOW, True)
        for component in [self.config, self.maps, self.places, self.routes]:
            component.hide()
        for component_name, help_text in self.COMPONENT_HELP.items():
            icon_path = os.path.join(
                self.plugin_directory, f"ui/{component_name}/{component_name}.png"
            )
            action = self.add_action(
                icon_path=icon_path,
                text=component_name.capitalize(),
                callback=getattr(self, f"show_{component_name}"),
                status_tip=help_text,
                whats_this=help_text,
                parent=self.main_window,
            )
            action.setToolTip(f"{component_name.capitalize()} — {help_text}")

    def unload(self) -> None:
        """Removes the plugin actions, dialogs, toolbar and Processing provider."""
        for action in self.actions:
            self.iface.removePluginMenu(self.MAIN_NAME, action)
            self.toolbar.removeAction(action)
            action.deleteLater()
        self.actions.clear()
        for dialog in (self.config, self.maps, self.places, self.routes):
            if dialog is not None:
                dialog.close()
                dialog.deleteLater()
        self.config = self.maps = self.places = self.routes = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        if self.toolbar is not None:
            self.main_window.removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None

    @staticmethod
    def _present(dialog) -> None:
        """Shows and raises a reused dialog."""
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_config(self) -> None:
        """
        Reloads settings before showing a hidden configuration dialog.

        Reading encrypted auth storage may prompt for the QGIS master password.
        """
        if not self.config.isVisible():
            self.config.reload_settings()
        self._present(self.config)

    def show_maps(self) -> None:
        """Displays the maps dialog."""
        self._present(self.maps)

    def show_places(self) -> None:
        """Displays the places dialog."""
        self._present(self.places)

    def _refresh_region_capabilities(self) -> None:
        """Refreshes Places and Routes after region or API-key settings are saved."""
        self.places.refresh_region_capabilities()
        self.routes.refresh_region_capabilities()

    def show_routes(self) -> None:
        """Displays the routes dialog."""
        self._present(self.routes)

    def show_terms(self) -> None:
        """Opens the AWS Service Terms page or reports an error."""
        if not self.terms.open_service_terms_url():
            show_error(
                self.main_window,
                "Error",
                "Failed to open the AWS Service Terms page in the default browser.",
            )
