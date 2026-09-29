import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .places_algorithms import (
    GeocodeAlgorithm,
    GetPlaceAlgorithm,
    ReverseGeocodeAlgorithm,
    SearchNearbyAlgorithm,
    SearchTextAlgorithm,
)
from .routes_algorithms import (
    CalculateIsolinesAlgorithm,
    CalculateRouteMatrixAlgorithm,
    CalculateRoutesAlgorithm,
    SnapToRoadsAlgorithm,
)

PROVIDER_ID = "amazonlocationservice"

PLACES_ALGORITHMS = (
    SearchTextAlgorithm,
    GeocodeAlgorithm,
    ReverseGeocodeAlgorithm,
    SearchNearbyAlgorithm,
    GetPlaceAlgorithm,
)
ROUTES_ALGORITHMS = (
    CalculateRoutesAlgorithm,
    CalculateIsolinesAlgorithm,
    SnapToRoadsAlgorithm,
    CalculateRouteMatrixAlgorithm,
)


class LocationServiceProvider(QgsProcessingProvider):
    """Processing provider for the Amazon Location Service algorithms."""

    def id(self) -> str:
        """Returns the provider id used in algorithm ids."""
        return PROVIDER_ID

    def name(self) -> str:
        """Returns the provider name shown in the toolbox."""
        return "Amazon Location Service"

    def icon(self) -> QIcon:
        """Returns the plugin icon."""
        return QIcon(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui/icon.png")
        )

    def loadAlgorithms(self) -> None:
        """Registers the Places and Routes algorithms."""
        for algorithm in (*PLACES_ALGORITHMS, *ROUTES_ALGORITHMS):
            self.addAlgorithm(algorithm())
