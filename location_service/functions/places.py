from typing import Any
from urllib.parse import quote

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsSimpleMarkerSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from ..utils.configuration_handler import ConfigurationHandler
from ..utils.external_api_handler import ExternalApiHandler


class PlacesFunctions:
    """Searches for places and creates their point layer."""

    PLACES_LANGUAGE = None
    PLACES_MAX_RESULTS = 10
    WGS84_CRS = "EPSG:4326"
    LAYER_TYPE = "Point"
    FIELD_TITLE = "Title"
    FIELD_REGION = "Region"
    FIELD_LOCALITY = "Locality"
    FIELD_LABEL = "Label"
    SYMBOL_SHAPE = QgsSimpleMarkerSymbolLayer.Circle
    SYMBOL_COLOR = QColor(0, 124, 191)
    SYMBOL_SIZE = 3.0
    LABEL_TEXT_COLOR = QColor("black")
    LABEL_TEXT_SIZE = 10

    def __init__(self) -> None:
        """Initializes the configuration and API handlers."""
        self.configuration_handler = ConfigurationHandler()
        self.api_handler = ExternalApiHandler()

    def search_text(self, text: str, lon: float, lat: float) -> dict[str, Any]:
        """Searches for places near the supplied position."""
        region, apikey = self.configuration_handler.get_credentials()
        place_url = (
            f"https://places.geo.{region}.amazonaws.com/v2/search-text"
            f"?key={quote(apikey, safe='')}"
        )
        data = {
            "Language": self.PLACES_LANGUAGE,
            "MaxResults": self.PLACES_MAX_RESULTS,
            "QueryText": text,
            "BiasPosition": [lon, lat],
        }
        return self.api_handler.send_json_post_request(place_url, data)

    def add_point_layer(self, data: dict) -> None:
        """Adds search results to the current project as a point layer."""
        layer = QgsVectorLayer(
            f"{self.LAYER_TYPE}?crs={self.WGS84_CRS}", "SearchText", "memory"
        )
        self.setup_layer(layer, data)

    def setup_layer(self, layer: QgsVectorLayer, data: dict) -> None:
        """Populates, styles, and adds the point layer to the project."""
        self.add_attributes(layer)
        self.add_features(layer, data)
        self.apply_layer_style(layer)
        self.apply_label_style(layer)
        layer.triggerRepaint()
        QgsProject.instance().addMapLayer(layer)

    def add_attributes(self, layer: QgsVectorLayer) -> None:
        """Adds place-result fields to the layer."""
        fields = QgsFields()
        fields.append(QgsField(self.FIELD_TITLE, QVariant.String))
        fields.append(QgsField(self.FIELD_REGION, QVariant.String))
        fields.append(QgsField(self.FIELD_LOCALITY, QVariant.String))
        fields.append(QgsField(self.FIELD_LABEL, QVariant.String))
        layer.dataProvider().addAttributes(fields)
        layer.updateFields()

    def add_features(self, layer: QgsVectorLayer, data: dict) -> None:
        """Adds drawable results, ignoring items without a position."""
        features = []
        for result in data.get("ResultItems", []):
            position = result.get("Position")
            if not position:
                continue
            address = result.get("Address", {})
            feature = QgsFeature(layer.fields())
            feature.setGeometry(
                QgsGeometry.fromPointXY(QgsPointXY(position[0], position[1]))
            )
            feature.setAttributes(
                [
                    result.get("Title", ""),
                    address.get("Region", {}).get("Name", ""),
                    address.get("Locality", ""),
                    address.get("Label", ""),
                ]
            )
            features.append(feature)
        layer.dataProvider().addFeatures(features)

    def apply_layer_style(self, layer: QgsVectorLayer) -> None:
        """Applies the point symbol to the layer."""
        symbol_layer = QgsSimpleMarkerSymbolLayer()
        symbol_layer.setShape(self.SYMBOL_SHAPE)
        symbol_layer.setColor(self.SYMBOL_COLOR)
        symbol_layer.setSize(self.SYMBOL_SIZE)
        symbol = QgsMarkerSymbol.createSimple({})
        symbol.changeSymbolLayer(0, symbol_layer)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    def apply_label_style(self, layer: QgsVectorLayer) -> None:
        """Applies title labeling to the layer."""
        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = self.FIELD_TITLE
        label_settings.enabled = True
        text_format = QgsTextFormat()
        text_format.setSize(self.LABEL_TEXT_SIZE)
        text_format.setColor(self.LABEL_TEXT_COLOR)
        label_settings.setFormat(text_format)
        layer.setLabelsEnabled(True)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
