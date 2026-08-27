import re
import unittest
from unittest.mock import DEFAULT, Mock, patch

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    import qgis.utils
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCsException,
        QgsFeature,
        QgsGeometry,
        QgsProject,
        QgsVectorLayer,
    )
    from qgis.gui import QgsMapCanvas, QgsMapToolPan
    from qgis.PyQt.QtCore import QDate, QDateTime, QModelIndex, Qt, QTime
    from qgis.PyQt.QtWidgets import QMessageBox

    from location_service.functions.routes_requests import (
        MAX_TRACE_POINTS,
        TRANSIT_MODE_VALUES,
        SnapOptions,
        TracePoint,
    )
    from location_service.ui.routes import constants
    from location_service.ui.routes import routes as routes_module
    from location_service.ui.routes.routes import RoutesUi
    from location_service.utils.click_handler import MultiPointCollector
    from location_service.utils.external_api_handler import ApiError
    from location_service.utils.feedback import INFO, SUCCESS, WARNING

    HAS_IFACE = qgis.utils.iface is not None

    class CountingLayer(QgsVectorLayer):
        """A point layer that counts the features a scan reads from it."""

        def __init__(self, *args) -> None:
            """Creates the layer with its scan counter at zero."""
            super().__init__(*args)
            self.scanned = 0

        def getFeatures(self, *args):
            """Yields the layer features, counting each one handed out."""
            for feature in super().getFeatures(*args):
                self.scanned += 1
                yield feature

        def getSelectedFeatures(self, *args):
            """Yields the selected features, counting each one handed out."""
            for feature in super().getSelectedFeatures(*args):
                self.scanned += 1
                yield feature

else:
    HAS_IFACE = False

CREDENTIALS = ("ap-northeast-1", "v1.public.test")  # pragma: allowlist secret
CHANGED_CREDENTIALS = ("us-east-1", "v1.public.test")  # pragma: allowlist secret
REQUEST_METHODS = (
    "request_routes",
    "request_isolines",
    "request_snap_to_roads",
    "request_route_matrix",
)
FUNCTION_PAGES = (
    ("CalculateRoutes", "page_calculate_routes", "Get route"),
    ("CalculateIsolines", "page_calculate_isolines", "Get areas"),
    ("SnapToRoads", "page_snap_to_roads", "Snap to roads"),
    ("CalculateRouteMatrix", "page_calculate_route_matrix", "Build matrix"),
)
# A drawable CalculateRoutes response, so a skipped publish means cancellation.
# Every leg needs an official Type and TravelMode: the layer builder rejects
# a response whose legs carry anything else.
ROUTE_RESULT = {
    "Routes": [
        {
            "Summary": {"Distance": 1200, "Duration": 600},
            "Legs": [
                {
                    "Type": "Vehicle",
                    "TravelMode": "Car",
                    "Geometry": {"LineString": [[139.70, 35.60], [139.80, 35.70]]},
                }
            ],
        }
    ]
}
# A Transit route carrying the attributions the API requires displaying. The
# leg TravelMode is the concrete mode ("Subway"), not the "Transit" leg type.
TRANSIT_ROUTE_RESULT = {
    "Routes": [
        {
            "Summary": {"Distance": 5200, "Duration": 1800},
            "Legs": [
                {
                    "Type": "Transit",
                    "TravelMode": "Subway",
                    "Geometry": {"LineString": [[139.70, 35.60], [139.80, 35.70]]},
                    "TransitLegDetails": {
                        "Agency": {"Name": "Tokyo Metro"},
                        "Departure": {"Time": "2026-03-01T09:30:00+09:00"},
                        "Arrival": {"Time": "2026-03-01T10:00:00+09:00"},
                        "Transport": {
                            "RouteName": "Ginza Line",
                            "Headsign": "Asakusa",
                        },
                        "Attributions": [
                            {
                                "WebLink": {
                                    "Description": "Tokyo Metro",
                                    "Url": "https://example.com/metro",
                                }
                            }
                        ],
                    },
                }
            ],
        }
    ]
}
MATRIX_RESULT = {
    "RouteMatrix": [
        [{"Distance": 1200, "Duration": 600}, {"Distance": 2400, "Duration": 900}],
        [{"Distance": 1500, "Duration": 700}, {"Distance": 800, "Duration": 400}],
    ]
}
BROKEN_MATRIX_RESULT = {
    "RouteMatrix": [
        [{"Distance": 1200, "Duration": 600}, {"Distance": 2400, "Duration": 900}],
    ]
}
# RouteMatrixEntry.Error is an official enum, not free text; AWS also fills
# Distance and Duration on a failed pair, and both must stay out of the layer.
MATRIX_ERROR = "NoRoute"
MATRIX_ERROR_RESULT = {
    "RouteMatrix": [
        [{"Distance": 1200, "Duration": 600}, {"Distance": 2400, "Duration": 900}],
        [
            {"Distance": 1500, "Duration": 700},
            {"Distance": 0, "Duration": 0, "Error": MATRIX_ERROR},
        ],
    ]
}
TRACE_POSITIONS = ((139.70, 35.60), (139.71, 35.61))
ORIGIN_POSITIONS = ((139.70, 35.60), (139.71, 35.61))
DESTINATION_POSITIONS = ((139.80, 35.70), (139.81, 35.71))
PICKED_TIME = "2026-03-01T09:30:00"
UTC_OFFSET = re.compile(r"^[+-]\d{2}:\d{2}$")
# Trace points one degree of latitude apart, about 111 km; three points whose
# adjacent pairs are each about 89 km but which span about 178 km; and three
# points a few hundred meters apart.
FAR_TRACE = ((139.70, 35.60), (139.70, 36.60))
SPREAD_TRACE = ((0.0, 0.0), (0.8, 0.0), (1.6, 0.0))
NEAR_TRACE = ((139.70, 35.60), (139.71, 35.61), (139.72, 35.62))
# Traces on both sides of the antimeridian: the first spans about 122 km, the
# second only about 11 km even though its longitudes are 359.9 degrees apart.
ANTIMERIDIAN_TRACE = ((179.0, 0.0), (179.9, 0.0), (-179.9, 0.0))
NEAR_ANTIMERIDIAN_TRACE = ((179.95, 0.0), (-179.95, 0.0))


def _item_enabled(combo, text):
    """Returns whether the combo box entry with this text is selectable."""
    return combo.model().item(combo.findText(text)).isEnabled()


def _snap_request(positions, travel_mode="Car"):
    """Builds the captured request the snap billing estimate reads."""
    return {
        "rows": [{"position": list(position)} for position in positions],
        "options": SnapOptions(
            trace_points=tuple(TracePoint(position=point) for point in positions),
            travel_mode=travel_mode,
        ),
    }


