import os
from typing import Callable, Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QWidget

from .ui.config.config import ConfigUi
from .ui.maps.maps import MapsUi
from .ui.places.places import PlacesUi
from .ui.routes.routes import RoutesUi
from .ui.terms.terms import TermsUi


class LocationService:
    """
    Manages the Amazon Location Service interface within a QGIS environment.
    """

    MAIN_NAME = "Amazon Location Service"

    def __init__(self, iface) -> None:
        """
        Initializes the plugin interface, setting up UI components
        and internal variables.

        Args:
            iface (QgsInterface): Reference to the QGIS app interface.
        """
        self.iface = iface
        self.main_window = self.iface.mainWindow()
        self.plugin_directory = os.path.dirname(__file__)
        self.actions = []
        self.toolbar = self.iface.addToolBar(self.MAIN_NAME)
        self.toolbar.setObjectName(self.MAIN_NAME)
        self.config = ConfigUi()
        self.maps = MapsUi()
        self.places = PlacesUi()
        self.routes = RoutesUi()
        self.terms = TermsUi()
        for component in [self.config, self.maps, self.places, self.routes]:
            component.hide()

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
        """
        Adds an action to the plugin menu and toolbar.

        Args:
            icon_path (str): Path to the icon.
            text (str): Display text.
            callback (Callable): Function to call on trigger.
            enabled_flag (bool): Is the action enabled by default.
            add_to_menu (bool): Should the action be added to the menu.
            add_to_toolbar (bool): Should the action be added to the toolbar.
            status_tip (Optional[str]): Text for status bar on hover.
            whats_this (Optional[str]): Longer description of the action.
            parent (Optional[QWidget]): Parent widget.

        Returns:
            QAction: The created action.
        """
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

    def initGui(self) -> None:
        """
        Initializes the GUI components, adding actions to the interface.
        """
        components = ["config", "maps", "places", "routes", "terms"]
        for component_name in components:
            icon_path = os.path.join(
                self.plugin_directory, f"ui/{component_name}/{component_name}.png"
            )
            self.add_action(
                icon_path=icon_path,
                text=component_name.capitalize(),
                callback=getattr(self, f"show_{component_name}"),
                parent=self.main_window,
            )

    def unload(self) -> None:
        """
        Cleans up the plugin interface by removing actions and toolbar.
        """
        for action in self.actions:
            self.iface.removePluginMenu(self.MAIN_NAME, action)
            self.iface.removeToolBarIcon(action)
        del self.toolbar

    def show_config(self) -> None:
        """
        Displays the configuration dialog window.
        """
        self.config.setWindowFlags(Qt.WindowStaysOnTopHint)  # type: ignore
        self.config.show()

    def show_maps(self) -> None:
        """
        Displays the maps dialog window.
        """
        self.maps.setWindowFlags(Qt.WindowStaysOnTopHint)  # type: ignore
        self.maps.show()

    def show_places(self) -> None:
        """
        Displays the places dialog window.
        """
        self.places.setWindowFlags(Qt.WindowStaysOnTopHint)  # type: ignore
        self.places.show()

    def show_routes(self) -> None:
        """
        Displays the routes dialog window.
        """
        self.routes.setWindowFlags(Qt.WindowStaysOnTopHint)  # type: ignore
        self.routes.show()

    def show_terms(self) -> None:
        """
        Opens the service terms URL in the default web browser.
        """
        self.terms.open_service_terms_url()
