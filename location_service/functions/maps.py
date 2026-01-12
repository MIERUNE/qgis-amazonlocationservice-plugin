from qgis.core import QgsProject, QgsRasterLayer

from ..utils.configuration_handler import ConfigurationHandler


class MapsFunctions:
    """
    Manages the loading of vector tile layers into a QGIS project
    based on configurations.
    """

    KEY_REGION = "region_value"
    KEY_APIKEY = "apikey_value"

    def __init__(self) -> None:
        """
        Initializes the MapFunctions with a configuration handler.
        """
        self.configuration_handler = ConfigurationHandler()

    def get_configuration_settings(self) -> tuple[str, str]:
        """
        Fetches necessary configuration settings from the settings manager.

        Returns:
            Tuple[str, str]: A tuple containing the region,
            maps name, and API key.
        """
        region = self.configuration_handler.get_setting(self.KEY_REGION)
        apikey = self.configuration_handler.get_setting(self.KEY_APIKEY)
        return region, apikey

    def add_xyz_tile_layer(self, selected_style: str) -> str:
        """
        Adds an XYZ tile layer (raster tile) into the current QGIS project using
        configuration settings.
        """
        try:
            region_value, apikey_value = self.get_configuration_settings()
            tile_url = (
                f"https://als.dayjournal.dev/{region_value}/{selected_style}/"
                f"{{z}}/{{x}}/{{y}}?APIkey={apikey_value}"
            )
            layer_url = f"type=xyz&url={tile_url}&zmin=0&zmax=18"
            xyz_tile_layer = QgsRasterLayer(layer_url, selected_style, "wms")
            QgsProject.instance().addMapLayer(xyz_tile_layer)
        except KeyError as e:
            raise KeyError(f"Missing configuration for {e!r}") from e
        except Exception as e:
            raise Exception(f"Failed to load XYZ tile layer: {e!r}") from e