def _isoline_ring(index):
    """Returns one closed four-point ring around the isoline center."""
    size = 0.01 * (index + 1)
    corner = [139.70 - size, 35.60 - size]
    return [corner, [139.70 + size, 35.60 - size], [139.70, 35.60 + size], corner]


def _isoline_response(minutes):
    """Builds isolines that answer exactly the requested time thresholds."""
    return {
        "Isolines": [
            {
                "TimeThreshold": round(value * 60),
                "Geometries": [{"Polygon": [_isoline_ring(index)]}],
            }
            for index, value in enumerate(minutes)
        ]
    }


def _snapped_points(positions):
    """Returns SnappedTracePoints that answer the sent trace positions."""
    return [
        {
            "OriginalPosition": [lon, lat],
            "SnappedPosition": [lon + 0.001, lat + 0.001],
            "Confidence": 0.9,
        }
        for lon, lat in positions
    ]


def _snap_response(positions, with_line=True):
    """Builds a SnapToRoads response for the sent trace positions."""
    response = {"SnappedTracePoints": _snapped_points(positions)}
    if with_line:
        response["SnappedGeometry"] = {
            "LineString": [[lon + 0.001, lat + 0.001] for lon, lat in positions]
        }
    return response


def _messages_at(push_message, level):
    """Returns the texts pushed to the message bar at one message level."""
    return [
        call.args[1] for call in push_message.call_args_list if call.args[0] == level
    ]


def _point_layer(fields="", geometry="Point", name="trace"):
    """Returns an unregistered memory layer with the given attribute fields."""
    uri = f"{geometry}?crs=EPSG:4326"
    return QgsVectorLayer(f"{uri}&{fields}" if fields else uri, name, "memory")


def _add_point(layer, wkt, attributes=None):
    """Adds one feature to a memory layer and returns its feature id."""
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromWkt(wkt))
    for name, value in (attributes or {}).items():
        feature[name] = value
    _added, features = layer.dataProvider().addFeatures([feature])
    return features[0].id()


def _line_positions(count, lon=139.70, lat=35.60):
    """Returns ``count`` distinct positions spaced along one latitude."""
    return tuple((round(lon + index * 0.001, 6), lat) for index in range(count))


def _fill_points(layer, count):
    """Adds ``count`` distinct points to a memory layer and returns it."""
    features = []
    for lon, lat in _line_positions(count):
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt(f"Point ({lon} {lat})"))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return layer


def _counting_layer(count):
    """Returns a scan-counting point layer holding ``count`` points."""
    return _fill_points(
        CountingLayer("Point?crs=EPSG:4326", "counted", "memory"), count
    )


def _rows(
    layer,
    selected_only=False,
    id_field="",
    order_field="",
    name="Point layer",
    max_rows=None,
):
    """
    Snapshots a layer without a dialog; the method never uses ``self``.

    ``max_rows`` is required by the dialog and defaults here to the largest
    limit it passes, so a test only sets it to check the limit itself.
    """
    return RoutesUi._layer_input_rows(
        None,
        layer,
        selected_only,
        id_field,
        order_field,
        max_rows=MAX_TRACE_POINTS if max_rows is None else max_rows,
        name=name,
    )


def _positions(rows):
    """Returns the row positions rounded to the sixth decimal."""
    return [[round(value, 6) for value in row["position"]] for row in rows]


