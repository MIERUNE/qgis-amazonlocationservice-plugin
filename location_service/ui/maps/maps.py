import os

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QMessageBox

from ...functions.maps import MapsFunctions
from ...utils.configuration_handler import ConfigurationHandler


class MapsUi(QDialog):
    """
    A dialog for managing maps configurations and adding vector tile layers to a
    QGIS project.
    """

    UI_PATH = os.path.join(os.path.dirname(__file__), "maps.ui")
    MAP_STYLES = ("Standard", "Monochrome", "Hybrid", "Satellite")

    def __init__(self) -> None:
        """
        Initializes the Maps dialog, loads UI components, and populates the maps options.
        """
        super().__init__()
        self.ui = uic.loadUi(self.UI_PATH, self)
        self.button_add.clicked.connect(self._add)
        self.button_cancel.clicked.connect(self._cancel)
        self.maps = MapsFunctions()
        self.configuration_handler = ConfigurationHandler()
        self._populate_maps_options()

    def _populate_maps_options(self) -> None:
        """
        Populates the maps options dropdown with available configurations.
        """
        for style in self.MAP_STYLES:
            self.style_comboBox.addItem(style)

    def _add(self) -> None:
        """
        Adds the selected vector tile layer to the QGIS project and closes the dialog.
        """
        try:
            select_style = self.style_comboBox.currentText()
            self.maps.add_xyz_tile_layer(select_style)
            self.close()
        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Failed to add raster tile layer: {e!r}"
            )

    def _cancel(self) -> None:
        """
        Cancels the operation and closes the dialog without making changes.
        """
        self.close()
