import html
import math
import os
from typing import Optional
from urllib.parse import urlparse

from qgis.core import (
    NULL,
    Qgis,
    QgsCsException,
    QgsMapLayerProxyModel,
    QgsMessageLog,
    QgsPointXY,
    QgsVectorLayer,
)
from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox
from qgis.PyQt import sip, uic
from qgis.PyQt.QtCore import QDateTime, QModelIndex, Qt
from qgis.PyQt.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QWidget,
)
from qgis.utils import iface

from ...functions import routes_capabilities, routes_layers, routes_results
from ...functions.routes import RoutesFunctions
from ...functions.routes_requests import (
    MAX_MATRIX_CELLS,
    MAX_TRACE_POINTS,
    MAX_WAYPOINTS,
    OPTIMIZE_FOR,
    ROUTES_TRAVEL_MODES,
    TRANSIT_LIKE_MODES,
    TRANSIT_MODE_VALUES,
    TRAVEL_MODES,
    IsolineOptions,
    MatrixOptions,
    RouteOptions,
    SnapOptions,
    TracePoint,
    build_isolines_body,
    build_matrix_body,
    build_routes_body,
    build_snap_body,
    haversine_meters,
)
from ...utils.click_handler import (
    MapClickCoordinateUpdater,
    MultiPointCollector,
    parse_lonlat,
    release_map_tool,
    wgs84_transform,
)
from ...utils.configuration_handler import ConfigurationError
from ...utils.external_api_handler import ApiError
from ...utils.feedback import (
    INFO,
    SUCCESS,
    WARNING,
    busy_operation,
    push_message,
    show_config_error,
    show_error,
)
from ..style_loader import load_style
from . import constants

# QGIS exposes the layer filter enum in different places on Qt 5 and Qt 6.
_POINT_LAYER_FILTER = getattr(
    QgsMapLayerProxyModel, "Filter", QgsMapLayerProxyModel
).PointLayer

# QSizePolicy enums are scoped on Qt 6 and unscoped on Qt 5.
_SIZE_POLICY = getattr(QSizePolicy, "Policy", QSizePolicy)

PERMISSION_HINT = (
    "The request was rejected (HTTP 403). Check that the API key allows the "
    "actions geo-routes:CalculateRoutes, geo-routes:CalculateIsolines, "
    "geo-routes:SnapToRoads and geo-routes:CalculateRouteMatrix (or "
    "geo-routes:*) on arn:aws:geo-routes:Region::provider/default, and check "
    "the configured region, the key's expiry, and any client restrictions "
    "on the key."
)

# Request bodies are built by these pure functions; building one early
# surfaces the count limits before the billing confirmation dialog.
_BODY_BUILDERS = {
    "CalculateRoutes": build_routes_body,
    "CalculateIsolines": build_isolines_body,
    "SnapToRoads": build_snap_body,
    "CalculateRouteMatrix": build_matrix_body,
}


