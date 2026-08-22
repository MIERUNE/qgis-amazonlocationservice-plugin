def classFactory(iface):
    """Creates the plugin instance for QGIS."""
    from .location_service import LocationService

    return LocationService(iface)
