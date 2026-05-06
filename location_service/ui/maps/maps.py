import os

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QComboBox, QDialog, QMessageBox

from ...functions.maps import MapsFunctions, MapsOptions
from ...utils.configuration_handler import ConfigurationHandler
from ..style_loader import load_style
from .constants import (
    COLOR_SCHEMES,
    CONTOUR_DENSITIES,
    LANGUAGES,
    MAP_STYLES,
    POLITICAL_VIEWS,
    RENDERER_TOOLTIP_BUILDINGS,
    RENDERER_TOOLTIP_TERRAIN,
    RENDERER_UNSUPPORTED,
    STYLE_SUPPORT,
    STYLE_TOOLTIP_NOT_SUPPORTED,
    TRAFFIC_MODES,
)


class MapsUi(QDialog):
    """
    A dialog for managing maps configurations and adding XYZ tile (raster)
    layers to a QGIS project.
    """

    UI_PATH = os.path.join(os.path.dirname(__file__), "maps.ui")

    def __init__(self) -> None:
        """
        Initializes the Maps dialog, loads UI components, and populates the maps options.
        """
        super().__init__()
        self.ui = uic.loadUi(self.UI_PATH, self)
        load_style(self)
        self.button_add.clicked.connect(self._add)
        self.button_cancel.clicked.connect(self._cancel)
        self.style_comboBox.currentTextChanged.connect(self._on_style_changed)
        self.maps = MapsFunctions()
        self.configuration_handler = ConfigurationHandler()
        self._populate_maps_options()
        self._on_style_changed(self.style_comboBox.currentText())

    def _populate_maps_options(self) -> None:
        """
        Populates the maps options dropdown with available configurations.
        Terrain items are populated dynamically by ``_on_style_changed``.
        """
        for style in MAP_STYLES:
            self.style_comboBox.addItem(style)
        for scheme in COLOR_SCHEMES:
            self.colorscheme_comboBox.addItem(scheme)
        for lang in LANGUAGES:
            self.language_comboBox.addItem(lang)
        for label, code in POLITICAL_VIEWS:
            self.political_view_comboBox.addItem(label, code)
        for label, code in CONTOUR_DENSITIES:
            self.contour_density_comboBox.addItem(label, code)
        for label, code in TRAFFIC_MODES:
            self.traffic_comboBox.addItem(label, code)

    def _set_terrain_items(self, items: tuple[tuple[str, str], ...]) -> None:
        """
        Replaces the items in the terrain combo box while preserving the
        current selection if still valid.

        Args:
            items (tuple[tuple[str, str], ...]): The new set of (label, code) pairs.
        """
        current_code = (
            self.terrain_comboBox.currentData() if self.terrain_comboBox.count() else ""
        )
        self.terrain_comboBox.blockSignals(True)
        self.terrain_comboBox.clear()
        for label, code in items:
            self.terrain_comboBox.addItem(label, code)
        index = self.terrain_comboBox.findData(current_code)
        self.terrain_comboBox.setCurrentIndex(index if index >= 0 else 0)
        self.terrain_comboBox.blockSignals(False)

    def _is_feature_active(self, style: str, feature: str) -> bool:
        """
        Returns ``True`` when ``feature`` is both supported by the Amazon Location Service Maps V2
        GetStyleDescriptor matrix for ``style`` and renderable by the
        chiitiler pipeline.
        """
        if feature in RENDERER_UNSUPPORTED:
            return False
        return feature in STYLE_SUPPORT.get(style, ())

    def _style_disabled_tooltip(self, style: str) -> str:
        """Returns the tooltip shown when an option is disabled by the current style."""
        return STYLE_TOOLTIP_NOT_SUPPORTED.format(style=style)

    def _apply_combobox_constraint(
        self,
        combo: QComboBox,
        style: str,
        feature: str,
    ) -> None:
        """Toggle a combobox based on style support, resetting it when disabled."""
        enabled = self._is_feature_active(style, feature)
        combo.setEnabled(enabled)
        if enabled:
            combo.setToolTip("")
        else:
            combo.setCurrentIndex(0)
            combo.setToolTip(self._style_disabled_tooltip(style))

    def _on_style_changed(self, style: str) -> None:
        """
        Updates UI constraints based on the selected map style following the
        Amazon Location Service Maps V2 GetStyleDescriptor matrix combined with
        the chiitiler renderer limitations.

        Args:
            style (str): The newly selected style name.
        """
        self._apply_combobox_constraint(
            self.colorscheme_comboBox,
            style,
            "colorScheme",
        )

        self._apply_combobox_constraint(
            self.language_comboBox,
            style,
            "language",
        )
        self._apply_combobox_constraint(
            self.political_view_comboBox,
            style,
            "politicalView",
        )
        self._apply_combobox_constraint(
            self.contour_density_comboBox,
            style,
            "contourDensity",
        )
        self._apply_combobox_constraint(
            self.traffic_comboBox,
            style,
            "traffic",
        )

        travel_modes_enabled = self._is_feature_active(style, "travelModes")
        self.transit_checkBox.setEnabled(travel_modes_enabled)
        self.truck_checkBox.setEnabled(travel_modes_enabled)
        if travel_modes_enabled:
            self.transit_checkBox.setToolTip("")
            self.truck_checkBox.setToolTip("")
        else:
            self.transit_checkBox.setChecked(False)
            self.truck_checkBox.setChecked(False)
            disabled_tip = self._style_disabled_tooltip(style)
            self.transit_checkBox.setToolTip(disabled_tip)
            self.truck_checkBox.setToolTip(disabled_tip)

        self.buildings3d_checkBox.setEnabled(False)
        self.buildings3d_checkBox.setChecked(False)
        self.buildings3d_checkBox.setToolTip(RENDERER_TOOLTIP_BUILDINGS)

        terrain_items: list[tuple[str, str]] = [("None", "")]
        if self._is_feature_active(style, "terrain_hillshade"):
            terrain_items.append(("Hillshade", "Hillshade"))
        self._set_terrain_items(tuple(terrain_items))
        self.terrain_comboBox.setEnabled(len(terrain_items) > 1)
        self.terrain_comboBox.setToolTip(RENDERER_TOOLTIP_TERRAIN)

    def _selected_travel_modes(self) -> list[str]:
        """
        Collects the checked travel modes.

        Returns:
            list[str]: The selected travel mode names.
        """
        modes: list[str] = []
        if self.transit_checkBox.isChecked():
            modes.append("Transit")
        if self.truck_checkBox.isChecked():
            modes.append("Truck")
        return modes

    def _add(self) -> None:
        """
        Adds the selected XYZ tile (raster) layer to the QGIS project and closes the dialog.
        """
        try:
            options = MapsOptions(
                style=self.style_comboBox.currentText(),
                color_scheme=self.colorscheme_comboBox.currentText(),
                language=self.language_comboBox.currentText(),
                political_view=self.political_view_comboBox.currentData() or "",
                terrain=self.terrain_comboBox.currentData() or "",
                contour_density=self.contour_density_comboBox.currentData() or "",
                traffic=self.traffic_comboBox.currentData() or "",
                travel_modes=self._selected_travel_modes(),
                buildings_3d=self.buildings3d_checkBox.isChecked(),
            )
            self.maps.add_xyz_tile_layer(options)
            self.close()
        except KeyError as e:
            QMessageBox.warning(
                self,
                "Configuration Error",
                f"Required configuration is missing: {e}. "
                "Please check your API key and region in the settings.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to add XYZ tile layer: {e!r}")

    def _cancel(self) -> None:
        """
        Cancels the operation and closes the dialog without making changes.
        """
        self.close()
