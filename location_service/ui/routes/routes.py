import os

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QMessageBox
from qgis.utils import iface

from ...functions.routes import RoutesFunctions
from ...utils.click_handler import MapClickCoordinateUpdater


class RoutesUi(QDialog):
    """
    Manages route calculations and visualizations on a map in a QGIS project.
    """

    UI_PATH = os.path.join(os.path.dirname(__file__), "routes.ui")

    def __init__(self) -> None:
        """
        Initializes the Routes dialog, loads UI components, and populates
        the routes options.
        """
        super().__init__()
        self.ui = uic.loadUi(self.UI_PATH, self)
        self.canvas = iface.mapCanvas()
        self.st_button_click.clicked.connect(self._st_click)
        self.ed_button_click.clicked.connect(self._ed_click)
        self.button_search.clicked.connect(self._search)
        self.button_cancel.clicked.connect(self._cancel)
        self.routes_comboBox.addItem("CalculateRoutes")
        self.routes = RoutesFunctions()

    def _search(self) -> None:
        """
        Retrieves coordinates from the UI, performs a route calculation,
        and visualizes the result as a line layer on the map.
        """
        st_lon = self.st_lon_lineEdit.text()
        st_lat = self.st_lat_lineEdit.text()
        ed_lon = self.ed_lon_lineEdit.text()
        ed_lat = self.ed_lat_lineEdit.text()
        try:
            result = self.routes.calculate_routes(st_lon, st_lat, ed_lon, ed_lat)
            self.routes.add_line_layer(result)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to calculate routes: {e!r}")
        finally:
            self.close()

    def _cancel(self) -> None:
        """
        Closes the dialog without making changes.
        """
        self.close()

    def _st_click(self) -> None:
        """
        Sets a custom map tool to capture map clicks and populate
        the start coordinate fields.
        """
        self.MapClick = MapClickCoordinateUpdater(self.canvas, self.ui, "st_routes")
        self.canvas.setMapTool(self.MapClick)

    def _ed_click(self) -> None:
        """
        Sets a custom map tool to capture map clicks and populate
        the end coordinate fields.
        """
        self.MapClick = MapClickCoordinateUpdater(self.canvas, self.ui, "ed_routes")
        self.canvas.setMapTool(self.MapClick)
