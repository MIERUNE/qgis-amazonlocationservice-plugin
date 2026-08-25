import os
from typing import Optional

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QWidget
from qgis.utils import iface

from ...functions.routes import RoutesFunctions
from ...utils.click_handler import (
    MapClickCoordinateUpdater,
    parse_lonlat,
    release_map_tool,
)
from ...utils.configuration_handler import ConfigurationError
from ...utils.feedback import busy_operation, show_config_error, show_error
from ..style_loader import load_style


class RoutesUi(QDialog):
    """Route calculation dialog."""

    UI_PATH = os.path.join(os.path.dirname(__file__), "routes.ui")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Loads the dialog and connects its controls."""
        super().__init__(parent)
        uic.loadUi(self.UI_PATH, self)
        load_style(self)
        self.canvas = iface.mapCanvas()
        self.st_button_click.clicked.connect(self._st_click)
        self.ed_button_click.clicked.connect(self._ed_click)
        self.button_search.clicked.connect(self._search)
        self.button_cancel.clicked.connect(self._cancel)
        self.routes_comboBox.addItem("CalculateRoutes")
        self.routes = RoutesFunctions()
        self._start_map_click = None
        self._end_map_click = None
        self._cancelled = False

    def _search(self) -> None:
        """Calculates routes, adds them to the map, and stays open on errors."""
        start = parse_lonlat(self.st_lon_lineEdit.text(), self.st_lat_lineEdit.text())
        end = parse_lonlat(self.ed_lon_lineEdit.text(), self.ed_lat_lineEdit.text())
        if start is None or end is None:
            show_error(
                self,
                "Input Error",
                "Set valid start and end coordinates by clicking 'Get Location'.",
            )
            return
        self._cancelled = False
        try:
            with busy_operation(self.button_search, "Calculating…"):
                credentials = self.routes.configuration_handler.get_credentials()
                if self._cancelled:
                    return
                result = self.routes.calculate_routes(
                    start[0], start[1], end[0], end[1], credentials=credentials
                )
            if self._cancelled:
                return
            self.routes.add_line_layer(result)
        except ConfigurationError as e:
            if not self._cancelled:
                show_config_error(self, e)
            return
        except Exception as e:
            if not self._cancelled:
                show_error(self, "Error", f"Failed to calculate routes: {e}")
            return
        self.close()

    def _cancel(self) -> None:
        """Closes the dialog and cancels active work."""
        self.close()

    def _st_click(self) -> None:
        """Activates the map tool for choosing the route start."""
        if self._start_map_click is None:
            self._start_map_click = MapClickCoordinateUpdater(
                self.canvas, self.st_lon_lineEdit, self.st_lat_lineEdit
            )
        self._start_map_click.arm()

    def _ed_click(self) -> None:
        """Activates the map tool for choosing the route end."""
        if self._end_map_click is None:
            self._end_map_click = MapClickCoordinateUpdater(
                self.canvas, self.ed_lon_lineEdit, self.ed_lat_lineEdit
            )
        self._end_map_click.arm()

    def hideEvent(self, event) -> None:
        """Cancels active work when hidden, except during minimization."""
        super().hideEvent(event)
        if event.spontaneous():
            return
        self._cancelled = True
        self.routes.api_handler.abort()
        release_map_tool(self._start_map_click)
        release_map_tool(self._end_map_click)
