from __future__ import annotations

from typing import Any
from urllib.parse import quote

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsSimpleLineSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsSymbol,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from ..utils.configuration_handler import ConfigurationHandler
from ..utils.external_api_handler import ExternalApiHandler


def major_road_names(route: dict[str, Any]) -> str:
    """
    Joins major-road labels, preferring ``RoadName`` over ``RouteNumber``.

    Each nested value may be absent or null.
    """
    names = []
    for label in route.get("MajorRoadLabels") or []:
        label = label or {}
        road_name = (label.get("RoadName") or {}).get("Value")
        route_number = (label.get("RouteNumber") or {}).get("Value")
        if road_name or route_number:
            names.append(road_name or route_number)
    return ", ".join(names)


class RoutesFunctions:
    """Calculates routes and creates their line layer."""

    WGS84_CRS = "EPSG:4326"
    LAYER_TYPE = "LineString"
    FIELD_ROADNAME = "RoadName"
    LINE_COLOR = QColor(255, 0, 0)
    LINE_WIDTH = 2.0

    def __init__(self) -> None:
        """Initializes the configuration and API handlers."""
        self.configuration_handler = ConfigurationHandler()
        self.api_handler = ExternalApiHandler()

    def calculate_routes(
        self,
        st_lon: float,
        st_lat: float,
        ed_lon: float,
        ed_lat: float,
        credentials: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Calculates routes between the supplied WGS84 coordinates."""
        if credentials is None:
            credentials = self.configuration_handler.get_credentials()
        region, apikey = credentials
        routes_url = (
            f"https://routes.geo.{region}.amazonaws.com/v2/routes"
            f"?key={quote(apikey, safe='')}"
        )
        data = {
            "Origin": [st_lon, st_lat],
            "Destination": [ed_lon, ed_lat],
            "LegGeometryFormat": "Simple",
        }
        return self.api_handler.send_json_post_request(routes_url, data)

    def add_line_layer(self, data: dict[str, Any]) -> None:
        """Adds route results to the current project as a line layer."""
        layer = QgsVectorLayer(
            f"{self.LAYER_TYPE}?crs={self.WGS84_CRS}", "CalculateRoutes", "memory"
        )
        self.setup_layer(layer, data)

    def setup_layer(self, layer: QgsVectorLayer, data: dict[str, Any]) -> None:
        """Populates, styles, and adds the line layer to the project."""
        self.add_attributes(layer)
        self.add_features(layer, data)
        self.apply_layer_style(layer)
        layer.triggerRepaint()
        QgsProject.instance().addMapLayer(layer)

    def add_attributes(self, layer: QgsVectorLayer) -> None:
        """Adds the road-name field to the layer."""
        fields = QgsFields()
        fields.append(QgsField(self.FIELD_ROADNAME, QVariant.String))
        layer.dataProvider().addAttributes(fields)
        layer.updateFields()

    def add_features(self, layer: QgsVectorLayer, data: dict) -> None:
        """Adds one line feature for each route leg."""
        features = []
        for route in data.get("Routes", []):
            roadname = major_road_names(route)
            for leg in route.get("Legs", []):
                line_points = [
                    QgsPointXY(coord[0], coord[1])
                    for coord in leg["Geometry"]["LineString"]
                ]
                geometry = QgsGeometry.fromPolylineXY(line_points)
                feature = QgsFeature(layer.fields())
                feature.setAttributes([roadname])
                feature.setGeometry(geometry)
                features.append(feature)
        layer.dataProvider().addFeatures(features)

    def apply_layer_style(self, layer: QgsVectorLayer) -> None:
        """Applies the route line symbol to the layer."""
        symbol_layer = QgsSimpleLineSymbolLayer()
        symbol_layer.setColor(self.LINE_COLOR)
        symbol_layer.setWidth(self.LINE_WIDTH)
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.changeSymbolLayer(0, symbol_layer)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
