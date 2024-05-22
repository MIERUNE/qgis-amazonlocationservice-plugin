from typing import Dict, Tuple, Any
from PyQt5.QtCore import QVariant
from PyQt5.QtGui import QColor
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFields,
    QgsField,
    QgsPointXY,
    QgsFeature,
    QgsGeometry,
    QgsSimpleLineSymbolLayer,
    QgsSymbol,
    QgsSingleSymbolRenderer,
)
from ..utils.configuration_handler import ConfigurationHandler
from ..utils.external_api_handler import ExternalApiHandler


class RoutesFunctions:
    """
    Manages the calculation and visualization of routes between two points on a map.
    """

    KEY_REGION = "region_value"
    KEY_ROUTES = "routes_value"
    KEY_APIKEY = "apikey_value"
    WGS84_CRS = "EPSG:4326"
    LAYER_TYPE = "LineString"
    FIELD_DISTANCE = "Distance"
    FIELD_DURATION = "DurationSec"
    LINE_COLOR = QColor(255, 0, 0)
    LINE_WIDTH = 2.0

    def __init__(self) -> None:
        """
        Initializes the RoutesFunctions class with configuration and API handlers.
        """
        self.configuration_handler = ConfigurationHandler()
        self.api_handler = ExternalApiHandler()

    def get_configuration_settings(self) -> Tuple[str, str, str]:
        """
        Fetches necessary configuration settings from the settings manager.

        Returns:
            Tuple[str, str, str]: A tuple containing the region,
            route calculator name, and API key.
        """
        region = self.configuration_handler.get_setting(self.KEY_REGION)
        routes = self.configuration_handler.get_setting(self.KEY_ROUTES)
        apikey = self.configuration_handler.get_setting(self.KEY_APIKEY)
        return region, routes, apikey

    def calculate_route(
        self, st_lon: float, st_lat: float, ed_lon: float, ed_lat: float
    ) -> Dict[str, Any]:
        """
        Calculates a route from start to end coordinates using an external API.

        Args:
            st_lon (float): Longitude of the start position.
            st_lat (float): Latitude of the start position.
            ed_lon (float): Longitude of the end position.
            ed_lat (float): Latitude of the end position.

        Returns:
            A dictionary containing the calculated route data.
        """
        region, routes, apikey = self.get_configuration_settings()
        routes_url = (
            f"https://routes.geo.{region}.amazonaws.com/routes/v0/calculators/"
            f"{routes}/calculate/route?key={apikey}"
        )
        data = {
            "DeparturePosition": [st_lon, st_lat],
            "DestinationPosition": [ed_lon, ed_lat],
            "IncludeLegGeometry": "true",
        }
        result = self.api_handler.send_json_post_request(routes_url, data)
        if result is None:
            raise ValueError("Failed to receive a valid response from the API.")
        return result

    def add_line_layer(self, data: Dict[str, Any]) -> None:
        """
        Adds a line layer to the QGIS project based on route data provided.

        Args:
            data (Dict): Route data including the route legs and geometry.
        """
        routes = self.configuration_handler.get_setting(self.KEY_ROUTES)
        layer = QgsVectorLayer(
            f"{self.LAYER_TYPE}?crs={self.WGS84_CRS}", routes, "memory"
        )
        self.setup_layer(layer, data)

    def setup_layer(self, layer: QgsVectorLayer, data: Dict[str, Any]) -> None:
        """
        Configures the given layer with attributes, features,
        and styling based on route data.

        Args:
            layer (QgsVectorLayer): The vector layer to be configured.
            data (Dict): Route data used to populate the layer.
        """
        self.add_attributes(layer)
        self.add_features(layer, data)
        self.apply_layer_style(layer)
        layer.triggerRepaint()
        QgsProject.instance().addMapLayer(layer)

    def add_attributes(self, layer: QgsVectorLayer) -> None:
        """
        Adds necessary fields to the vector layer.

        Args:
            layer (QgsVectorLayer): The layer to which fields are added.
        """
        fields = QgsFields()
        fields.append(QgsField(self.FIELD_DISTANCE, QVariant.Double))
        fields.append(QgsField(self.FIELD_DURATION, QVariant.Int))
        layer.dataProvider().addAttributes(fields)
        layer.updateFields()

    def add_features(self, layer: QgsVectorLayer, data: Dict[str, Any]) -> None:
        """
        Adds features to the layer based on the route data.

        Args:
            layer (QgsVectorLayer): The layer to which features are added.
            data (Dict): The route data containing legs and geometry.
        """
        features = []
        for leg in data["Legs"]:
            line_points = [
                QgsPointXY(coord[0], coord[1])
                for coord in leg["Geometry"]["LineString"]
            ]
            geometry = QgsGeometry.fromPolylineXY(line_points)
            feature = QgsFeature(layer.fields())
            feature.setGeometry(geometry)
            feature.setAttributes([leg["Distance"], leg["DurationSeconds"]])
            features.append(feature)
        layer.dataProvider().addFeatures(features)

    def apply_layer_style(self, layer: QgsVectorLayer) -> None:
        """
        Applies styling to the layer to visually differentiate it.

        Args:
            layer (QgsVectorLayer): The layer to be styled.
        """
        symbol_layer = QgsSimpleLineSymbolLayer()
        symbol_layer.setColor(self.LINE_COLOR)
        symbol_layer.setWidth(self.LINE_WIDTH)
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.changeSymbolLayer(0, symbol_layer)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
