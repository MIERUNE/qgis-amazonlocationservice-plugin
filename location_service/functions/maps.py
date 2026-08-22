from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from qgis.core import QgsProject, QgsRasterLayer

from ..utils.configuration_handler import ConfigurationHandler


@dataclass
class MapsOptions:
    """User-selected Maps V2 style and tile options."""

    style: str
    color_scheme: str = "Light"
    language: str = ""
    political_view: str = ""
    terrain: str = ""
    contour_density: str = ""
    traffic: str = ""
    travel_modes: list[str] = field(default_factory=list)
    buildings_3d: bool = False


class MapsFunctions:
    """Builds and adds Amazon Location XYZ layers."""

    BASE_URL = "https://als.dayjournal.dev"

    def __init__(self) -> None:
        """Initializes the configuration handler."""
        self.configuration_handler = ConfigurationHandler()

    def build_tile_url(self, region: str, apikey: str, options: MapsOptions) -> str:
        """
        Builds the proxy XYZ URL with QGIS z/x/y placeholders.

        The proxy maps camelCase style options to Maps V2 descriptor
        parameters. It handles ``language`` by rewriting ``text-field``
        expressions before chiitiler renders tiles. For ``Satellite``,
        ``colorScheme`` is the only style option sent.
        """
        params: dict[str, str] = {
            "APIkey": apikey,
            "colorScheme": options.color_scheme,
        }
        if options.style != "Satellite":
            optional_params = {
                "politicalView": options.political_view,
                "terrain": options.terrain,
                "contourDensity": options.contour_density,
                "traffic": options.traffic,
            }
            for key, value in optional_params.items():
                if value:
                    params[key] = value
            if options.language and options.language != "Default":
                params["language"] = options.language
            if options.travel_modes:
                params["travelModes"] = ",".join(options.travel_modes)
            if options.buildings_3d:
                params["buildings"] = "Buildings3D"
        query = urlencode(params, safe=",")
        return f"{self.BASE_URL}/{region}/{options.style}/{{z}}/{{x}}/{{y}}?{query}"

    def compose_layer_name(self, options: MapsOptions) -> str:
        """Returns the short name shown in the layer panel."""
        return f"{options.style} {options.color_scheme}"

    def add_xyz_tile_layer(self, options: MapsOptions) -> None:
        """Adds the selected XYZ tile layer to the current project."""
        region_value, apikey_value = self.configuration_handler.get_credentials()
        tile_url = self.build_tile_url(region_value, apikey_value, options)
        encoded_tile_url = quote(tile_url, safe=":/?{}=,%")
        layer_url = f"type=xyz&url={encoded_tile_url}&zmin=0&zmax=18"
        layer_name = self.compose_layer_name(options)
        xyz_tile_layer = QgsRasterLayer(layer_url, layer_name, "wms")
        if not xyz_tile_layer.isValid():
            # Avoid exposing layer_url because its query string contains the API key.
            raise RuntimeError(f"Invalid XYZ tile layer (style: {layer_name})")
        QgsProject.instance().addMapLayer(xyz_tile_layer)
