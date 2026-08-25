import os
from typing import Optional

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QComboBox, QDialog, QWidget

from ...functions.maps import MapsFunctions, MapsOptions
from ...utils.configuration_handler import ConfigurationError
from ...utils.feedback import (
    SUCCESS,
    WARNING,
    busy_operation,
    push_message,
    show_config_error,
    show_error,
)
from ...utils.localization_preferences import LocalizationPreferences
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
    """Adds configured Amazon Location basemaps to QGIS as XYZ tile layers."""

    UI_PATH = os.path.join(os.path.dirname(__file__), "maps.ui")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Loads the dialog, connects its controls, and populates map options."""
        super().__init__(parent)
        uic.loadUi(self.UI_PATH, self)
        self._apply_style()
        self.button_add.clicked.connect(self._add)
        self.button_cancel.clicked.connect(self._cancel)
        self.style_comboBox.currentTextChanged.connect(self._on_style_changed)
        self.maps = MapsFunctions()
        self.localization_preferences = LocalizationPreferences()
        self._last_language = "Default"
        self._last_political_view = ""
        self._language_uses_default_fallback = False
        self.language_comboBox.activated.connect(self._on_language_selected)
        self._populate_maps_options()
        self._restore_localization_preferences()
        self._on_style_changed(self.style_comboBox.currentText())

    def _apply_style(self) -> None:
        """Applies QSS locally across the scroll tree for QGIS 3 dark themes."""
        scroll_contents = self.scrollArea.widget()
        # QGIS 3's application theme can override an inherited stylesheet at
        # the QScrollArea boundary. Giving each descendant a local stylesheet
        # keeps the plugin theme authoritative on Qt 5 as well as Qt 6.
        load_style(
            self,
            self.scrollArea,
            self.scrollArea.viewport(),
            scroll_contents,
            *scroll_contents.findChildren(QWidget),
        )

    def showEvent(self, event) -> None:
        """Reapplies the Maps theme after QGIS finishes polishing the window."""
        super().showEvent(event)
        self._apply_style()
        if event.spontaneous():
            return
        self._restore_localization_preferences()
        self._on_style_changed(self.style_comboBox.currentText())

    def _populate_maps_options(self) -> None:
        """Populates map options other than terrain, which depends on the style."""
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

    def _restore_localization_preferences(self) -> None:
        """Restores the language and political view last used by either dialog."""
        language, political_view = self.localization_preferences.load()

        language_text = language or "Default"
        language_index = self.language_comboBox.findText(language_text)
        self._language_uses_default_fallback = bool(language) and language_index < 0
        if language_index < 0:
            language_index = self.language_comboBox.findText("Default")
        self.language_comboBox.setCurrentIndex(max(language_index, 0))

        political_index = self.political_view_comboBox.findData(political_view)
        self.political_view_comboBox.setCurrentIndex(max(political_index, 0))
        self._last_language = self.language_comboBox.currentText()
        self._last_political_view = self.political_view_comboBox.currentData() or ""

    def _on_language_selected(self, _index: int) -> None:
        """Marks the displayed language as an explicit user selection."""
        self._language_uses_default_fallback = False

    def _remember_localization_selection(self) -> None:
        """Remembers localization values before a style disables their controls."""
        if self.language_comboBox.isEnabled() and self.language_comboBox.count():
            self._last_language = self.language_comboBox.currentText()
        if (
            self.political_view_comboBox.isEnabled()
            and self.political_view_comboBox.count()
        ):
            self._last_political_view = self.political_view_comboBox.currentData() or ""

    def _restore_localization_selection(self) -> None:
        """Restores remembered values when the selected style supports them."""
        if self.language_comboBox.isEnabled():
            language_index = self.language_comboBox.findText(self._last_language)
            self.language_comboBox.setCurrentIndex(max(language_index, 0))
        if self.political_view_comboBox.isEnabled():
            political_index = self.political_view_comboBox.findData(
                self._last_political_view
            )
            self.political_view_comboBox.setCurrentIndex(max(political_index, 0))

    def _set_terrain_items(self, items: tuple[tuple[str, str], ...]) -> None:
        """Replaces terrain options, keeping the current value when possible."""
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
        """Returns whether Maps V2 and chiitiler both support a style feature."""
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
        """Enables a combo box when supported; otherwise resets and disables it."""
        enabled = self._is_feature_active(style, feature)
        combo.setEnabled(enabled)
        if enabled:
            combo.setToolTip("")
        else:
            combo.setCurrentIndex(0)
            combo.setToolTip(self._style_disabled_tooltip(style))

    def _on_style_changed(self, style: str) -> None:
        """Updates controls for the selected style and renderer capabilities."""
        self._remember_localization_selection()
        constraints = (
            (self.colorscheme_comboBox, "colorScheme"),
            (self.language_comboBox, "language"),
            (self.political_view_comboBox, "politicalView"),
            (self.contour_density_comboBox, "contourDensity"),
            (self.traffic_comboBox, "traffic"),
        )
        for combo, feature in constraints:
            self._apply_combobox_constraint(combo, style, feature)
        self._restore_localization_selection()

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
        """Returns the selected travel mode names."""
        modes: list[str] = []
        if self.transit_checkBox.isChecked():
            modes.append("Transit")
        if self.truck_checkBox.isChecked():
            modes.append("Truck")
        return modes

    def _add(self) -> None:
        """Adds the selected basemap to the project."""
        try:
            save_language = self.language_comboBox.isEnabled() and not (
                self._language_uses_default_fallback
                and self.language_comboBox.currentText() == "Default"
            )
            save_political_view = self.political_view_comboBox.isEnabled()
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
            with busy_operation(self.button_add, "Adding…"):
                self.maps.add_xyz_tile_layer(options)
            language, political_view = self.localization_preferences.load()
            if save_language:
                language = "" if options.language == "Default" else options.language
            if save_political_view:
                political_view = options.political_view
            self.localization_preferences.save(language, political_view)
            push_message(
                SUCCESS,
                f"Added the “{options.style} {options.color_scheme}” basemap.",
            )
            push_message(
                WARNING,
                "Note: the basemap layer's source URI contains your API key and "
                "is saved in plain text inside the QGIS project file.",
                duration=8,
            )
            self.close()
        except ConfigurationError as e:
            show_config_error(self, e)
        except Exception as e:
            show_error(self, "Error", f"Failed to add XYZ tile layer: {e}")

    def _cancel(self) -> None:
        """Closes the dialog without adding a basemap."""
        self.close()
