import os

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QMessageBox
from qgis.utils import iface

from ...functions.places import PlacesFunctions
from ...utils.click_handler import MapClickCoordinateUpdater


class PlacesUi(QDialog):
    """
    Manages places search and visualization in a QGIS project.
    """

    UI_PATH = os.path.join(os.path.dirname(__file__), "places.ui")

    def __init__(self) -> None:
        """
        Initializes the Places dialog, loads UI components, and populates
        the places options.
        """
        super().__init__()
        self.ui = uic.loadUi(self.UI_PATH, self)
        self.canvas = iface.mapCanvas()
        self.button_click.clicked.connect(self._click)
        self.button_search.clicked.connect(self._search)
        self.button_cancel.clicked.connect(self._cancel)
        self.places_comboBox.addItem("SearchText")
        self.places = PlacesFunctions()

    def _search(self) -> None:
        """
        Performs a places search and visualizes the results on the map.
        """
        text = self.text_lineEdit.text()
        lon = self.lon_lineEdit.text()
        lat = self.lat_lineEdit.text()
        if not text or not lon or not lat:
            QMessageBox.critical(
                self,
                "Input Error",
                "All fields (Text, Longitude, Latitude) must be filled in.",
            )
            return
        try:
            result = self.places.search_text(text, lon, lat)
            self.places.add_point_layer(result)
        except Exception as e:
            QMessageBox.critical(
                self, "Search Error", f"Failed to search places: {e!r}"
            )
        finally:
            self.close()

    def _cancel(self) -> None:
        """
        Closes the dialog without making changes.
        """
        self.close()

    def _click(self) -> None:
        """
        Sets a custom map tool to capture map clicks and populate coordinates.
        """
        self.MapClick = MapClickCoordinateUpdater(self.canvas, self.ui, "places")
        self.canvas.setMapTool(self.MapClick)
