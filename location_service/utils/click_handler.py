from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsPointXY,
    QgsProject,
)
from qgis.gui import QgsMapCanvas, QgsMapMouseEvent, QgsMapTool
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QLineEdit

from .feedback import WARNING, push_message

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


def wgs84_transform(source_crs: QgsCoordinateReferenceSystem) -> QgsCoordinateTransform:
    """Returns a reusable transform from the source CRS to EPSG:4326 (WGS 84)."""
    wgs84_crs = QgsCoordinateReferenceSystem(WGS84_CRS)
    return QgsCoordinateTransform(source_crs, wgs84_crs, QgsProject.instance())


def transform_to_wgs84(
    map_point: QgsPointXY, source_crs: QgsCoordinateReferenceSystem
) -> QgsPointXY:
    """Converts a map point from the canvas CRS to EPSG:4326 (WGS 84)."""
    if not source_crs.isValid():
        raise ValueError("The map CRS is missing or invalid.")
    transform = wgs84_transform(source_crs)
    if not transform.isValid():
        raise ValueError("The map CRS cannot be transformed to WGS 84.")
    try:
        return transform.transform(map_point)
    except QgsCsException as error:
        raise ValueError(
            "The clicked location cannot be transformed to WGS 84."
        ) from error


class RestoringMapTool(QgsMapTool):
    """Map tool that restores the tool it replaced when it is disarmed."""

    def __init__(self, canvas: QgsMapCanvas) -> None:
        """Initializes the tool without activating it."""
        super().__init__(canvas)
        self._previous_tool: QgsMapTool | None = None

    def arm(self) -> None:
        """Activates the tool and remembers the current map tool."""
        canvas = self.canvas()
        if canvas is None:
            return
        current = canvas.mapTool()
        if isinstance(current, RestoringMapTool):
            # Preserve the original map tool when replacing an active picker.
            self._previous_tool = current._previous_tool
        else:
            self._previous_tool = current
        canvas.setMapTool(self)

    def disarm(self) -> None:
        """Deactivates the tool and restores the previous map tool."""
        canvas = self.canvas()
        if canvas is None or canvas.mapTool() is not self:
            return
        previous = self._previous_tool
        self._previous_tool = None
        if previous is not None and not sip.isdeleted(previous):
            canvas.setMapTool(previous)
        else:
            canvas.unsetMapTool(self)


class MapClickCoordinateUpdater(RestoringMapTool):
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

    def canvasReleaseEvent(self, e: QgsMapMouseEvent) -> None:
        """
        Writes a left click as WGS 84 coordinates; a right click cancels.

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
        try:
            wgs84_point = transform_to_wgs84(map_point, source_crs)
        except ValueError as error:
            push_message(WARNING, str(error))
            self.disarm()
            return
        self.lon_edit.setText(str(wgs84_point.x()))
        self.lat_edit.setText(str(wgs84_point.y()))
        self.disarm()


class MultiPointCollector(RestoringMapTool):
    """
    Collects a series of left clicks as WGS 84 points.

    Each left click emits ``point_collected``; a right click stops the
    collection, restores the previous map tool and emits
    ``collection_finished``. Handled on release for the same reason as
    ``MapClickCoordinateUpdater``.
    """

    point_collected = pyqtSignal(QgsPointXY)
    collection_finished = pyqtSignal()

    def canvasReleaseEvent(self, e: QgsMapMouseEvent) -> None:
        """Emits one collected point per left click until a right click."""
        if e.button() == _RIGHT_BUTTON:
            self.disarm()
            self.collection_finished.emit()
            return
        if e.button() != _LEFT_BUTTON:
            return
        canvas = self.canvas()
        if canvas is None:
            return
        map_point = self.toMapCoordinates(e.pos())
        source_crs = canvas.mapSettings().destinationCrs()
        try:
            point = transform_to_wgs84(map_point, source_crs)
        except ValueError as error:
            push_message(WARNING, str(error))
            self.disarm()
            return
        self.point_collected.emit(point)


def release_map_tool(tool: RestoringMapTool | None) -> None:
    """Disarms a picker that has not already been deleted by QGIS."""
    if tool is not None and not sip.isdeleted(tool):
        tool.disarm()
