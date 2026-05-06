from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from qgis.core import QgsProject, QgsRasterLayer

from ..utils.configuration_handler import ConfigurationHandler


@dataclass
class MapsOptions:
    """
    Holds the user-selected map style options that map to Amazon Location
    Service Maps V2 style and tile parameters.
    """

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
    """
    Manages the loading of XYZ tile (raster) layers into a QGIS project
    based on configurations.
    """

    KEY_REGION = "region_value"
    KEY_APIKEY = "apikey_value"
    BASE_URL = "https://als.dayjournal.dev"

    def __init__(self) -> None:
        """
        Initializes the MapsFunctions with a configuration handler.
        """
        self.configuration_handler = ConfigurationHandler()

    def get_configuration_settings(self) -> tuple[str, str]:
        """
        Fetches necessary configuration settings from the settings manager.

        Returns:
            tuple[str, str]: A tuple containing the region and API key.

        Raises:
            ValueError: If the region or API key is missing or empty.
        """
        region = self.configuration_handler.get_setting(self.KEY_REGION)
        apikey = self.configuration_handler.get_setting(self.KEY_APIKEY)

        if not region or not str(region).strip():
            raise ValueError("Missing required configuration setting: region")
        if not apikey or not str(apikey).strip():
            raise ValueError("Missing required configuration setting: apikey")

        return region, apikey

    def build_tile_url(self, region: str, apikey: str, options: MapsOptions) -> str:
        """
        Builds the XYZ tile URL with selected style parameters.

        The Lambda wrapper translates the camelCase keys below to Amazon
        Location Service Maps V2 GetStyleDescriptor parameters (kebab-case).

        Amazon Location Service only accepts ``colorScheme`` for the ``Satellite`` style; the
        other descriptor parameters (politicalView, terrain, contourDensity,
        traffic, travelModes, buildings) are therefore omitted when
        ``style == 'Satellite'``.

        The ``language`` parameter is consumed by the proxy wrapper (not by AWS).
        The wrapper rewrites the style descriptor's ``text-field`` expressions to
        ``name:{language}`` before chiitiler renders the tile.

        Args:
            region (str): Amazon Location Service region value.
            apikey (str): API key value.
            options (MapsOptions): User-selected style options.

        Returns:
            str: The XYZ tile URL with z/x/y placeholders for QGIS.
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
        """
        Composes the layer name shown in the QGIS layer panel.

        Args:
            options (MapsOptions): User-selected style options.

        Returns:
            str: A short layer name combining style and color scheme.
        """
        return f"{options.style} {options.color_scheme}"

    def add_xyz_tile_layer(self, options: MapsOptions) -> None:
        """
        Adds an XYZ tile layer (raster tile) into the current QGIS project using
        configuration settings.

        Args:
            options (MapsOptions): User-selected style options.
        """
        try:
            region_value, apikey_value = self.get_configuration_settings()
            tile_url = self.build_tile_url(region_value, apikey_value, options)
            encoded_tile_url = quote(tile_url, safe=":/?{}=,%")
            layer_url = f"type=xyz&url={encoded_tile_url}&zmin=0&zmax=18"
            layer_name = self.compose_layer_name(options)
            xyz_tile_layer = QgsRasterLayer(layer_url, layer_name, "wms")
            if not xyz_tile_layer.isValid():
                raise RuntimeError(f"Invalid XYZ tile layer for URL: {layer_url}")
            QgsProject.instance().addMapLayer(xyz_tile_layer)
        except KeyError as e:
            raise KeyError(f"Missing configuration for {e!r}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to add XYZ tile layer: {e!r}") from e