@unittest.skipUnless(HAS_IFACE, "A running QGIS iface is required")
class TestRoutesUiRegressions(unittest.TestCase):
    """Covers the dialog state, the billing guards and the published layers."""

    def setUp(self):
        patcher = patch.object(
            RoutesUi, "_configured_region", return_value="ap-northeast-1"
        )
        self.region = patcher.start()
        self.addCleanup(patcher.stop)
        self.dialog = RoutesUi()
        self.project = QgsProject.instance()
        self.existing_layers = set(self.project.mapLayers())
        self.input_layers = set()
        self.addCleanup(self._remove_test_layers)

    def tearDown(self):
        self.dialog.deleteLater()

    def _remove_test_layers(self):
        """Removes every layer this test added, published or input."""
        added = list(set(self.project.mapLayers()) - self.existing_layers)
        if added:
            self.project.removeMapLayers(added)

    def _published_layer_names(self):
        """Returns the names of the result layers the run published."""
        published = (
            set(self.project.mapLayers()) - self.existing_layers - self.input_layers
        )
        return sorted(self.project.mapLayer(layer_id).name() for layer_id in published)

    def _published_layer(self, name):
        """Returns the single published result layer with this name."""
        (layer,) = self.project.mapLayersByName(name)
        return layer

    def _patch(self, name):
        """Patches one attribute of the dialog module for this test."""
        patcher = patch.object(routes_module, name)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _mock_routes(self, bucket="Core"):
        """Replaces the network facade so no request leaves the test."""
        routes = Mock()
        routes.configuration_handler.get_credentials.return_value = CREDENTIALS
        # Start with a stale value; each mocked response writes the current bucket.
        routes.api_handler.last_pricing_bucket = bucket

        def bill(*_args, **_kwargs):
            routes.api_handler.last_pricing_bucket = bucket
            return DEFAULT

        for method in REQUEST_METHODS:
            getattr(routes, method).side_effect = bill
        self.dialog.routes = routes
        return routes

    def _patch_send_request(self, response, bucket="Core"):
        """Replaces the send step with one that bills the run first."""

        def send(_request):
            self.dialog.routes.api_handler.last_pricing_bucket = bucket
            return response

        return patch.object(self.dialog, "_send_request", side_effect=send)

    def _run_dialog(self):
        """Runs the dialog once and returns the error box and message mocks."""
        # The error box is modal, so an unexpected failure would block the run.
        error = self._patch("show_error")
        push_message = self._patch("push_message")
        self.dialog._run()
        return error, push_message

    def _patch_iface(self, active_layer):
        """Replaces the QGIS interface so its layer calls can be asserted."""
        iface = self._patch("iface")
        iface.activeLayer.return_value = active_layer
        return iface

    def _run_with_failed_publish(self, active_layer):
        """Runs CalculateRoutes with a publish that fails as QGIS would."""
        self._set_route_positions()
        iface = self._patch_iface(active_layer)
        patcher = patch.object(
            routes_module.routes_layers,
            "publish_layers",
            side_effect=RuntimeError("Could not add a result layer to the project."),
        )
        self.addCleanup(patcher.stop)
        patcher.start()
        routes = self._mock_routes()
        routes.request_routes.return_value = ROUTE_RESULT
        error, push_message = self._run_dialog()
        return iface, error, push_message

    def _project_layer(self, name, positions):
        """Adds an input point layer to the project for the layer pickers."""
        layer = _point_layer(name=name)
        for lon, lat in positions:
            _add_point(layer, f"Point ({lon} {lat})")
        self.project.addMapLayer(layer)
        self.input_layers.add(layer.id())
        return layer

    def _set_route_positions(self):
        """Fills the start and end coordinates a route request requires."""
        self.dialog.st_lon_lineEdit.setText("139.70")
        self.dialog.st_lat_lineEdit.setText("35.60")
        self.dialog.ed_lon_lineEdit.setText("139.80")
        self.dialog.ed_lat_lineEdit.setText("35.70")

    def _set_isoline_center(self):
        """Fills the center coordinates an isoline request requires."""
        self.dialog.iso_lon_lineEdit.setText("139.70")
        self.dialog.iso_lat_lineEdit.setText("35.60")

    def _set_snap_layer(self):
        """Selects a trace layer for SnapToRoads."""
        layer = self._project_layer("trace points", TRACE_POSITIONS)
        self.dialog.snap_layer_comboBox.setLayer(layer)
        return layer

    def _set_matrix_layers(self):
        """Selects the origin and destination layers for the matrix."""
        origins = self._project_layer("matrix origins", ORIGIN_POSITIONS)
        destinations = self._project_layer("matrix destinations", DESTINATION_POSITIONS)
        self.dialog.mx_origins_comboBox.setLayer(origins)
        self.dialog.mx_dest_comboBox.setLayer(destinations)

    def _set_matrix_layer_sizes(self, origins, destinations):
        """Selects matrix layers holding the given numbers of points."""
        self.dialog.mx_origins_comboBox.setLayer(
            self._project_layer("matrix origins", _line_positions(origins))
        )
        self.dialog.mx_dest_comboBox.setLayer(
            self._project_layer(
                "matrix destinations", _line_positions(destinations, lat=35.70)
            )
        )

    def _set_thresholds(self, text, threshold_type="Time"):
        """Selects the threshold unit and enters the raw threshold text."""
        combo = self.dialog.iso_threshold_type_comboBox
        combo.setCurrentIndex(combo.findData(threshold_type))
        self.dialog.iso_thresholds_lineEdit.setText(text)

    def test_each_function_gets_its_own_page_and_run_button_label(self):
        for function, page, label in FUNCTION_PAGES:
            with self.subTest(function=function):
                self.dialog.routes_comboBox.setCurrentText(function)

                stack = self.dialog.params_stackedWidget
                assert stack.currentIndex() == constants.FUNCTION_PAGE[function]
                assert stack.currentWidget().objectName() == page
                assert self.dialog.button_search.text() == label

    def test_only_the_current_function_page_sets_the_stack_height(self):
        stack = self.dialog.params_stackedWidget
        route_height = stack.currentWidget().sizeHint().height()

        for function, _page, _label in FUNCTION_PAGES:
            with self.subTest(function=function):
                self.dialog.routes_comboBox.setCurrentText(function)
                current_index = stack.currentIndex()

                assert (
                    stack.layout().sizeHint().height()
                    == stack.currentWidget().sizeHint().height()
                )
                for page_index in range(stack.count()):
                    policy = stack.widget(page_index).sizePolicy().verticalPolicy()
                    expected = (
                        routes_module._SIZE_POLICY.Preferred
                        if page_index == current_index
                        else routes_module._SIZE_POLICY.Ignored
                    )
                    assert policy == expected

                if function != "CalculateRoutes":
                    assert stack.layout().sizeHint().height() < route_height

    def test_switching_function_scrolls_to_the_top(self):
        self.dialog.routes_comboBox.setCurrentText("CalculateIsolines")
        scroll_bar = self.dialog.scrollArea.verticalScrollBar()
        scroll_bar.setRange(0, 100)
        scroll_bar.setValue(100)

        self.dialog.routes_comboBox.setCurrentText("CalculateRoutes")

        assert scroll_bar.value() == 0

    def test_unknown_functions_are_not_published_or_billed_as_a_matrix(self):
        with self.assertRaisesRegex(ValueError, "Unknown function"):
            self.dialog._publish_result({"function": "UnknownOperation"}, {})
        with self.assertRaisesRegex(ValueError, "Unknown function"):
            self.dialog._billing_estimate("UnknownOperation")

    def test_short_function_pages_keep_extra_space_below_the_controls(self):
        for name in (
            "calculateIsolinesLayout",
            "snapToRoadsLayout",
            "calculateRouteMatrixLayout",
        ):
            with self.subTest(layout=name):
                layout = getattr(self.dialog, name)
                spacer = layout.itemAt(layout.count() - 1).spacerItem()

                assert spacer is not None
                assert spacer.expandingDirections() & Qt.Orientation.Vertical

    def test_transit_travel_mode_disables_the_vehicle_only_options(self):
        self.dialog.travelmode_comboBox.setCurrentText("Transit")

        for widget in self._vehicle_only_widgets():
            with self.subTest(widget=widget.objectName()):
                assert not widget.isEnabled()
                assert widget.toolTip() == constants.TRANSIT_DISABLED_TOOLTIP
        for name, _key in constants.AVOID_OPTIONS:
            with self.subTest(avoid=name):
                assert not getattr(self.dialog, name).isEnabled()
        assert self.dialog.transit_groupBox.isEnabled()

    def test_the_transit_mode_list_offers_every_mode_except_all(self):
        widget = self.dialog.transit_modes_listWidget
        modes = [widget.item(index).text() for index in range(widget.count())]

        assert len(modes) == 15
        assert "All" not in modes
        assert modes == [mode for mode in TRANSIT_MODE_VALUES if mode != "All"]

    def test_intermodal_travel_mode_also_disables_the_transit_modes(self):
        self.dialog.travelmode_comboBox.setCurrentText("Intermodal")

        assert not self.dialog.optimizefor_comboBox.isEnabled()
        assert not self.dialog.wp_button_click.isEnabled()
        assert not self.dialog.transit_groupBox.isEnabled()

    def test_switching_back_to_car_restores_the_vehicle_only_options(self):
        self.dialog.travelmode_comboBox.setCurrentText("Transit")

        self.dialog.travelmode_comboBox.setCurrentText("Car")

        for widget in self._vehicle_only_widgets():
            with self.subTest(widget=widget.objectName()):
                assert widget.isEnabled()
                assert widget.toolTip() == ""
        for name, _key in constants.AVOID_OPTIONS:
            with self.subTest(avoid=name):
                assert getattr(self.dialog, name).isEnabled()
                assert getattr(self.dialog, name).toolTip() == ""
        assert not self.dialog.transit_groupBox.isEnabled()

    def _vehicle_only_widgets(self):
        """Returns the controls that only apply to vehicle-style travel modes."""
        return (
            self.dialog.optimizefor_comboBox,
            self.dialog.wp_button_click,
            self.dialog.wp_button_undo,
            self.dialog.wp_button_clear,
            self.dialog.waypoints_listWidget,
        )

    def test_the_date_time_editor_follows_the_time_choice(self):
        assert self.dialog.time_none_radioButton.isChecked()
        assert not self.dialog.time_dateTimeEdit.isEnabled()

        for radio, expected in (
            (self.dialog.time_departure_radioButton, True),
            (self.dialog.time_arrival_radioButton, True),
            (self.dialog.time_departnow_radioButton, False),
            (self.dialog.time_none_radioButton, False),
        ):
            with self.subTest(radio=radio.objectName()):
                radio.setChecked(True)

                assert self.dialog.time_dateTimeEdit.isEnabled() == expected

    def test_the_date_time_editor_starts_at_the_current_time(self):
        # routes.ui sets no value, so Qt alone would start it at 2000-01-01.
        assert self.dialog.time_dateTimeEdit.dateTime().date().year() >= 2020

    def test_an_explicit_time_is_sent_with_the_local_utc_offset(self):
        self.dialog.time_dateTimeEdit.setDateTime(
            QDateTime(QDate(2026, 3, 1), QTime(9, 30))
        )

        for radio, key in (
            (self.dialog.time_departure_radioButton, "departure_time"),
            (self.dialog.time_arrival_radioButton, "arrival_time"),
        ):
            with self.subTest(key=key):
                radio.setChecked(True)

                selected = self.dialog._selected_time()

                assert set(selected) == {key}
                assert selected[key].startswith(PICKED_TIME)
                assert UTC_OFFSET.match(selected[key].removeprefix(PICKED_TIME))

    def test_a_threshold_that_rounds_to_zero_is_rejected(self):
        self._set_thresholds("0.001")

        with self.assertRaisesRegex(ValueError, "rounds to zero"):
            self.dialog._parse_thresholds()

    def test_a_non_finite_threshold_is_rejected(self):
        for text in ("inf", "-inf", "Infinity", "nan", "1e309"):
            with self.subTest(text=text):
                self._set_thresholds(text)

                with self.assertRaisesRegex(ValueError, "finite numbers"):
                    self.dialog._parse_thresholds()

    def test_a_non_finite_threshold_is_reported_as_an_input_error(self):
        self.dialog.routes_comboBox.setCurrentText("CalculateIsolines")
        self._set_isoline_center()
        self._set_thresholds("1e309")
        routes = self._mock_routes()

        error, _push_message = self._run_dialog()

        error.assert_called_once()
        _parent, title, text = error.call_args.args
        assert title == "Input Error"
        assert "finite numbers" in text
        routes.request_isolines.assert_not_called()

    def test_fractional_minutes_become_whole_seconds(self):
        self._set_thresholds("1.5")

        assert self.dialog._parse_thresholds() == (90,)

    def test_distance_thresholds_become_meters(self):
        self._set_thresholds("1, 2", threshold_type="Distance")

        assert self.dialog._parse_thresholds() == (1000, 2000)

    def test_isoline_billing_uses_the_rounded_request_thresholds(self):
        cases = (
            ("60.0001", "Time", (3600,)),
            ("100.0001", "Distance", (100_000,)),
        )
        for text, threshold_type, expected in cases:
            with self.subTest(threshold_type=threshold_type):
                self._set_thresholds(text, threshold_type=threshold_type)

                assert self.dialog._parse_thresholds() == expected
                _unit, bucket, _reason = self.dialog._billing_estimate_isolines()
                assert bucket == "Advanced"

    def test_isoline_billing_detects_a_rounded_threshold_over_the_limit(self):
        cases = (
            ("60.01", "Time", (3601,)),
            ("100.0006", "Distance", (100_001,)),
        )
        for text, threshold_type, expected in cases:
            with self.subTest(threshold_type=threshold_type):
                self._set_thresholds(text, threshold_type=threshold_type)

                assert self.dialog._parse_thresholds() == expected
                _unit, bucket, _reason = self.dialog._billing_estimate_isolines()
                assert bucket == "Premium"

    def test_a_grab_region_limits_the_operations_and_options(self):
        self.region.return_value = "ap-southeast-1"
        self.dialog._apply_region_capabilities()

        assert not _item_enabled(self.dialog.routes_comboBox, "CalculateIsolines")
        assert not _item_enabled(self.dialog.routes_comboBox, "SnapToRoads")
        assert not _item_enabled(self.dialog.travelmode_comboBox, "Truck")
        assert self.dialog.alternatives_spinBox.maximum() == 3
        assert not self.dialog.time_arrival_radioButton.isEnabled()
        assert (
            self.dialog.time_arrival_radioButton.toolTip()
            == constants.REGION_DISABLED_TOOLTIP
        )

    def test_leaving_a_grab_region_restores_the_operations_and_options(self):
        self.region.return_value = "ap-southeast-1"
        self.dialog._apply_region_capabilities()

        self.region.return_value = "ap-northeast-1"
        self.dialog._apply_region_capabilities()

        assert _item_enabled(self.dialog.routes_comboBox, "CalculateIsolines")
        assert _item_enabled(self.dialog.routes_comboBox, "SnapToRoads")
        assert _item_enabled(self.dialog.travelmode_comboBox, "Truck")
        assert self.dialog.alternatives_spinBox.maximum() == 5
        assert self.dialog.time_arrival_radioButton.isEnabled()
        assert self.dialog.time_arrival_radioButton.toolTip() == ""

    def test_saving_settings_during_a_run_cancels_the_active_request(self):
        routes = self._mock_routes()
        self.dialog._busy = True

        self.dialog.refresh_region_capabilities()

        assert self.dialog._cancelled
        routes.api_handler.abort.assert_called_once()

    def test_saving_settings_while_idle_cancels_nothing(self):
        routes = self._mock_routes()

        self.dialog.refresh_region_capabilities()

        assert not self.dialog._cancelled
        routes.api_handler.abort.assert_not_called()

    def test_the_matrix_tooltip_shows_the_axis_limits_of_the_region(self):
        tooltip = self.dialog.mx_origins_groupBox.toolTip()

        assert "15 origins" in tooltip
        assert "100 destinations" in tooltip
        assert "matches the AWS Unbounded limit" in tooltip
        assert "not an AWS limit" not in tooltip

    def test_a_grab_region_raises_the_matrix_axis_limits_in_the_tooltip(self):
        self.region.return_value = "ap-southeast-1"
        self.dialog._apply_region_capabilities()

        tooltip = self.dialog.mx_origins_groupBox.toolTip()

        assert "350 origins" in tooltip
        assert "15 origins" not in tooltip
        assert "stricter plugin safety limit in GrabMaps regions" in tooltip

    def test_snap_field_labels_show_the_required_formats_and_units(self):
        assert self.dialog.snap_timestamp_label.text() == "Timestamp (ISO 8601)"
        assert "timezone offset" in self.dialog.snap_timestamp_label.toolTip()
        assert self.dialog.snap_heading_label.text() == "Heading (deg)"
        assert "0 to 360 degrees" in self.dialog.snap_heading_label.toolTip()
        assert self.dialog.snap_speed_label.text() == "Speed (km/h)"
        assert "kilometers per hour" in self.dialog.snap_speed_label.toolTip()

    def test_the_od_lines_checkbox_warns_that_the_lines_are_not_routes(self):
        tooltip = self.dialog.mx_odlines_checkBox.toolTip()

        assert "EPSG:4326" in tooltip
        assert "not road geometry" in tooltip
        assert "antimeridian" in tooltip

    def test_the_waypoint_button_stops_the_collector_when_pressed_again(self):
        self.dialog._collect_waypoints()
        collector = self.dialog._waypoint_collector

        assert self.dialog.canvas.mapTool() is collector
        assert self.dialog.wp_button_click.text() == constants.WAYPOINT_BUTTON_ACTIVE

        self.dialog._collect_waypoints()

        assert self.dialog.canvas.mapTool() is not collector
        assert self.dialog.wp_button_click.text() == constants.WAYPOINT_BUTTON_ADD

    def test_a_run_cancelled_during_the_request_publishes_nothing(self):
        self._set_route_positions()
        routes = self._mock_routes()

        def cancel_during_request(*_args, **_kwargs):
            self.dialog._cancelled = True
            return ROUTE_RESULT

        routes.request_routes.side_effect = cancel_during_request

        error, _push_message = self._run_dialog()

        error.assert_not_called()
        routes.request_routes.assert_called_once()
        assert self._published_layer_names() == []
        assert not self.dialog._busy

    def test_a_run_cancelled_during_the_confirmation_sends_nothing(self):
        self._set_matrix_layers()
        self.dialog.routes_comboBox.setCurrentText("CalculateRouteMatrix")
        routes = self._mock_routes()

        def cancel_and_confirm(*_args, **_kwargs):
            # The confirmation runs a nested event loop, so hiding the dialog
            # or saving settings can cancel the run while it is open.
            self.dialog._cancelled = True
            return QMessageBox.StandardButton.Yes

        with patch.object(QMessageBox, "question", side_effect=cancel_and_confirm):
            error, _push_message = self._run_dialog()

        routes.request_route_matrix.assert_not_called()
        error.assert_not_called()
        assert self._published_layer_names() == []

    def test_declining_the_billing_confirmation_sends_nothing(self):
        self.dialog.routes_comboBox.setCurrentText("CalculateRouteMatrix")
        self._set_matrix_layers()
        routes = self._mock_routes()

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.No
        ) as confirmation:
            error, _push_message = self._run_dialog()

        confirmation.assert_called_once()
        prompt = confirmation.call_args.args[2]
        assert "2 x 2 = 4 billed route calculations" in prompt
        for method in REQUEST_METHODS:
            with self.subTest(request=method):
                getattr(routes, method).assert_not_called()
        error.assert_not_called()
        assert self._published_layer_names() == []
        assert not self.dialog._busy

    def test_a_failed_run_reports_the_function_it_ran(self):
        self._set_route_positions()
        routes = self._mock_routes()
        routes.request_routes.side_effect = RuntimeError("network down")

        error, _push_message = self._run_dialog()

        error.assert_called_once()
        _parent, title, text = error.call_args.args
        assert title == "Routes Error"
        assert "Failed to run CalculateRoutes" in text

    def test_send_request_rejects_credentials_changed_after_the_start(self):
        routes = self._mock_routes()
        handler = routes.configuration_handler
        self._set_route_positions()
        request = self.dialog._build_request("CalculateRoutes")
        handler.get_credentials.return_value = CHANGED_CREDENTIALS

        with self.assertRaisesRegex(ValueError, "region or API key changed"):
            self.dialog._send_request(request)

        routes.request_routes.assert_not_called()

    def test_a_calculate_routes_run_publishes_the_route_layers(self):
        self._set_route_positions()
        self.dialog.travelmode_comboBox.setCurrentText("Transit")
        self.dialog.rt_summary_checkBox.setChecked(True)
        routes = self._mock_routes()
        routes.request_routes.return_value = TRANSIT_ROUTE_RESULT

        error, push_message = self._run_dialog()

        error.assert_not_called()
        routes.request_routes.assert_called_once()
        assert self._published_layer_names() == [
            "CalculateRoutes",
            "CalculateRoutes (route summary)",
        ]
        link = '<a href="https://example.com/metro">Tokyo Metro</a>'
        assert link in self.dialog.attributions_label.text()
        assert any(
            "[Core pricing bucket]" in text
            for text in _messages_at(push_message, SUCCESS)
        )

    def test_a_response_with_more_routes_than_requested_is_rejected(self):
        self._set_route_positions()
        self.dialog.alternatives_spinBox.setValue(0)
        routes = self._mock_routes()
        routes.request_routes.return_value = {"Routes": ROUTE_RESULT["Routes"] * 2}

        error, _push_message = self._run_dialog()

        routes.request_routes.assert_called_once()
        error.assert_called_once()
        _parent, title, text = error.call_args.args
        assert title == "Routes Error"
        assert "more routes than the request allowed" in text
        assert self._published_layer_names() == []

    def test_a_calculate_isolines_run_publishes_the_isoline_layer(self):
        self.dialog.routes_comboBox.setCurrentText("CalculateIsolines")
        self._set_isoline_center()
        self._set_thresholds("5, 10")
        routes = self._mock_routes()
        routes.request_isolines.return_value = _isoline_response((5, 10))

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            error, push_message = self._run_dialog()

        error.assert_not_called()
        routes.request_isolines.assert_called_once()
        assert self._published_layer_names() == ["CalculateIsolines"]
        assert any(
            "(2 isolines)" in text for text in _messages_at(push_message, SUCCESS)
        )

    def test_a_snap_to_roads_run_publishes_the_line_and_the_points(self):
        self.dialog.routes_comboBox.setCurrentText("SnapToRoads")
        self._set_snap_layer()
        routes = self._mock_routes()
        routes.request_snap_to_roads.return_value = _snap_response(TRACE_POSITIONS)

        error, _push_message = self._run_dialog()

        error.assert_not_called()
        routes.request_snap_to_roads.assert_called_once()
        assert self._published_layer_names() == [
            "SnapToRoads",
            "SnapToRoads (confidence points)",
        ]

    def test_a_route_matrix_run_publishes_the_table_and_the_od_lines(self):
        self.dialog.routes_comboBox.setCurrentText("CalculateRouteMatrix")
        self._set_matrix_layers()
        self.dialog.mx_odlines_checkBox.setChecked(True)
        routes = self._mock_routes()
        routes.request_route_matrix.return_value = MATRIX_RESULT

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            error, _push_message = self._run_dialog()

        error.assert_not_called()
        routes.request_route_matrix.assert_called_once()
        assert self._published_layer_names() == [
            "CalculateRouteMatrix",
            "CalculateRouteMatrix (OD straight lines)",
        ]

    def test_a_failed_matrix_pair_is_reported_and_gets_no_od_line(self):
        self.dialog.routes_comboBox.setCurrentText("CalculateRouteMatrix")
        self._set_matrix_layers()
        self.dialog.mx_odlines_checkBox.setChecked(True)
        routes = self._mock_routes()
        routes.request_route_matrix.return_value = MATRIX_ERROR_RESULT

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            error, push_message = self._run_dialog()

        error.assert_not_called()
        assert self._published_layer_names() == [
            "CalculateRouteMatrix",
            "CalculateRouteMatrix (OD straight lines)",
        ]
        # The table keeps all four pairs; only the three that have a route
        # get a straight line.
        assert self._published_layer("CalculateRouteMatrix").featureCount() == 4
        lines = self._published_layer("CalculateRouteMatrix (OD straight lines)")
        assert lines.featureCount() == 3
        assert any(
            "returned 1 cell error(s)" in text
            for text in _messages_at(push_message, WARNING)
        )

    def test_the_matrix_origins_stop_at_the_axis_limit_of_the_region(self):
        self.dialog.routes_comboBox.setCurrentText("CalculateRouteMatrix")
        self._set_matrix_layer_sizes(origins=16, destinations=2)

        with self.assertRaisesRegex(
            ValueError, "Origins: the layer provides more than 15 usable points"
        ):
            self.dialog._build_matrix_request()

    def test_the_matrix_destination_limit_follows_the_origin_count(self):
        # 10 origins leave room for only 100 // 10 destinations before the
        # 100-pair cap, so the eleventh destination stops the scan.
        self.dialog.routes_comboBox.setCurrentText("CalculateRouteMatrix")
        self._set_matrix_layer_sizes(origins=10, destinations=11)

        with self.assertRaisesRegex(
            ValueError, "Destinations: the layer provides more than 10 usable points"
        ):
            self.dialog._build_matrix_request()

    def test_a_matrix_over_the_limits_is_reported_as_an_input_error(self):
        self.dialog.routes_comboBox.setCurrentText("CalculateRouteMatrix")
        self._set_matrix_layer_sizes(origins=10, destinations=11)
        routes = self._mock_routes()

        error, _push_message = self._run_dialog()

        error.assert_called_once()
        _parent, title, text = error.call_args.args
        assert title == "Input Error"
        assert "Destinations: the layer provides more than 10 usable points" in text
        routes.request_route_matrix.assert_not_called()

    def test_a_matrix_pair_over_10000_km_is_not_sent(self):
        self.dialog.routes_comboBox.setCurrentText("CalculateRouteMatrix")
        origins = self._project_layer("far matrix origin", ((0.0, 0.0),))
        destinations = self._project_layer("far matrix destination", ((100.0, 0.0),))
        self.dialog.mx_origins_comboBox.setLayer(origins)
        self.dialog.mx_dest_comboBox.setLayer(destinations)
        routes = self._mock_routes()

        with patch.object(QMessageBox, "question") as confirmation:
            error, _push_message = self._run_dialog()

        error.assert_called_once()
        _parent, title, text = error.call_args.args
        assert title == "Input Error"
        assert "10,000 km" in text
        confirmation.assert_not_called()
        routes.request_route_matrix.assert_not_called()

    def test_a_snap_layer_without_a_valid_crs_is_not_sent(self):
        self.dialog.routes_comboBox.setCurrentText("SnapToRoads")
        layer = self._set_snap_layer()
        layer.setCrs(QgsCoordinateReferenceSystem())
        routes = self._mock_routes()

        error, _push_message = self._run_dialog()

        error.assert_called_once()
        _parent, title, text = error.call_args.args
        assert title == "Input Error"
        assert "layer has no valid CRS" in text
        routes.request_snap_to_roads.assert_not_called()

    def test_a_failed_publish_restores_the_previously_active_layer(self):
        previous = self._project_layer("previously active", TRACE_POSITIONS)

        iface, error, _push_message = self._run_with_failed_publish(previous)

        error.assert_called_once()
        iface.setActiveLayer.assert_called_once_with(previous)
        iface.layerTreeView.assert_not_called()
        assert self._published_layer_names() == []

    def test_a_failed_publish_clears_the_active_layer_when_there_was_none(self):
        iface, error, _push_message = self._run_with_failed_publish(None)

        error.assert_called_once()
        # setActiveLayer(None) does not clear the active layer on QGIS 3.34,
        # so an empty layer tree selection is what clears it instead.
        iface.setActiveLayer.assert_not_called()
        view = iface.layerTreeView.return_value
        view.setCurrentIndex.assert_called_once()
        (index,) = view.setCurrentIndex.call_args.args
        assert isinstance(index, QModelIndex)
        assert not index.isValid()

    def test_a_failed_publish_reports_the_bucket_it_was_billed_at(self):
        previous = self._project_layer("previously active", TRACE_POSITIONS)

        iface, error, push_message = self._run_with_failed_publish(previous)

        error.assert_called_once()
        iface.setActiveLayer.assert_called_once_with(previous)
        assert any(
            "used the Core pricing bucket" in text
            for text in _messages_at(push_message, INFO)
        )

    def test_a_snap_response_that_does_not_match_the_request_is_rejected(self):
        self.dialog.routes_comboBox.setCurrentText("SnapToRoads")
        self._set_snap_layer()
        # The index check must run even when the confidence points are off.
        self.dialog.snap_confidence_checkBox.setChecked(False)
        self._mock_routes()
        broken = {"SnappedTracePoints": _snapped_points(TRACE_POSITIONS[:1])}

        with self._patch_send_request(broken):
            error, _push_message = self._run_dialog()

        error.assert_called_once()
        _parent, title, text = error.call_args.args
        assert title == "Routes Error"
        assert "no layer was created" in text
        assert self._published_layer_names() == []

    def test_a_snap_response_without_a_line_publishes_nothing(self):
        self.dialog.routes_comboBox.setCurrentText("SnapToRoads")
        self._set_snap_layer()
        self._mock_routes()
        response = _snap_response(TRACE_POSITIONS, with_line=False)

        with self._patch_send_request(response):
            error, push_message = self._run_dialog()

        error.assert_not_called()
        assert self._published_layer_names() == []
        assert any(
            "no drawable results" in text for text in _messages_at(push_message, INFO)
        )
        assert _messages_at(push_message, WARNING) == []

    def test_a_response_without_drawable_results_still_reports_the_bucket(self):
        self._set_route_positions()
        self._mock_routes(bucket="Core")

        with self._patch_send_request({"Routes": []}):
            error, push_message = self._run_dialog()

        error.assert_not_called()
        assert self._published_layer_names() == []
        info = _messages_at(push_message, INFO)
        assert any("no drawable results" in text for text in info)
        assert any("[Core pricing bucket]" in text for text in info)

    def test_a_broken_response_still_reports_the_bucket_that_was_billed(self):
        self.dialog.routes_comboBox.setCurrentText("CalculateRouteMatrix")
        self._set_matrix_layers()
        routes = self._mock_routes(bucket="Core")
        routes.request_route_matrix.return_value = BROKEN_MATRIX_RESULT

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            error, push_message = self._run_dialog()

        routes.request_route_matrix.assert_called_once()
        error.assert_called_once()
        _parent, _title, text = error.call_args.args
        assert "route matrix size does not match" in text
        assert self._published_layer_names() == []
        assert any(
            "used the Core pricing bucket" in text
            for text in _messages_at(push_message, INFO)
        )

    def test_an_unreadable_body_after_http_200_still_reports_the_bucket(self):
        # The pricing header arrives with the HTTP 200, so the request is
        # billed even when its body cannot be read at all.
        self._set_route_positions()
        routes = self._mock_routes()

        def bill_then_fail(*_args, **_kwargs):
            routes.api_handler.last_pricing_bucket = "RoutesCore"
            raise ApiError("invalid response", status_code=200)

        routes.request_routes.side_effect = bill_then_fail

        error, push_message = self._run_dialog()

        error.assert_called_once()
        _parent, title, text = error.call_args.args
        assert title == "Routes Error"
        assert "invalid response" in text
        assert any(
            "used the RoutesCore pricing bucket" in message
            for message in _messages_at(push_message, INFO)
        )

    def test_a_request_that_was_never_billed_reports_no_bucket(self):
        # The bucket is cleared before the request is sent, so the bucket a
        # previous run left behind is not reported for this failure.
        self._set_route_positions()
        routes = self._mock_routes()
        routes.request_routes.side_effect = RuntimeError("network down")

        error, push_message = self._run_dialog()

        error.assert_called_once()
        assert _messages_at(push_message, INFO) == []

    def test_a_run_without_routes_clears_the_previous_attributions(self):
        self._set_route_positions()
        self.dialog._show_attributions(
            [{"type": "", "description": "Tokyo Metro", "url": ""}]
        )
        self._mock_routes()

        with self._patch_send_request({"Routes": []}):
            error, _push_message = self._run_dialog()

        error.assert_not_called()
        assert self._published_layer_names() == []
        assert self.dialog.attributions_label.text() == constants.NO_ATTRIBUTIONS_TEXT

    def test_the_dialog_has_no_persistent_billing_label(self):
        assert not hasattr(self.dialog, "billing_label")

    def test_intermodal_requires_premium_billing_confirmation(self):
        self._set_route_positions()
        self.dialog.travelmode_comboBox.setCurrentText("Intermodal")
        routes = self._mock_routes()

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.No
        ) as confirmation:
            error, _push_message = self._run_dialog()

        confirmation.assert_called_once()
        prompt = confirmation.call_args.args[2]
        assert "estimated Premium pricing bucket" in prompt
        assert "Intermodal travel mode" in prompt
        routes.request_routes.assert_not_called()
        error.assert_not_called()

    def test_two_far_apart_trace_points_are_estimated_as_premium(self):
        unit, bucket, reason = self.dialog._snap_billing_from_request(
            _snap_request(FAR_TRACE)
        )

        assert unit == "1 request (2 trace points)"
        assert bucket == "Premium"
        assert "trace points more than 100 km apart" in reason

    def test_trace_points_spread_over_100_km_are_estimated_as_premium(self):
        # Every adjacent pair stays under 100 km, so only a check of every
        # pair sees that the trace as a whole is too wide.
        unit, bucket, reason = self.dialog._snap_billing_from_request(
            _snap_request(SPREAD_TRACE)
        )

        assert unit == "1 request (3 trace points)"
        assert bucket == "Premium"
        assert "trace points more than 100 km apart" in reason

    def test_a_trace_across_the_antimeridian_is_measured_by_distance(self):
        for trace, expected in (
            (ANTIMERIDIAN_TRACE, "Premium"),
            (NEAR_ANTIMERIDIAN_TRACE, "Advanced"),
        ):
            with self.subTest(trace=trace):
                _unit, bucket, _reason = self.dialog._snap_billing_from_request(
                    _snap_request(trace)
                )

                assert bucket == expected

    def test_nearby_trace_points_are_estimated_as_advanced(self):
        _unit, bucket, _reason = self.dialog._snap_billing_from_request(
            _snap_request(NEAR_TRACE)
        )

        assert bucket == "Advanced"

    def test_a_scooter_trace_is_estimated_as_premium(self):
        _unit, bucket, reason = self.dialog._snap_billing_from_request(
            _snap_request(NEAR_TRACE, travel_mode="Scooter")
        )

        assert bucket == "Premium"
        assert reason == "Scooter travel mode"

    def test_more_than_200_trace_points_are_estimated_as_premium(self):
        trace = tuple((139.70 + index * 0.0001, 35.60) for index in range(201))

        unit, bucket, reason = self.dialog._snap_billing_from_request(
            _snap_request(trace)
        )

        assert unit == "1 request (201 trace points)"
        assert bucket == "Premium"
        assert "more than 200 trace points" in reason

    def test_the_attributions_use_the_description_as_the_link_text(self):
        self.dialog._show_attributions(
            [
                {
                    "type": "TransitOperator",
                    "description": "Metro & Rail",
                    "url": "https://example.com/?a&b",
                }
            ]
        )

        link = '<a href="https://example.com/?a&amp;b">Metro &amp; Rail</a>'
        assert link in self.dialog.attributions_label.text()

    def test_an_attribution_url_that_is_not_a_web_link_stays_plain_text(self):
        for url in ("file:///etc/passwd", "example.com/metro"):
            with self.subTest(url=url):
                self.dialog._show_attributions(
                    [{"type": "", "description": "Metro", "url": url}]
                )

                text = self.dialog.attributions_label.text()
                assert "<a href" not in text
                assert f"Metro ({url})" in text


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestOrderSortKey(unittest.TestCase):
    """A layer order field can mix numbers, text and NULL without a TypeError."""

    def test_numbers_sort_before_text_and_null_sorts_last(self):
        values = [None, "b", 2, "a", 1, True]

        ordered = sorted(values, key=routes_module._order_sort_key)

        assert ordered == [1, 2, True, "a", "b", None]

    def test_a_boolean_sorts_as_text_instead_of_as_a_number(self):
        group = routes_module._order_sort_key(True)[0]

        assert group == routes_module._order_sort_key("x")[0]
        assert group != routes_module._order_sort_key(1)[0]

    def test_a_nan_order_sorts_in_the_null_group(self):
        nan_key = routes_module._order_sort_key(float("nan"))

        assert nan_key == routes_module._order_sort_key(None)
        assert nan_key[0] != routes_module._order_sort_key(1)[0]


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestLayerInputRows(unittest.TestCase):
    """The layer points are copied into request rows before anything is sent."""

    def test_a_multipoint_feature_becomes_one_row_per_part(self):
        layer = _point_layer(geometry="MultiPoint")
        _add_point(layer, "MultiPoint ((139.70 35.60),(139.80 35.70))")

        rows = _rows(layer)

        assert [row["part"] for row in rows] == [0, 1]
        assert _positions(rows) == [[139.7, 35.6], [139.8, 35.7]]

    def test_a_multipoint_is_read_without_building_a_list_of_all_parts(self):
        layer = _point_layer(geometry="MultiPoint")
        _add_point(layer, "MultiPoint ((139.70 35.60),(139.80 35.70))")

        with patch.object(
            QgsGeometry,
            "asMultiPoint",
            side_effect=AssertionError("asMultiPoint must not be called"),
        ):
            rows = _rows(layer)

        assert _positions(rows) == [[139.7, 35.6], [139.8, 35.7]]

    def test_a_layer_without_a_valid_crs_is_rejected(self):
        layer = _point_layer()
        _add_point(layer, "Point (139.70 35.60)")
        layer.setCrs(QgsCoordinateReferenceSystem())

        with self.assertRaisesRegex(ValueError, "layer has no valid CRS"):
            _rows(layer)

    def test_a_layer_with_an_unusable_transform_is_rejected(self):
        layer = _point_layer()
        _add_point(layer, "Point (139.70 35.60)")
        transform = Mock()
        transform.isValid.return_value = False

        with (
            patch.object(routes_module, "wgs84_transform", return_value=transform),
            self.assertRaisesRegex(ValueError, "cannot be transformed to WGS 84"),
        ):
            _rows(layer)

    def test_a_point_that_cannot_be_transformed_is_rejected(self):
        layer = _point_layer()
        _add_point(layer, "Point (139.70 35.60)")
        transform = Mock()
        transform.isValid.return_value = True
        transform.transform.side_effect = QgsCsException("transform failed")

        with (
            patch.object(routes_module, "wgs84_transform", return_value=transform),
            self.assertRaisesRegex(ValueError, "point could not be transformed"),
        ):
            _rows(layer, name="Trace points")

    def test_a_null_id_falls_back_to_the_feature_id(self):
        layer = _point_layer("field=trace_id:string")
        _add_point(layer, "Point (139.70 35.60)", {"trace_id": "start"})
        second = _add_point(layer, "Point (139.80 35.70)")

        rows = _rows(layer, id_field="trace_id")

        assert [row["id"] for row in rows] == ["start", str(second)]

    def test_mixed_order_values_sort_numbers_first_and_null_last(self):
        layer = _point_layer("field=seq:integer")
        first = _add_point(layer, "Point (139.70 35.60)", {"seq": 10})
        _add_point(layer, "Point (139.71 35.61)", {"seq": 2})
        _add_point(layer, "Point (139.72 35.62)")
        # An uncommitted edit can hold a value the field type does not accept,
        # so one layer really can mix numbers, text and NULL order values.
        layer.startEditing()
        layer.changeAttributeValue(first, layer.fields().indexOf("seq"), "text")

        rows = _rows(layer, order_field="seq")

        assert [row["order"] for row in rows] == [2, "text", None]

    def test_a_nan_order_value_becomes_null(self):
        layer = _point_layer("field=seq:double")
        _add_point(layer, "Point (139.70 35.60)", {"seq": float("nan")})
        _add_point(layer, "Point (139.71 35.61)", {"seq": 2.0})

        rows = _rows(layer, order_field="seq")

        assert [row["order"] for row in rows] == [2.0, None]

    def test_using_the_selection_requires_a_selected_feature(self):
        layer = _point_layer()
        _add_point(layer, "Point (139.70 35.60)")

        with self.assertRaisesRegex(ValueError, "Origins: no features are selected"):
            _rows(layer, selected_only=True, name="Origins")

    def test_a_missing_layer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Origins: select a point layer"):
            _rows(None, name="Origins")

    def test_a_non_point_layer_is_rejected(self):
        layer = QgsVectorLayer("LineString?crs=EPSG:4326", "lines", "memory")

        with self.assertRaisesRegex(ValueError, "Origins: select a point layer"):
            _rows(layer, name="Origins")

    def test_a_trace_layer_over_the_point_limit_is_rejected(self):
        layer = _fill_points(_point_layer(), MAX_TRACE_POINTS + 1)

        with self.assertRaisesRegex(
            ValueError, "Trace: the layer provides more than 5000 usable points"
        ):
            _rows(layer, name="Trace")

    def test_the_scan_stops_at_the_limit_instead_of_reading_the_layer(self):
        layer = _counting_layer(50)

        with self.assertRaisesRegex(ValueError, "more than 5 usable points"):
            _rows(layer, max_rows=5)

        # The point that exceeds the limit stops the scan, so the rest of the
        # layer is never read.
        assert layer.scanned == 6

    def test_the_scan_of_a_selection_also_stops_at_the_limit(self):
        # The selection is read through its own iterator, which stops at the
        # limit the same way.
        layer = _counting_layer(50)
        layer.selectAll()

        with self.assertRaisesRegex(ValueError, "more than 5 usable points"):
            _rows(layer, selected_only=True, max_rows=5)

        assert layer.scanned == 6

    def test_the_limit_also_counts_the_parts_of_one_multipoint(self):
        layer = _point_layer(geometry="MultiPoint")
        _add_point(layer, "MultiPoint ((139.70 35.60),(139.71 35.61),(139.72 35.62))")

        with self.assertRaisesRegex(ValueError, "more than 2 usable points"):
            _rows(layer, max_rows=2)


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestMultiPointCollectorTool(unittest.TestCase):
    """The waypoint collector restores the map tool it replaced."""

    def setUp(self):
        self.canvas = QgsMapCanvas()
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        self.previous_tool = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self.previous_tool)

    def tearDown(self):
        current_tool = self.canvas.mapTool()
        if current_tool is not None:
            self.canvas.unsetMapTool(current_tool)
        self.canvas.deleteLater()

    def test_arming_the_collector_replaces_and_then_restores_the_map_tool(self):
        collector = MultiPointCollector(self.canvas)

        collector.arm()
        assert self.canvas.mapTool() is collector

        collector.disarm()
        assert self.canvas.mapTool() is self.previous_tool


if __name__ == "__main__":
    unittest.main()
