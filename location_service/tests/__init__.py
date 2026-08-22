import importlib.util

# Probe qgis because find_spec("qgis.core") raises when qgis is absent.
HAS_QGIS = importlib.util.find_spec("qgis") is not None
