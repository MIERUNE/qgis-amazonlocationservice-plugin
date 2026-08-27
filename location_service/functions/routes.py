from __future__ import annotations

from typing import Any

from .base import ServiceFunctionsBase
from .routes_capabilities import validate_region_options
from .routes_requests import (
    IsolineOptions,
    MatrixOptions,
    RouteOptions,
    SnapOptions,
    build_isolines_body,
    build_matrix_body,
    build_routes_body,
    build_snap_body,
)
from .routes_results import major_road_names

__all__ = ["RoutesFunctions", "major_road_names"]


class RoutesFunctions(ServiceFunctionsBase):
    """
    Network facade for the Amazon Location Routes V2 operations.

    Request bodies are built and validated in ``routes_requests``, responses
    are checked in ``routes_results``, and QGIS layers are built in
    ``routes_layers``; this class only sends the requests.
    """

    SERVICE_HOST = "routes.geo.{region}.amazonaws.com"

    def request_routes(
        self, options: RouteOptions, *, credentials: tuple[str, str]
    ) -> dict[str, Any]:
        """Sends a CalculateRoutes request with the captured credentials."""
        validate_region_options(
            credentials[0],
            "CalculateRoutes",
            travel_mode=options.travel_mode,
            avoid=options.avoid,
            max_alternatives_value=options.max_alternatives,
            arrival_time=options.arrival_time,
        )
        url = self.build_endpoint("v2/routes", credentials=credentials)
        return self.api_handler.send_json_post_request(url, build_routes_body(options))

    def request_isolines(
        self, options: IsolineOptions, *, credentials: tuple[str, str]
    ) -> dict[str, Any]:
        """Sends a CalculateIsolines request with the captured credentials."""
        validate_region_options(
            credentials[0], "CalculateIsolines", travel_mode=options.travel_mode
        )
        url = self.build_endpoint("v2/isolines", credentials=credentials)
        return self.api_handler.send_json_post_request(
            url, build_isolines_body(options)
        )

    def request_snap_to_roads(
        self, options: SnapOptions, *, credentials: tuple[str, str]
    ) -> dict[str, Any]:
        """Sends a SnapToRoads request with the captured credentials."""
        validate_region_options(
            credentials[0], "SnapToRoads", travel_mode=options.travel_mode
        )
        url = self.build_endpoint("v2/snap-to-roads", credentials=credentials)
        return self.api_handler.send_json_post_request(url, build_snap_body(options))

    def request_route_matrix(
        self, options: MatrixOptions, *, credentials: tuple[str, str]
    ) -> dict[str, Any]:
        """Sends a CalculateRouteMatrix request with the captured credentials."""
        validate_region_options(
            credentials[0],
            "CalculateRouteMatrix",
            travel_mode=options.travel_mode,
            origins_count=len(options.origins),
            destinations_count=len(options.destinations),
        )
        url = self.build_endpoint("v2/route-matrix", credentials=credentials)
        return self.api_handler.send_json_post_request(url, build_matrix_body(options))

    def calculate_routes(
        self,
        st_lon: float,
        st_lat: float,
        ed_lon: float,
        ed_lat: float,
        credentials: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Calculates routes between the supplied WGS 84 coordinates.

        Deprecated compatibility wrapper around :meth:`request_routes` for
        callers that use the v4.4 coordinate-based method.
        """
        if credentials is None:
            credentials = self.configuration_handler.get_credentials()
        options = RouteOptions(origin=(st_lon, st_lat), destination=(ed_lon, ed_lat))
        return self.request_routes(options, credentials=credentials)
