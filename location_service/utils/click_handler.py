from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
)
from qgis.gui import QgsMapCanvas, QgsMapMouseEvent, QgsMapTool
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QLineEdit

try:
    _LEFT_BUTTON = Qt.LeftButton
    _RIGHT_BUTTON = Qt.RightButton
except AttributeError:
    _LEFT_BUTTON = Qt.MouseButton.LeftButton
    _RIGHT_BUTTON = Qt.MouseButton.RightButton

WGS84_CRS = "EPSG:4326"


def parse_lonlat(lon_text: str, lat_text: str) -> list[float] | None:
    """Returns a finite in-range ``[lon, lat]`` pair, or ``None``."""
    lon_text = lon_text.strip()
    lat_text = lat_text.strip()
    if not lon_text or not lat_text:
        return None
    try:
        lon, lat = float(lon_text), float(lat_text)
    except ValueError:
        return None
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        return None
    return [lon, lat]


def transform_to_wgs84(
    map_point: QgsPointXY, source_crs: QgsCoordinateReferenceSystem
) -> QgsPointXY:
    """Converts a map point from the canvas CRS to WGS84."""
    project = QgsProject.instance()
    wgs84_crs = QgsCoordinateReferenceSystem(WGS84_CRS)
    transform = QgsCoordinateTransform(source_crs, wgs84_crs, project)
    return transform.transform(map_point)


class MapClickCoordinateUpdater(QgsMapTool):
    """Writes one map click to a longitude/latitude input pair."""

    def __init__(
        self,
        canvas: QgsMapCanvas,
        lon_edit: QLineEdit,
        lat_edit: QLineEdit,
    ) -> None:
        """Initializes the picker for the target coordinate fields."""
        super().__init__(canvas)
        self.lon_edit = lon_edit
        self.lat_edit = lat_edit
        self._previous_tool: QgsMapTool | None = None

    def arm(self) -> None:
        """Activates the picker and remembers the current map tool."""
        canvas = self.canvas()
        if canvas is None:
            return
        current = canvas.mapTool()
        if isinstance(current, MapClickCoordinateUpdater):
            # Preserve the original map tool when replacing an active picker.
            self._previous_tool = current._previous_tool
        else:
            self._previous_tool = current
        canvas.setMapTool(self)

    def disarm(self) -> None:
        """Deactivates the picker and restores the previous map tool."""
        canvas = self.canvas()
        if canvas is None or canvas.mapTool() is not self:
            return
        previous = self._previous_tool
        self._previous_tool = None
        if previous is not None and not sip.isdeleted(previous):
            canvas.setMapTool(previous)
        else:
            canvas.unsetMapTool(self)

    def canvasReleaseEvent(self, e: QgsMapMouseEvent) -> None:
        """
        Converts a left click to WGS84 and writes it into the target line
        edits; a right click cancels the pick without writing anything.

        Handled on release, not press: disarming restores the previous map
        tool, and doing that on press would deliver the release half of the
        same click to the restored tool (e.g. triggering an Identify).
        """
        if e.button() == _RIGHT_BUTTON:
            self.disarm()
            return
        if e.button() != _LEFT_BUTTON:
            return
        if sip.isdeleted(self.lon_edit) or sip.isdeleted(self.lat_edit):
            self.disarm()
            return
        canvas = self.canvas()
        if canvas is None:
            return
        map_point = self.toMapCoordinates(e.pos())
        source_crs = canvas.mapSettings().destinationCrs()
        wgs84_point = transform_to_wgs84(map_point, source_crs)
        self.lon_edit.setText(str(wgs84_point.x()))
        self.lat_edit.setText(str(wgs84_point.y()))
        self.disarm()


def release_map_tool(tool: MapClickCoordinateUpdater | None) -> None:
    """Disarms a picker that has not already been deleted by QGIS."""
    if tool is not None and not sip.isdeleted(tool):
        tool.disarm()