class RoutesUi(QDialog):
    """Routes dialog for the four Routes V2 operations."""

    UI_PATH = os.path.join(os.path.dirname(__file__), "routes.ui")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Loads the dialog, connects its controls, and populates the options."""
        super().__init__(parent)
        uic.loadUi(self.UI_PATH, self)
        self._apply_style()
        self.canvas = iface.mapCanvas()
        self.routes = RoutesFunctions()
        self._start_map_click = None
        self._end_map_click = None
        self._iso_map_click = None
        self._waypoint_collector = None
        self._waypoints: list[tuple[float, float]] = []
        self._cancelled = False
        self._busy = False

        self.time_dateTimeEdit.setDateTime(QDateTime.currentDateTime())
        self._populate_routes_options()
        self._apply_static_tooltips()
        self._build_layer_pickers()
        self._connect_signals()
        self._apply_region_capabilities()
        self._on_function_changed(self.routes_comboBox.currentText())
        self._on_travel_mode_changed()
        self._on_threshold_type_changed()
        self._on_transit_filter_changed()

    def _apply_style(self) -> None:
        """Applies QSS locally across the scroll tree for QGIS 3 dark themes."""
        scroll_contents = self.scrollArea.widget()
        # QGIS 3 can override inherited QSS at the scroll-area boundary, so
        # descendants receive the plugin style too. Embedded editors are
        # skipped to avoid applying QLineEdit padding inside their parent.
        children = [
            child
            for child in scroll_contents.findChildren(QWidget)
            if not isinstance(child.parentWidget(), (QAbstractSpinBox, QComboBox))
        ]
        load_style(
            self,
            self.scrollArea,
            self.scrollArea.viewport(),
            scroll_contents,
            *children,
        )

    def showEvent(self, event) -> None:
        """Reapplies the theme after QGIS finishes polishing the window."""
        super().showEvent(event)
        self._apply_style()
        if event.spontaneous():
            return
        self._apply_region_capabilities()

    def _populate_routes_options(self) -> None:
        """Populates the function selector and the option combo boxes."""
        for function in constants.FUNCTIONS:
            self.routes_comboBox.addItem(function)
        for mode in ROUTES_TRAVEL_MODES:
            self.travelmode_comboBox.addItem(mode)
        for combo in (
            self.iso_travelmode_comboBox,
            self.snap_travelmode_comboBox,
            self.mx_travelmode_comboBox,
        ):
            for mode in TRAVEL_MODES:
                combo.addItem(mode)
        for value in OPTIMIZE_FOR:
            self.optimizefor_comboBox.addItem(value)
        for label, value in constants.ISOLINE_DIRECTIONS:
            self.iso_direction_comboBox.addItem(label, value)
        for label, value in constants.THRESHOLD_TYPES:
            self.iso_threshold_type_comboBox.addItem(label, value)
        for label, value in constants.TRANSIT_FILTERS:
            self.transit_filter_comboBox.addItem(label, value)
        self._populate_transit_modes()

    def _apply_static_tooltips(self) -> None:
        """Explains static request behavior and input formats in tooltips."""
        self.time_groupBox.setToolTip(
            "Live and dynamic traffic is only reflected when a time is "
            "specified. For Transit and Intermodal the time also selects "
            "the timetable."
        )
        self.transit_groupBox.setToolTip(
            "Transit only. Intermodal uses the AWS default leg composition."
        )
        self.snap_timestamp_label.setToolTip(
            "ISO 8601 with a timezone offset, such as 2026-08-26T09:00:00+09:00."
        )
        self.snap_heading_label.setToolTip("GPS heading from 0 to 360 degrees.")
        self.snap_speed_label.setToolTip("Speed in kilometers per hour; zero or more.")
        self.mx_odlines_checkBox.setToolTip(
            "Straight lines in EPSG:4326, not road geometry. A pair on "
            "opposite sides of the antimeridian is drawn the long way around."
        )

    def _populate_transit_modes(self) -> None:
        """Fills the checkable transit mode list with the concrete modes."""
        for mode in TRANSIT_MODE_VALUES:
            # "All" is expressed by leaving the filter on its default.
            if mode == "All":
                continue
            item = QListWidgetItem(mode, self.transit_modes_listWidget)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)

    def _build_layer_pickers(self) -> None:
        """Creates the QGIS layer and field pickers inside their containers."""
        self.snap_layer_comboBox = self._layer_picker(self.snap_layer_container)
        self.snap_id_comboBox = self._field_picker(self.snap_id_container)
        self.snap_order_comboBox = self._field_picker(self.snap_order_container)
        self.snap_timestamp_comboBox = self._field_picker(self.snap_timestamp_container)
        self.snap_heading_comboBox = self._field_picker(self.snap_heading_container)
        self.snap_speed_comboBox = self._field_picker(self.snap_speed_container)
        self._bind_layer_fields(
            self.snap_layer_comboBox,
            (
                self.snap_id_comboBox,
                self.snap_order_comboBox,
                self.snap_timestamp_comboBox,
                self.snap_heading_comboBox,
                self.snap_speed_comboBox,
            ),
        )

        self.mx_origins_comboBox = self._layer_picker(self.mx_origins_container)
        self.mx_origins_id_comboBox = self._field_picker(self.mx_origins_id_container)
        self.mx_origins_order_comboBox = self._field_picker(
            self.mx_origins_order_container
        )
        self._bind_layer_fields(
            self.mx_origins_comboBox,
            (self.mx_origins_id_comboBox, self.mx_origins_order_comboBox),
        )

        self.mx_dest_comboBox = self._layer_picker(self.mx_dest_container)
        self.mx_dest_id_comboBox = self._field_picker(self.mx_dest_id_container)
        self.mx_dest_order_comboBox = self._field_picker(self.mx_dest_order_container)
        self._bind_layer_fields(
            self.mx_dest_comboBox,
            (self.mx_dest_id_comboBox, self.mx_dest_order_comboBox),
        )

    @staticmethod
    def _layer_picker(container: QWidget) -> QgsMapLayerComboBox:
        """Adds a point-layer picker to a container widget."""
        combo = QgsMapLayerComboBox(container)
        combo.setFilters(_POINT_LAYER_FILTER)
        container.layout().addWidget(combo)
        return combo

    @staticmethod
    def _field_picker(container: QWidget) -> QgsFieldComboBox:
        """Adds an optional field picker to a container widget."""
        combo = QgsFieldComboBox(container)
        combo.setAllowEmptyFieldName(True)
        container.layout().addWidget(combo)
        return combo

    @staticmethod
    def _bind_layer_fields(layer_combo, field_combos) -> None:
        """Keeps the field pickers in sync with the selected layer."""
        for field_combo in field_combos:
            layer_combo.layerChanged.connect(field_combo.setLayer)
            field_combo.setLayer(layer_combo.currentLayer())

    def _connect_signals(self) -> None:
        """Connects the dialog controls."""
        self.routes_comboBox.currentTextChanged.connect(self._on_function_changed)
        self.travelmode_comboBox.currentTextChanged.connect(
            self._on_travel_mode_changed
        )
        self.iso_threshold_type_comboBox.currentTextChanged.connect(
            self._on_threshold_type_changed
        )
        self.transit_filter_comboBox.currentTextChanged.connect(
            self._on_transit_filter_changed
        )
        for radio in (
            self.time_none_radioButton,
            self.time_departnow_radioButton,
            self.time_departure_radioButton,
            self.time_arrival_radioButton,
        ):
            radio.toggled.connect(self._on_time_choice_changed)
        self.st_button_click.clicked.connect(self._st_click)
        self.ed_button_click.clicked.connect(self._ed_click)
        self.iso_button_click.clicked.connect(self._iso_click)
        self.wp_button_click.clicked.connect(self._collect_waypoints)
        self.wp_button_undo.clicked.connect(self._undo_waypoint)
        self.wp_button_clear.clicked.connect(self._clear_waypoints)
        self.button_search.clicked.connect(self._run)
        self.button_cancel.clicked.connect(self._cancel)

    def _current_function(self) -> str:
        """Returns the currently selected function name."""
        return self.routes_comboBox.currentText()

    def _on_function_changed(self, function: str) -> None:
        """Shows the selected function page and resets its scroll position."""
        index = constants.FUNCTION_PAGE.get(function, 0)
        stack = self.params_stackedWidget
        stack.setCurrentIndex(index)
        for page_index in range(stack.count()):
            page = stack.widget(page_index)
            policy = page.sizePolicy()
            policy.setVerticalPolicy(
                _SIZE_POLICY.Preferred if page_index == index else _SIZE_POLICY.Ignored
            )
            page.setSizePolicy(policy)
        stack.updateGeometry()
        self.scrollArea.verticalScrollBar().setValue(0)
        self.button_search.setText(constants.RUN_BUTTON_LABEL.get(function, "Run"))
        # Release pickers before a later click can update a hidden page.
        release_map_tool(self._start_map_click)
        release_map_tool(self._end_map_click)
        release_map_tool(self._iso_map_click)
        release_map_tool(self._waypoint_collector)
        self._waypoints_finished()

    def _on_travel_mode_changed(self, *_args) -> None:
        """Enables the controls that apply to the selected travel mode."""
        transit_like = self.travelmode_comboBox.currentText() in TRANSIT_LIKE_MODES
        for widget in (
            self.optimizefor_comboBox,
            self.wp_button_click,
            self.wp_button_undo,
            self.wp_button_clear,
            self.waypoints_listWidget,
        ):
            widget.setEnabled(not transit_like)
            widget.setToolTip(
                constants.TRANSIT_DISABLED_TOOLTIP if transit_like else ""
            )
        self._sync_avoid_checkboxes(transit_like)
        if transit_like:
            self._clear_waypoints()
        self.transit_groupBox.setEnabled(
            self.travelmode_comboBox.currentText() == "Transit"
        )

    def _sync_avoid_checkboxes(self, transit_like: bool) -> None:
        """Syncs the Avoid checkboxes with the travel mode and the region."""
        allowed = routes_capabilities.allowed_avoid_keys(self._configured_region())
        for name, key in constants.AVOID_OPTIONS:
            checkbox = getattr(self, name)
            if transit_like:
                checkbox.setEnabled(False)
                checkbox.setChecked(False)
                checkbox.setToolTip(constants.TRANSIT_DISABLED_TOOLTIP)
            elif key not in allowed:
                checkbox.setEnabled(False)
                checkbox.setChecked(False)
                checkbox.setToolTip(constants.REGION_DISABLED_TOOLTIP)
            else:
                checkbox.setEnabled(True)
                checkbox.setToolTip("")

    def _on_time_choice_changed(self, *_args) -> None:
        """Enables the date-time editor only for an explicit time choice."""
        explicit = (
            self.time_departure_radioButton.isChecked()
            or self.time_arrival_radioButton.isChecked()
        )
        self.time_dateTimeEdit.setEnabled(explicit)

    def _on_threshold_type_changed(self, *_args) -> None:
        """Updates the threshold labels for the selected unit."""
        threshold_type = self.iso_threshold_type_comboBox.currentData() or "Time"
        self.iso_thresholds_label.setText(
            constants.THRESHOLD_UNIT_LABEL[threshold_type]
        )
        self.iso_thresholds_lineEdit.setPlaceholderText(
            constants.THRESHOLD_PLACEHOLDER[threshold_type]
        )

    def _on_transit_filter_changed(self, *_args) -> None:
        """Enables the transit mode list only when a filter is selected."""
        self.transit_modes_listWidget.setEnabled(
            bool(self.transit_filter_comboBox.currentData())
        )

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

    def _iso_click(self) -> None:
        """Activates the map tool for choosing the isoline center."""
        if self._iso_map_click is None:
            self._iso_map_click = MapClickCoordinateUpdater(
                self.canvas, self.iso_lon_lineEdit, self.iso_lat_lineEdit
            )
        self._iso_map_click.arm()

    def _collect_waypoints(self) -> None:
        """Starts collecting route waypoints; pressing again stops it."""
        if self._waypoint_collector is None:
            self._waypoint_collector = MultiPointCollector(self.canvas)
            self._waypoint_collector.point_collected.connect(self._add_waypoint)
            self._waypoint_collector.collection_finished.connect(
                self._waypoints_finished
            )
            # Another map tool can deactivate the collector without a click.
            self._waypoint_collector.deactivated.connect(self._waypoints_finished)
        if self.canvas.mapTool() is self._waypoint_collector:
            release_map_tool(self._waypoint_collector)
            self._waypoints_finished()
            return
        self.wp_button_click.setText(constants.WAYPOINT_BUTTON_ACTIVE)
        push_message(INFO, constants.WAYPOINT_HINT)
        self._waypoint_collector.arm()

    def _add_waypoint(self, point: QgsPointXY) -> None:
        """Appends one collected waypoint, up to the API limit."""
        if len(self._waypoints) >= MAX_WAYPOINTS:
            release_map_tool(self._waypoint_collector)
            self._waypoints_finished()
            push_message(INFO, f"Waypoints are limited to {MAX_WAYPOINTS} points.")
            return
        self._waypoints.append((point.x(), point.y()))
        self.waypoints_listWidget.addItem(f"{point.x():.6f}, {point.y():.6f}")

    def _waypoints_finished(self) -> None:
        """Restores the waypoint button after collecting ends."""
        self.wp_button_click.setText(constants.WAYPOINT_BUTTON_ADD)

    def _undo_waypoint(self) -> None:
        """Removes the most recently collected waypoint."""
        if not self._waypoints:
            return
        self._waypoints.pop()
        self.waypoints_listWidget.takeItem(self.waypoints_listWidget.count() - 1)

    def _clear_waypoints(self) -> None:
        """Removes every collected waypoint."""
        release_map_tool(self._waypoint_collector)
        self._waypoints_finished()
        self._waypoints.clear()
        self.waypoints_listWidget.clear()

    def _configured_region(self) -> str:
        """Returns the configured region without prompting for credentials."""
        handler = self.routes.configuration_handler
        return str(handler.get_setting(handler.KEY_REGION) or "").strip()

    def refresh_region_capabilities(self) -> None:
        """Cancels active work and refreshes controls after settings change."""
        if self._busy:
            self._cancelled = True
            self.routes.api_handler.abort()
        self._apply_region_capabilities()

    def _apply_region_capabilities(self) -> None:
        """Disables the operations and options the region does not support."""
        region = self._configured_region()
        operations = routes_capabilities.available_operations(region)
        for index in range(self.routes_comboBox.count()):
            function = self.routes_comboBox.itemText(index)
            item = self.routes_comboBox.model().item(index)
            if item is not None:
                item.setEnabled(function in operations)
        if self._current_function() not in operations:
            self.routes_comboBox.setCurrentText("CalculateRoutes")

        self._apply_travel_mode_capabilities(region)
        max_origins, max_destinations = routes_capabilities.matrix_axis_limits(region)
        matrix_limits = (
            f"AWS Unbounded axis limits: {max_origins} origins and "
            f"{max_destinations} destinations. The plugin allows at most "
            f"{MAX_MATRIX_CELLS} origin x destination pairs. This matches "
            "the AWS Unbounded limit "
            "in standard regions and is a stricter plugin safety limit in "
            "GrabMaps regions; every pair is one billed route calculation."
        )
        self.mx_origins_groupBox.setToolTip(matrix_limits)
        self.mx_dest_groupBox.setToolTip(matrix_limits)
        self.mx_options_groupBox.setToolTip(matrix_limits)
        self.alternatives_spinBox.setMaximum(
            routes_capabilities.max_alternatives(region)
        )
        arrival_allowed = routes_capabilities.arrival_time_allowed(region)
        self.time_arrival_radioButton.setEnabled(arrival_allowed)
        self.time_arrival_radioButton.setToolTip(
            "" if arrival_allowed else constants.REGION_DISABLED_TOOLTIP
        )
        if not arrival_allowed and self.time_arrival_radioButton.isChecked():
            self.time_none_radioButton.setChecked(True)
        self._on_travel_mode_changed()

    def _apply_travel_mode_capabilities(self, region: str) -> None:
        """Disables travel modes the region does not support."""
        combos = {
            self.travelmode_comboBox: "CalculateRoutes",
            self.iso_travelmode_comboBox: "CalculateIsolines",
            self.snap_travelmode_comboBox: "SnapToRoads",
            self.mx_travelmode_comboBox: "CalculateRouteMatrix",
        }
        for combo, operation in combos.items():
            allowed = routes_capabilities.allowed_travel_modes(region, operation)
            for index in range(combo.count()):
                item = combo.model().item(index)
                if item is not None:
                    item.setEnabled(combo.itemText(index) in allowed)
            if combo.currentText() not in allowed:
                combo.setCurrentText("Car")

    def _parse_position(
        self, lon_edit: QLineEdit, lat_edit: QLineEdit, name: str
    ) -> list:
        """Returns a required picked position, or raises ``ValueError``."""
        position = parse_lonlat(lon_edit.text(), lat_edit.text())
        if position is None:
            raise ValueError(f"Set the {name} by clicking 'Get Location' on the map.")
        return position

    def _selected_time(self) -> dict:
        """Returns the depart/arrival choice as RouteOptions keyword values."""
        if self.time_departnow_radioButton.isChecked():
            return {"depart_now": True}
        # The editor value is local time; send it with the system UTC offset.
        iso_time = (
            self.time_dateTimeEdit.dateTime()
            .toPyDateTime()
            .astimezone()
            .isoformat(timespec="seconds")
        )
        if self.time_departure_radioButton.isChecked():
            return {"departure_time": iso_time}
        if self.time_arrival_radioButton.isChecked():
            return {"arrival_time": iso_time}
        return {}

    def _checked_transit_modes(self) -> tuple:
        """Returns the checked transit modes from the structured list."""
        checked = []
        for index in range(self.transit_modes_listWidget.count()):
            item = self.transit_modes_listWidget.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                checked.append(item.text())
        return tuple(checked)

    def _transit_mode_options(self) -> dict:
        """Returns the allowed/excluded transit modes for RouteOptions."""
        if self.travelmode_comboBox.currentText() != "Transit":
            return {}
        transit_filter = self.transit_filter_comboBox.currentData()
        if not transit_filter:
            return {}
        checked = self._checked_transit_modes()
        if not checked:
            raise ValueError(
                "Check at least one transit mode, or switch the filter back "
                "to 'All modes allowed'."
            )
        if transit_filter == "allow":
            return {"transit_allowed_modes": checked}
        return {"transit_excluded_modes": checked}

    def _checked_avoid_keys(self) -> tuple:
        """Returns the API keys of the checked avoidance options."""
        return tuple(
            key
            for name, key in constants.AVOID_OPTIONS
            if getattr(self, name).isChecked()
        )

    def _parse_thresholds(self) -> tuple:
        """Returns the isoline thresholds converted to API units."""
        threshold_type = self.iso_threshold_type_comboBox.currentData() or "Time"
        factor = 60 if threshold_type == "Time" else 1000
        values = []
        for part in self.iso_thresholds_lineEdit.text().split(","):
            part = part.strip()
            if not part:
                continue
            try:
                number = float(part)
            except ValueError:
                raise ValueError(
                    f"Threshold values must be numbers: {part!r}."
                ) from None
            if not math.isfinite(number):
                raise ValueError(f"Threshold values must be finite numbers: {part!r}.")
            value = round(number * factor)
            if value < 1:
                raise ValueError(
                    f"Threshold value {part} is too small; it rounds to zero."
                )
            values.append(value)
        if not values:
            raise ValueError("Enter at least one threshold value (e.g. 5, 10, 15).")
        return tuple(values)

    def _build_request(self, function: str) -> dict:
        """Captures one complete request from the current inputs."""
        request: dict = {"function": function}
        if function == "CalculateRoutes":
            request["options"] = self._build_route_options()
            request["add_summary"] = self.rt_summary_checkBox.isChecked()
        elif function == "CalculateIsolines":
            request["options"] = IsolineOptions(
                center=tuple(
                    self._parse_position(
                        self.iso_lon_lineEdit, self.iso_lat_lineEdit, "center point"
                    )
                ),
                direction=self.iso_direction_comboBox.currentData() or "Origin",
                threshold_type=(
                    self.iso_threshold_type_comboBox.currentData() or "Time"
                ),
                thresholds=self._parse_thresholds(),
                travel_mode=self.iso_travelmode_comboBox.currentText(),
            )
        elif function == "SnapToRoads":
            request.update(self._build_snap_request())
        elif function == "CalculateRouteMatrix":
            request.update(self._build_matrix_request())
        else:
            raise ValueError(f"Unknown function: {function}")
        credentials = self.routes.configuration_handler.get_credentials()
        request["region"] = credentials[0]
        request["credentials"] = credentials
        return request

    def _build_route_options(self) -> RouteOptions:
        """Builds the CalculateRoutes options from the current inputs."""
        transit_like = self.travelmode_comboBox.currentText() in TRANSIT_LIKE_MODES
        return RouteOptions(
            origin=tuple(
                self._parse_position(
                    self.st_lon_lineEdit, self.st_lat_lineEdit, "starting point"
                )
            ),
            destination=tuple(
                self._parse_position(
                    self.ed_lon_lineEdit, self.ed_lat_lineEdit, "end point"
                )
            ),
            waypoints=() if transit_like else tuple(self._waypoints),
            travel_mode=self.travelmode_comboBox.currentText(),
            optimize_for=self.optimizefor_comboBox.currentText(),
            avoid=() if transit_like else self._checked_avoid_keys(),
            max_alternatives=self.alternatives_spinBox.value(),
            **self._selected_time(),
            **self._transit_mode_options(),
        )

    def _build_snap_request(self) -> dict:
        """Snapshots the trace layer input before the request is sent."""
        layer = self.snap_layer_comboBox.currentLayer()
        rows = self._layer_input_rows(
            layer,
            self.snap_selected_checkBox.isChecked(),
            self.snap_id_comboBox.currentField(),
            self.snap_order_comboBox.currentField(),
            max_rows=MAX_TRACE_POINTS,
            timestamp_field=self.snap_timestamp_comboBox.currentField(),
            heading_field=self.snap_heading_comboBox.currentField(),
            speed_field=self.snap_speed_comboBox.currentField(),
        )
        trace_points = tuple(
            TracePoint(
                position=tuple(row["position"]),
                timestamp=row.get("timestamp"),
                heading=row.get("heading"),
                speed=row.get("speed"),
            )
            for row in rows
        )
        return {
            "rows": rows,
            "options": SnapOptions(
                trace_points=trace_points,
                snap_radius=self.snap_radius_spinBox.value(),
                travel_mode=self.snap_travelmode_comboBox.currentText(),
            ),
            "output_confidence": self.snap_confidence_checkBox.isChecked(),
        }

    def _build_matrix_request(self) -> dict:
        """Snapshots the origin and destination layers before the request."""
        max_origins, max_destinations = routes_capabilities.matrix_axis_limits(
            self._configured_region()
        )
        origins = self._layer_input_rows(
            self.mx_origins_comboBox.currentLayer(),
            self.mx_origins_selected_checkBox.isChecked(),
            self.mx_origins_id_comboBox.currentField(),
            self.mx_origins_order_comboBox.currentField(),
            max_rows=min(max_origins, MAX_MATRIX_CELLS),
            name="Origins",
        )
        # Stop once another destination would exceed the billed-cell cap.
        destination_cap = min(
            max_destinations, MAX_MATRIX_CELLS // max(len(origins), 1)
        )
        destinations = self._layer_input_rows(
            self.mx_dest_comboBox.currentLayer(),
            self.mx_dest_selected_checkBox.isChecked(),
            self.mx_dest_id_comboBox.currentField(),
            self.mx_dest_order_comboBox.currentField(),
            max_rows=max(destination_cap, 1),
            name="Destinations",
        )
        return {
            "origins": origins,
            "destinations": destinations,
            "options": MatrixOptions(
                origins=tuple(tuple(row["position"]) for row in origins),
                destinations=tuple(tuple(row["position"]) for row in destinations),
                travel_mode=self.mx_travelmode_comboBox.currentText(),
            ),
            "od_lines": self.mx_odlines_checkBox.isChecked(),
        }

    def _layer_input_rows(
        self,
        layer,
        selected_only: bool,
        id_field: str,
        order_field: str,
        *,
        max_rows: int,
        timestamp_field: str = "",
        heading_field: str = "",
        speed_field: str = "",
        name: str = "Point layer",
    ) -> list:
        """
        Copies the layer's points into request rows before anything is sent.

        Multipoint features are expanded per part, and the scan stops as soon
        as ``max_rows`` is exceeded instead of reading a large layer to the
        end. Rows are ordered by the order field value (numbers before other
        values, NULL last), then by feature id, then by the part index. A
        missing or NULL id falls back to the feature id as a string.
        """
        if (
            not isinstance(layer, QgsVectorLayer)
            or sip.isdeleted(layer)
            or layer.geometryType() != Qgis.GeometryType.Point
        ):
            raise ValueError(f"{name}: select a point layer first.")
        if selected_only and layer.selectedFeatureCount() == 0:
            raise ValueError(f"{name}: no features are selected on the layer.")
        source_crs = layer.crs()
        if not source_crs.isValid():
            raise ValueError(f"{name}: the layer has no valid CRS.")
        features = layer.getSelectedFeatures() if selected_only else layer.getFeatures()
        transform = wgs84_transform(source_crs)
        if not transform.isValid():
            raise ValueError(f"{name}: the layer CRS cannot be transformed to WGS 84.")
        rows = []
        for feature in features:
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            for part_index, point in enumerate(geometry.vertices()):
                if len(rows) >= max_rows:
                    raise ValueError(
                        f"{name}: the layer provides more than {max_rows} "
                        "usable points."
                    )
                wgs84 = _transform_point_to_wgs84(transform, point, name)
                identifier = _field_text(feature, id_field)
                order = _field_value(feature, order_field)
                if isinstance(order, float) and not math.isfinite(order):
                    order = None
                rows.append(
                    {
                        "id": identifier if identifier else str(feature.id()),
                        "order": order,
                        "feature_id": feature.id(),
                        "part": part_index,
                        "position": [wgs84.x(), wgs84.y()],
                        "timestamp": _field_timestamp(feature, timestamp_field),
                        "heading": _field_number(feature, heading_field),
                        "speed": _field_number(feature, speed_field),
                    }
                )
        rows.sort(
            key=lambda row: (
                _order_sort_key(row["order"]),
                row["feature_id"],
                row["part"],
            )
        )
        return rows

    def _validate_request_options(self, request: dict) -> None:
        """Validates the region capabilities right before a request is sent."""
        options = request["options"]
        routes_capabilities.validate_region_options(
            request["region"],
            request["function"],
            travel_mode=options.travel_mode,
            avoid=getattr(options, "avoid", ()),
            max_alternatives_value=getattr(options, "max_alternatives", None),
            arrival_time=getattr(options, "arrival_time", None),
            origins_count=len(request.get("origins") or ()) or None,
            destinations_count=len(request.get("destinations") or ()) or None,
        )
        # Body building validates all count limits before billing confirmation.
        _BODY_BUILDERS[request["function"]](options)

    def _run(self) -> None:
        """Runs the selected operation and adds its results to the map."""
        if self._busy:
            return
        function = self._current_function()
        self._cancelled = False
        self._busy = True
        request: dict = {}
        try:
            request = self._build_request(function)
            if self._cancelled:
                return
            self._validate_request_options(request)
            if not self._confirm_billing(request):
                return
            # The confirmation's nested event loop may have cancelled the run.
            if self._cancelled:
                return
            # Clear stale pricing, then retain the new HTTP 200 bucket even if
            # its response body cannot be parsed.
            self.routes.api_handler.last_pricing_bucket = None
            try:
                with busy_operation(self.button_search, "Running…"):
                    result = self._send_request(request)
            finally:
                request["pricing_bucket"] = self.routes.api_handler.last_pricing_bucket
            if self._cancelled:
                return
            self._publish_result(request, result)
        except Exception as e:
            self._report_run_error(e, function)
            bucket = (
                request.get("pricing_bucket") if isinstance(request, dict) else None
            )
            if bucket and not self._cancelled:
                push_message(
                    INFO,
                    f"The {function} request used the {bucket} pricing bucket.",
                )
        finally:
            self._busy = False
            # Restore the label for the function currently shown.
            self.button_search.setText(
                constants.RUN_BUTTON_LABEL.get(self._current_function(), "Run")
            )

    def _send_request(self, request: dict) -> dict:
        """Re-checks the captured credentials and sends one request."""
        credentials = request["credentials"]
        if credentials != self.routes.configuration_handler.get_credentials():
            raise ValueError(
                "The configured region or API key changed after this request "
                "started. Run it again."
            )
        function = request["function"]
        options = request["options"]
        if function == "CalculateRoutes":
            return self.routes.request_routes(options, credentials=credentials)
        if function == "CalculateIsolines":
            return self.routes.request_isolines(options, credentials=credentials)
        if function == "SnapToRoads":
            return self.routes.request_snap_to_roads(options, credentials=credentials)
        if function == "CalculateRouteMatrix":
            return self.routes.request_route_matrix(options, credentials=credentials)
        raise ValueError(f"Unknown function: {function}")

    def _publish_result(self, request: dict, result: dict) -> None:
        """Builds, checks, and publishes the layers of one response."""
        function = request["function"]
        if function == "CalculateRoutes":
            layers, message = self._routes_result_layers(request, result)
        elif function == "CalculateIsolines":
            layers, message = self._isolines_result_layers(request, result)
        elif function == "SnapToRoads":
            layers, message = self._snap_result_layers(request, result)
        elif function == "CalculateRouteMatrix":
            layers, message = self._matrix_result_layers(request, result)
        else:
            raise ValueError(f"Unknown function: {function}")
        bucket = request.get("pricing_bucket")
        if function == "CalculateRoutes":
            self._show_attributions(request.get("attributions") or [])
        if not layers:
            text = f"{function} returned no drawable results."
            if bucket:
                text = f"{text} [{bucket} pricing bucket]"
            push_message(INFO, text)
            return

        previous_active = iface.activeLayer()
        try:
            routes_layers.publish_layers(layers)
        except Exception:
            self._restore_active_layer(previous_active)
            raise
        iface.setActiveLayer(layers[0])
        if bucket:
            message = f"{message} [{bucket} pricing bucket]"
        push_message(SUCCESS, message)

    def _routes_result_layers(self, request: dict, result: dict) -> tuple:
        """Builds the CalculateRoutes layers and success message."""
        routes_results.validate_route_count(result, request["options"].max_alternatives)
        attributions = routes_results.collect_attributions(result)
        request["attributions"] = attributions
        self._report_notices("CalculateRoutes", routes_results.collect_notices(result))
        layer = routes_layers.build_route_leg_layer(result)
        if layer is None:
            return [], ""
        if any(
            None in routes_results.route_totals(route)
            for route in result.get("Routes") or []
            if isinstance(route, dict)
        ):
            push_message(
                WARNING,
                "Some route totals are unavailable; their RouteDistance and "
                "RouteDuration are NULL.",
                duration=10,
            )
        layers = [layer]
        if request.get("add_summary"):
            summary = routes_layers.build_route_summary_layer(result)
            if summary is not None:
                layers.append(summary)
        for built in layers:
            routes_layers.record_layer_source(built, "CalculateRoutes", attributions)
        return layers, self._routes_success_message(result)

    @staticmethod
    def _routes_success_message(result: dict) -> str:
        """Summarizes the main route and the alternative count."""
        routes = [route for route in result.get("Routes") or [] if route]
        message = "Added the “CalculateRoutes” layer."
        if routes:
            distance, duration = routes_results.route_totals(routes[0])
            parts = []
            if distance is not None:
                parts.append(f"{distance / 1000:.1f} km")
            if duration is not None:
                parts.append(f"{round(duration / 60)} min")
            if parts:
                message = f"{message} Main route: {', '.join(parts)}."
            if len(routes) > 1:
                message = f"{message} {len(routes) - 1} alternative(s)."
        return message

    def _isolines_result_layers(self, request: dict, result: dict) -> tuple:
        """Builds the CalculateIsolines layer and success message."""
        options = request["options"]
        isolines = routes_results.normalize_isolines(
            result, options.threshold_type, options.thresholds
        )
        layer = routes_layers.build_isoline_layer(
            isolines, options.direction, options.travel_mode
        )
        if layer is None:
            return [], ""
        routes_layers.record_layer_source(layer, "CalculateIsolines")
        return [layer], (
            f"Added the “CalculateIsolines” layer ({len(isolines)} isolines)."
        )

    def _snap_result_layers(self, request: dict, result: dict) -> tuple:
        """Builds the SnapToRoads layers and success message."""
        notices = routes_results.validate_notices(result, "The SnapToRoads response")
        self._report_snap_notices(notices)
        rows = request["rows"]
        # Always verify response indexes before joining input attributes,
        # even when the confidence-point layer is disabled.
        snapped = routes_results.normalize_snapped_trace_points(
            result, [row["position"] for row in rows]
        )
        # Confidence points are never published without the primary line.
        line_points = routes_results.snapped_line_points(result)
        if line_points is None:
            return [], ""
        layers = [
            routes_layers.build_snap_line_layer(
                line_points,
                len(rows),
                len(notices),
            )
        ]
        if request["output_confidence"]:
            points_layer = routes_layers.build_snap_points_layer(snapped, rows)
            if points_layer is not None:
                layers.append(points_layer)
        for built in layers:
            routes_layers.record_layer_source(built, "SnapToRoads")
        return layers, "Added the “SnapToRoads” layer(s)."

    def _matrix_result_layers(self, request: dict, result: dict) -> tuple:
        """Builds the CalculateRouteMatrix layers and success message."""
        origins = request["origins"]
        destinations = request["destinations"]
        rows = routes_results.normalize_matrix(result, len(origins), len(destinations))
        error_count = sum(1 for row in rows for cell in row if cell["error"])
        if error_count:
            push_message(
                WARNING,
                f"CalculateRouteMatrix returned {error_count} cell error(s); "
                "their Distance and Duration are NULL.",
                duration=10,
            )
        layers = routes_layers.build_matrix_layers(
            rows, origins, destinations, request["od_lines"]
        )
        for built in layers:
            routes_layers.record_layer_source(built, "CalculateRouteMatrix")
        cells = len(origins) * len(destinations)
        return layers, (f"Added the “CalculateRouteMatrix” table ({cells} routes).")

    @staticmethod
    def _restore_active_layer(previous) -> None:
        """Restores the active layer, clearing it when there was none."""
        if previous is not None and not sip.isdeleted(previous):
            iface.setActiveLayer(previous)
            return
        # Clearing the tree selection also works when QGIS 3 ignores None.
        view = iface.layerTreeView()
        if view is not None:
            view.setCurrentIndex(QModelIndex())

    def _report_notices(self, function: str, notices: list) -> None:
        """Summarizes the notices in the bar and logs every detail."""
        if not notices:
            return
        for notice in notices:
            QgsMessageLog.logMessage(
                f"{function} notice: code={notice['code']!r} "
                f"impact={notice['impact']!r} scope={notice['scope']} "
                f"route={notice['route_index']} leg={notice['leg_index']} "
                f"type={notice['leg_type']!r} details={notice['details']!r}",
                "Amazon Location Service",
                Qgis.MessageLevel.Warning,
            )
        codes = sorted({notice["code"] for notice in notices if notice.get("code")})
        if codes:
            text = f"{function} notices: {', '.join(codes)}."
        else:
            text = f"{function} returned {len(notices)} notice(s)."
        push_message(WARNING, f"{text} See the log panel for details.", duration=10)

    def _report_snap_notices(self, notices: list) -> None:
        """Shows the SnapToRoads notices (Code / Title / TracePointIndexes)."""
        labels = []
        for notice in notices:
            QgsMessageLog.logMessage(
                f"SnapToRoads notice: code={notice.get('Code')!r} "
                f"title={notice.get('Title')!r} "
                f"trace_points={notice.get('TracePointIndexes')!r}",
                "Amazon Location Service",
                Qgis.MessageLevel.Warning,
            )
            label = str(notice.get("Title") or notice.get("Code") or "").strip()
            if label:
                labels.append(label)
        if labels:
            push_message(
                WARNING,
                f"SnapToRoads notices: {', '.join(sorted(set(labels)))}. "
                "See the log panel for details.",
                duration=10,
            )

    def _show_attributions(self, attributions: list) -> None:
        """Shows the required data attributions until the next run."""
        if not attributions:
            self.attributions_label.setText(constants.NO_ATTRIBUTIONS_TEXT)
            return
        lines = ["From the last CalculateRoutes run:"]
        for entry in attributions:
            text = html.escape(entry["description"] or entry["url"])
            url = entry["url"]
            # The URL comes from the API response; only web links may open.
            if url and urlparse(url).scheme.lower() in ("http", "https"):
                lines.append(f'<a href="{html.escape(url)}">{text}</a>')
            elif url:
                lines.append(f"{text} ({html.escape(url)})")
            else:
                lines.append(text)
        self.attributions_label.setText("<br>".join(lines))

    def _billing_estimate(self, function: str) -> tuple:
        """Returns the ``(unit text, bucket, reason)`` billing estimate."""
        grab = routes_capabilities.is_grab_region(self._configured_region())
        if function == "CalculateRoutes":
            mode = self.travelmode_comboBox.currentText()
            if mode == "Intermodal":
                return "1 request", "Premium", "Intermodal travel mode"
            if mode == "Scooter" and not grab:
                return "1 request", "Advanced", "Scooter travel mode"
            return "1 request", "Core", f"{mode} travel mode"
        if function == "CalculateIsolines":
            return self._billing_estimate_isolines()
        if function != "CalculateRouteMatrix":
            raise ValueError(f"Unknown function: {function}")
        mode = self.mx_travelmode_comboBox.currentText()
        unit = "origins x destinations"
        if mode == "Scooter" and not grab:
            return unit, "Advanced", "Scooter travel mode"
        return unit, "Core", f"{mode} travel mode"

    def _billing_estimate_isolines(self) -> tuple:
        """Returns the isoline billing estimate from the current inputs."""
        threshold_type = self.iso_threshold_type_comboBox.currentData() or "Time"
        factor = 60 if threshold_type == "Time" else 1000
        advanced_limit = 3600 if threshold_type == "Time" else 100_000
        count = 0
        over_advanced = False
        for part in self.iso_thresholds_lineEdit.text().split(","):
            part = part.strip()
            if not part:
                continue
            count += 1
            try:
                number = float(part)
            except ValueError:
                continue
            value = number * factor
            if not math.isfinite(value):
                continue
            if round(value) > advanced_limit:
                over_advanced = True
        unit = f"{count or 1} billed isolines (one per threshold)"
        if self.iso_travelmode_comboBox.currentText() == "Scooter":
            return unit, "Premium", "Scooter travel mode"
        if over_advanced:
            limit = "60 minutes" if threshold_type == "Time" else "100 km"
            return unit, "Premium", f"a threshold exceeds {limit}"
        return unit, "Advanced", "within the Advanced threshold range"

    def _snap_billing_from_request(self, request: dict) -> tuple:
        """Recomputes the snap estimate from the exact captured trace points."""
        rows = request["rows"]
        unit = f"1 request ({len(rows)} trace points)"
        if request["options"].travel_mode == "Scooter":
            return unit, "Premium", "Scooter travel mode"
        if len(rows) > 200:
            return unit, "Premium", "more than 200 trace points"
        # AWS does not say "adjacent", so check every pair. At most 200 points
        # means 19,900 checks and this also handles the antimeridian.
        positions = [row["position"] for row in rows]
        for index, first in enumerate(positions):
            for second in positions[index + 1 :]:
                if haversine_meters(tuple(first), tuple(second)) > 100_000:
                    return unit, "Premium", "trace points more than 100 km apart"
        return unit, "Advanced", "within the Advanced limits"

    def _confirm_billing(self, request: dict) -> bool:
        """Asks for confirmation before the higher-cost requests."""
        function = request["function"]
        if function == "SnapToRoads":
            # Use the captured points so the confirmation matches the request.
            unit, bucket, reason = self._snap_billing_from_request(request)
        else:
            unit, bucket, reason = self._billing_estimate(function)
        needs_confirmation = bucket == "Premium"
        if function == "CalculateRouteMatrix":
            origins = len(request["origins"])
            destinations = len(request["destinations"])
            unit = (
                f"{origins} x {destinations} = {origins * destinations} "
                "billed route calculations"
            )
            needs_confirmation = True
        elif function == "CalculateIsolines":
            thresholds = len(request["options"].thresholds)
            unit = f"{thresholds} billed isolines (one per threshold)"
            needs_confirmation = needs_confirmation or thresholds > 1
        if not needs_confirmation:
            return True
        answer = QMessageBox.question(
            self,
            "Billing confirmation",
            f"This will send {unit} at the estimated {bucket} pricing "
            f"bucket ({reason}). Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _report_run_error(self, error: Exception, function: str) -> None:
        """Reports one failed run; cancelled work stays silent."""
        if self._cancelled:
            return
        # ConfigurationError subclasses ValueError, so it must be checked first.
        if isinstance(error, ConfigurationError):
            show_config_error(self, error)
            return
        if isinstance(error, ValueError):
            show_error(self, "Input Error", str(error))
            return
        if isinstance(error, ApiError) and error.status_code == 403:
            show_error(
                self, "Routes Error", f"{function} failed: {error} {PERMISSION_HINT}"
            )
            return
        show_error(self, "Routes Error", f"Failed to run {function}: {error}")

    def _cancel(self) -> None:
        """Closes the dialog and cancels active work."""
        self.close()

    def hideEvent(self, event) -> None:
        """Cancels active work when hidden, except during minimization."""
        super().hideEvent(event)
        if event.spontaneous():
            return
        self._cancelled = True
        self.routes.api_handler.abort()
        release_map_tool(self._start_map_click)
        release_map_tool(self._end_map_click)
        release_map_tool(self._iso_map_click)
        release_map_tool(self._waypoint_collector)
        self._waypoints_finished()


def _transform_point_to_wgs84(transform, point, name: str) -> QgsPointXY:
    """Transforms one layer point and reports a readable input error."""
    try:
        return transform.transform(QgsPointXY(point))
    except QgsCsException as error:
        raise ValueError(
            f"{name}: a point could not be transformed to WGS 84."
        ) from error


def _is_null(value) -> bool:
    """Returns whether a feature attribute is NULL."""
    return value is None or value == NULL


def _field_value(feature, field_name: str):
    """Returns a raw attribute value, or ``None`` for NULL or no field."""
    if not field_name:
        return None
    value = feature[field_name]
    return None if _is_null(value) else value


def _field_text(feature, field_name: str) -> str:
    """Returns an attribute as text, or an empty string."""
    value = _field_value(feature, field_name)
    return "" if value is None else str(value)


def _field_number(feature, field_name: str):
    """Returns an attribute as a float, or ``None``."""
    value = _field_value(feature, field_name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"Field {field_name!r} must contain numbers (got {value!r})."
        ) from None


def _field_timestamp(feature, field_name: str):
    """Returns an attribute as an offset-aware ISO time string, or ``None``."""
    value = _field_value(feature, field_name)
    if value is None:
        return None
    if hasattr(value, "toPyDateTime"):
        # QDateTime values are local time; attach the system UTC offset.
        return value.toPyDateTime().astimezone().isoformat(timespec="seconds")
    return str(value).strip() or None


def _order_sort_key(order) -> tuple:
    """Returns a type-safe sort key: numbers, then text, then NULL."""
    if order is None:
        return (2, 0.0, "")
    if isinstance(order, bool):
        return (1, 0.0, str(order))
    if isinstance(order, (int, float)):
        # NaN breaks sorting, so non-finite values sort with NULL, last.
        if math.isfinite(order):
            return (0, float(order), "")
        return (2, 0.0, "")
    return (1, 0.0, str(order))
