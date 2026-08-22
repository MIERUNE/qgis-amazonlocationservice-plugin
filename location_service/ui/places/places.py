import os
from typing import Optional

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QWidget
from qgis.utils import iface

from ...functions.places import PlacesFunctions
from ...utils.click_handler import (
    MapClickCoordinateUpdater,
    parse_lonlat,
    release_map_tool,
)
from ...utils.configuration_handler import ConfigurationError
from ...utils.feedback import busy_operation, show_config_error, show_error
from ..style_loader import load_style


class PlacesUi(QDialog):
    """Places search dialog."""

    UI_PATH = os.path.join(os.path.dirname(__file__), "places.ui")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Loads the dialog and connects its controls."""
        super().__init__(parent)
        uic.loadUi(self.UI_PATH, self)
        load_style(self)
        self.canvas = iface.mapCanvas()
        self.button_click.clicked.connect(self._click)
        self.button_search.clicked.connect(self._search)
        self.button_cancel.clicked.connect(self._cancel)
        self.places_comboBox.addItem("SearchText")
        self.places = PlacesFunctions()
        self._map_click = None
        self._cancelled = False

    def _search(self) -> None:
        """Searches for places, adds results to the map, and stays open on errors."""
        text = self.text_lineEdit.text().strip()
        position = parse_lonlat(self.lon_lineEdit.text(), self.lat_lineEdit.text())
        if not text or position is None:
            show_error(
                self,
                "Input Error",
                "Enter a search text and set valid coordinates by clicking "
                "'Get Location'.",
            )
            return
        self._cancelled = False
        try:
            with busy_operation(self.button_search, "Searching…"):
                result = self.places.search_text(text, position[0], position[1])
            if self._cancelled:
                return
            self.places.add_point_layer(result)
        except ConfigurationError as e:
            if not self._cancelled:
                show_config_error(self, e)
            return
        except Exception as e:
            if not self._cancelled:
                show_error(self, "Search Error", f"Failed to search places: {e}")
            return
        self.close()

    def _cancel(self) -> None:
        """Closes the dialog and cancels active work."""
        self.close()

    def _click(self) -> None:
        """Activates the map tool for choosing search coordinates."""
        if self._map_click is None:
            self._map_click = MapClickCoordinateUpdater(
                self.canvas, self.lon_lineEdit, self.lat_lineEdit
            )
        self._map_click.arm()

    def hideEvent(self, event) -> None:
        """Cancels active work when hidden, except during minimization."""
        super().hideEvent(event)
        if event.spontaneous():
            return
        self._cancelled = True
        self.places.api_handler.abort()
        release_map_tool(self._map_click)
