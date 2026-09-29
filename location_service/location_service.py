import os
from functools import partial
from typing import Callable, ClassVar, Optional

from qgis.core import QgsApplication
from qgis.gui import QgisInterface
from qgis.PyQt.QtGui import QCursor, QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu, QToolButton, QWidget

from .processing_provider.provider import (
    PLACES_ALGORITHMS,
    PROVIDER_ID,
    ROUTES_ALGORITHMS,
    LocationServiceProvider,
)
from .ui.config.config import ConfigUi
from .ui.maps.maps import MapsUi
from .ui.terms.terms import TermsUi
from .utils.feedback import show_error

_INSTANT_POPUP = QToolButton.ToolButtonPopupMode.InstantPopup


class LocationService:
    """QGIS plugin entry point."""

    MAIN_NAME = "Amazon Location Service"

    COMPONENT_HELP: ClassVar[dict[str, str]] = {
        "config": "Set your AWS region and API key.",
        "maps": "Add an Amazon Location basemap.",
        "places": "Processing tools to search, geocode, and reverse-geocode places.",
        "routes": (
            "Processing tools for routing, isolines, snap-to-roads, and a route matrix."
        ),
        "terms": "Open the AWS Service Terms page.",
    }

    def __init__(self, iface: QgisInterface) -> None:
        """Initializes the toolbar and plugin dialogs."""
        self.iface = iface
        self.main_window = self.iface.mainWindow()
        self.plugin_directory = os.path.dirname(__file__)
        self.actions = []
        self.toolbar = self.iface.addToolBar(self.MAIN_NAME)
        self.toolbar.setObjectName(self.MAIN_NAME)
        self.provider: Optional[LocationServiceProvider] = None
        self.algorithm_menus: list[QMenu] = []
        self.algorithm_dialogs: list[QWidget] = []
        self.config = ConfigUi(self.main_window)
        self.maps = MapsUi(self.main_window)
        self.terms = TermsUi()
        for component in [self.config, self.maps]:
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
        """Adds plugin actions to the QGIS menu and toolbar."""
        self.initProcessing()
        algorithm_groups = {
            "places": PLACES_ALGORITHMS,
            "routes": ROUTES_ALGORITHMS,
        }
        for component_name, help_text in self.COMPONENT_HELP.items():
            icon_path = os.path.join(
                self.plugin_directory, f"ui/{component_name}/{component_name}.png"
            )
            algorithms = algorithm_groups.get(component_name)
            menu = self._algorithm_menu(algorithms) if algorithms else None
            if menu is not None:
                callback = partial(self._popup_menu, menu)
            else:
                callback = getattr(self, f"show_{component_name}")
            action = self.add_action(
                icon_path=icon_path,
                text=component_name.capitalize(),
                callback=callback,
                status_tip=help_text,
                whats_this=help_text,
                parent=self.main_window,
            )
            action.setToolTip(f"{component_name.capitalize()} — {help_text}")
            if menu is not None:
                # The plugin menu shows it as a submenu and the toolbar button
                # opens it on click.
                action.setMenu(menu)
                button = self.toolbar.widgetForAction(action)
                if isinstance(button, QToolButton):
                    button.setPopupMode(_INSTANT_POPUP)

    @staticmethod
    def _popup_menu(menu: QMenu, *_args) -> None:
        """Opens an algorithm menu when its action is triggered directly."""
        menu.popup(QCursor.pos())

    def _algorithm_menu(self, algorithms) -> QMenu:
        """Returns a menu that opens each Processing algorithm dialog."""
        menu = QMenu(self.main_window)
        for algorithm_class in algorithms:
            algorithm = algorithm_class()
            algorithm_id = f"{PROVIDER_ID}:{algorithm.name()}"
            item = menu.addAction(algorithm.icon(), algorithm.displayName())
            item.setStatusTip(f"Open the {algorithm.displayName()} algorithm.")
            item.triggered.connect(partial(self._open_algorithm, algorithm_id))
        self.algorithm_menus.append(menu)
        return menu

    def _open_algorithm(self, algorithm_id: str, *_args) -> None:
        """Opens an algorithm from a menu item, ignoring the checked flag."""
        self.show_algorithm(algorithm_id)

    def show_algorithm(self, algorithm_id: str) -> None:
        """Opens a non-modal Processing dialog so points can be picked on the map."""
        try:
            import processing
        except ImportError:
            show_error(
                self.main_window,
                "Error",
                "The QGIS Processing plugin is required. Enable it in the Plugin "
                "Manager.",
            )
            return
        dialog = processing.createAlgorithmDialog(algorithm_id)
        if dialog is None:
            show_error(
                self.main_window,
                "Error",
                f"The Processing algorithm {algorithm_id} is not available.",
            )
            return
        self.algorithm_dialogs = [
            existing for existing in self.algorithm_dialogs if existing.isVisible()
        ]
        self.algorithm_dialogs.append(dialog)
        self._present(dialog)

    def unload(self) -> None:
        """Removes plugin actions and destroys its dialogs and toolbar."""
        for action in self.actions:
            self.iface.removePluginMenu(self.MAIN_NAME, action)
            self.toolbar.removeAction(action)
            action.deleteLater()
        self.actions.clear()
        for menu in self.algorithm_menus:
            menu.deleteLater()
        self.algorithm_menus.clear()
        for dialog in (*self.algorithm_dialogs, self.config, self.maps):
            dialog.close()
            dialog.deleteLater()
        self.algorithm_dialogs.clear()
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        self.main_window.removeToolBar(self.toolbar)
        self.toolbar.deleteLater()
        del self.toolbar

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

    def show_terms(self) -> None:
        """Opens the AWS Service Terms page or reports an error."""
        if not self.terms.open_service_terms_url():
            show_error(
                self.main_window,
                "Error",
                "Failed to open the AWS Service Terms page in the default browser.",
            )
