from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .payload import prune_payload

MAX_WAYPOINTS = 23
MAX_ALTERNATIVES = 5
MAX_ISOLINE_THRESHOLDS = 5
MAX_THRESHOLD_SECONDS = 10_800
MAX_THRESHOLD_METERS = 300_000
MIN_TRACE_POINTS = 2
MAX_TRACE_POINTS = 5_000
MIN_SNAP_RADIUS = 0
MAX_SNAP_RADIUS = 10_000
# Keep a margin because the local great-circle check approximates AWS's 500 km limit.
MAX_TRACE_DISTANCE_METERS = 500_000
TRACE_DISTANCE_SAFETY_FACTOR = 0.995
MAX_TRANSIT_MODES = 15
# Every matrix cell is billed, so the plugin caps all regions at 100 cells.
MAX_MATRIX_CELLS = 100
# AWS limits every origin-destination pair in an Unbounded matrix to 10,000 km.
MAX_MATRIX_DISTANCE_METERS = 10_000_000

TRAVEL_MODES = ("Car", "Pedestrian", "Scooter", "Truck")
ROUTES_TRAVEL_MODES = (*TRAVEL_MODES, "Transit", "Intermodal")
TRANSIT_LIKE_MODES = ("Transit", "Intermodal")
OPTIMIZE_FOR = ("FastestRoute", "ShortestRoute")
AVOID_KEYS = (
    "TollRoads",
    "Ferries",
    "Tunnels",
    "ControlledAccessHighways",
    "DirtRoads",
    "UTurns",
)
TRANSIT_MODE_VALUES = (
    "AerialTramway",
    "Airplane",
    "All",
    "Bus",
    "BusRapidTransit",
    "CityTrain",
    "Ferry",
    "FunicularRailway",
    "HighSpeedTrain",
    "IntercityTrain",
    "InterregionalTrain",
    "LightRail",
    "Monorail",
    "PrivateBus",
    "RegionalTrain",
    "Subway",
)
ISOLINE_DIRECTIONS = ("Origin", "Destination")
THRESHOLD_TYPES = ("Time", "Distance")

# Offset-aware AWS time format: 1-9 fractional digits, offset hours 00-23,
# and second 60 for leap seconds. \Z rejects a trailing newline; re.ASCII
# keeps \d at [0-9] instead of accepting other Unicode decimal digits.
_ISO_WITH_OFFSET = re.compile(
    r"([12]\d{3})-(\d{2})-(\d{2})"
    r"T(\d{2}):(\d{2}):(\d{2})(\.\d{1,9})?"
    r"(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)\Z",
    re.ASCII,
)

_EARTH_RADIUS_METERS = 6_371_000.0


def _freeze(instance, name: str, value) -> None:
    """Stores a tuple copy of a field on a frozen dataclass."""
    object.__setattr__(instance, name, tuple(value))


def _freeze_positions(instance, name: str, positions) -> None:
    """Stores a tuple copy of a list of positions on a frozen dataclass."""
    object.__setattr__(instance, name, tuple(tuple(position) for position in positions))


@dataclass(frozen=True)
class TracePoint:
    """One GPS trace point for SnapToRoads."""

    position: tuple[float, float]
    timestamp: str | None = None
    heading: float | None = None
    speed: float | None = None

    def __post_init__(self) -> None:
        """Copies the position so a caller's list cannot change it later."""
        _freeze(self, "position", self.position)


@dataclass(frozen=True)
class RouteOptions:
    """Options for a CalculateRoutes request."""

    origin: tuple[float, float]
    destination: tuple[float, float]
    waypoints: tuple[tuple[float, float], ...] = ()
    travel_mode: str = "Car"
    optimize_for: str = "FastestRoute"
    avoid: tuple[str, ...] = ()
    max_alternatives: int = 0
    depart_now: bool = False
    departure_time: str | None = None
    arrival_time: str | None = None
    transit_allowed_modes: tuple[str, ...] = ()
    transit_excluded_modes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Copies every collection so callers' lists cannot change them later."""
        _freeze(self, "origin", self.origin)
        _freeze(self, "destination", self.destination)
        _freeze_positions(self, "waypoints", self.waypoints)
        _freeze(self, "avoid", self.avoid)
        _freeze(self, "transit_allowed_modes", self.transit_allowed_modes)
        _freeze(self, "transit_excluded_modes", self.transit_excluded_modes)


@dataclass(frozen=True)
class IsolineOptions:
    """Options for a CalculateIsolines request; thresholds use API units."""

    center: tuple[float, float]
    direction: str = "Origin"
    threshold_type: str = "Time"
    thresholds: tuple[int, ...] = ()
    travel_mode: str = "Car"

    def __post_init__(self) -> None:
        """Copies every collection so callers' lists cannot change them later."""
        _freeze(self, "center", self.center)
        _freeze(self, "thresholds", self.thresholds)


@dataclass(frozen=True)
class SnapOptions:
    """Options for a SnapToRoads request."""

    trace_points: tuple[TracePoint, ...] = ()
    snap_radius: int = 300
    travel_mode: str = "Car"

    def __post_init__(self) -> None:
        """Copies the trace points so a caller's list cannot change them later."""
        _freeze(self, "trace_points", self.trace_points)


@dataclass(frozen=True)
class MatrixOptions:
    """Options for a CalculateRouteMatrix request."""

    origins: tuple[tuple[float, float], ...] = ()
    destinations: tuple[tuple[float, float], ...] = ()
    travel_mode: str = "Car"

    def __post_init__(self) -> None:
        """Copies every collection so callers' lists cannot change them later."""
        _freeze_positions(self, "origins", self.origins)
        _freeze_positions(self, "destinations", self.destinations)


def _validate_position(position: object, name: str) -> list[float]:
    """Returns a validated WGS 84 ``[longitude, latitude]`` position."""
    if not isinstance(position, (list, tuple)) or len(position) != 2:
        raise ValueError(f"{name} must contain longitude and latitude.")

    lon, lat = position
    if isinstance(lon, bool) or isinstance(lat, bool):
        raise ValueError(f"{name} must contain numeric coordinates.")
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        raise ValueError(f"{name} must contain numeric coordinates.")
    try:
        finite = math.isfinite(lon) and math.isfinite(lat)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must contain finite coordinates.")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError(f"{name} is outside the WGS 84 coordinate range.")

    return [float(lon), float(lat)]


def _validate_travel_mode(mode: str, allowed: tuple[str, ...]) -> str:
    """Returns a travel mode accepted by the operation."""
    if mode not in allowed:
        raise ValueError(f"TravelMode must be one of: {', '.join(allowed)}.")
    return mode


def _validate_iso_time(value: str | None, name: str) -> str | None:
    """Returns an offset-aware ISO 8601 time, or ``None`` when omitted."""
    if value in (None, ""):
        return None
    if parse_iso_time(value) is None:
        raise ValueError(
            f"{name} must be a valid ISO 8601 time with a timezone offset, "
            f"such as 2026-08-26T09:00:00+09:00."
        )
    return value


def parse_iso_time(value: object) -> datetime | None:
    """
    Parses an offset-aware ISO 8601 time the AWS pattern accepts.

    Returns ``None`` for anything else. The value is parsed by hand so the
    result is identical on every supported Python version: fromisoformat
    accepts a different set of fraction digits per version and rejects the
    leap second 60. Request validation and the response time-difference
    fallback share this parser.
    """
    if not isinstance(value, str):
        return None
    match = _ISO_WITH_OFFSET.match(value)
    if match is None:
        return None
    year, month, day, hour, minute, second = (
        int(match.group(index)) for index in range(1, 7)
    )
    if hour > 23 or minute > 59 or second > 60:
        return None
    fraction = (match.group(7) or ".")[1:]
    microsecond = int((fraction + "000000")[:6]) if fraction else 0
    offset_text = match.group(8)
    if offset_text == "Z":
        offset = timezone.utc
    else:
        sign = 1 if offset_text[0] == "+" else -1
        delta = timedelta(hours=int(offset_text[1:3]), minutes=int(offset_text[4:6]))
        offset = timezone(sign * delta)
    # datetime cannot represent a leap second; treat it as the next second.
    leap = second == 60
    if leap:
        second = 59
    try:
        parsed = datetime(
            year, month, day, hour, minute, second, microsecond, tzinfo=offset
        )
    except ValueError:
        return None
    return parsed + timedelta(seconds=1) if leap else parsed


def _validate_time_choice(options: RouteOptions) -> None:
    """Ensures at most one of DepartNow, DepartureTime and ArrivalTime is set."""
    if not isinstance(options.depart_now, bool):
        raise ValueError("DepartNow must be true or false.")
    choices = (
        int(options.depart_now),
        int(options.departure_time not in (None, "")),
        int(options.arrival_time not in (None, "")),
    )
    if sum(choices) > 1:
        raise ValueError("Set only one of DepartNow, DepartureTime and ArrivalTime.")


def _validate_avoid(avoid: tuple[str, ...]) -> dict[str, bool]:
    """Returns the Avoid body for the checked avoidance options."""
    for key in avoid:
        if key not in AVOID_KEYS:
            raise ValueError(f"Avoid option {key!r} is not supported.")
    return dict.fromkeys(avoid, True)


def _validate_transit_modes(modes: tuple[str, ...], name: str) -> list[str]:
    """Returns validated transit mode names without duplicates."""
    for mode in modes:
        if mode not in TRANSIT_MODE_VALUES:
            raise ValueError(f"{name} contains an unknown transit mode: {mode!r}.")
    if len(set(modes)) != len(modes):
        raise ValueError(f"{name} contains duplicate transit modes.")
    if len(modes) > MAX_TRANSIT_MODES:
        raise ValueError(f"{name} must contain at most {MAX_TRANSIT_MODES} modes.")
    return list(modes)


def _validate_max_alternatives(value: int) -> int:
    """Returns a MaxAlternatives count within the API range."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("MaxAlternatives must be an integer.")
    if not 0 <= value <= MAX_ALTERNATIVES:
        raise ValueError(f"MaxAlternatives must be between 0 and {MAX_ALTERNATIVES}.")
    return value


def haversine_meters(start: tuple[float, float], end: tuple[float, float]) -> float:
    """Returns the approximate geodesic distance between two lon/lat points."""
    lon1, lat1 = math.radians(start[0]), math.radians(start[1])
    lon2, lat2 = math.radians(end[0]), math.radians(end[1])
    half_dlat = (lat2 - lat1) / 2
    half_dlon = (lon2 - lon1) / 2
    a = (
        math.sin(half_dlat) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(half_dlon) ** 2
    )
    a = min(a, 1.0)
    return 2 * _EARTH_RADIUS_METERS * math.asin(math.sqrt(a))


def build_routes_body(options: RouteOptions) -> dict[str, Any]:
    """
    Builds a CalculateRoutes request body.

    Transit and Intermodal use a reduced field set: AWS rejects Avoid,
    OptimizeRoutingFor, Waypoints, Tolls, Traffic and several other
    top-level fields for these modes, so they are never sent. The plugin
    does not request TravelStepType because it does not publish turn-by-turn
    steps.
    """
    _validate_travel_mode(options.travel_mode, ROUTES_TRAVEL_MODES)
    _validate_time_choice(options)

    body: dict[str, Any] = {
        "Origin": _validate_position(options.origin, "Origin"),
        "Destination": _validate_position(options.destination, "Destination"),
        "TravelMode": options.travel_mode,
        "LegGeometryFormat": "Simple",
        # Without "Summary" the response carries no leg distance/duration.
        "LegAdditionalFeatures": ["Summary"],
        "DepartNow": True if options.depart_now else None,
        "DepartureTime": _validate_iso_time(options.departure_time, "DepartureTime"),
        "ArrivalTime": _validate_iso_time(options.arrival_time, "ArrivalTime"),
    }
    body["MaxAlternatives"] = _validate_max_alternatives(options.max_alternatives)

    if options.travel_mode in TRANSIT_LIKE_MODES:
        if options.transit_allowed_modes and options.transit_excluded_modes:
            raise ValueError("Set either allowed or excluded transit modes, not both.")
        if options.travel_mode == "Transit":
            transit_options: dict[str, Any] = {
                "AllowedModes": _validate_transit_modes(
                    options.transit_allowed_modes, "AllowedModes"
                ),
                "ExcludedModes": _validate_transit_modes(
                    options.transit_excluded_modes, "ExcludedModes"
                ),
            }
            body["TravelModeOptions"] = {"Transit": transit_options}
        return prune_payload(body)

    if len(options.waypoints) > MAX_WAYPOINTS:
        raise ValueError(f"Waypoints must contain at most {MAX_WAYPOINTS} points.")
    body["OptimizeRoutingFor"] = _validate_optimize_for(options.optimize_for)
    body["Waypoints"] = [
        {"Position": _validate_position(point, "Waypoint")}
        for point in options.waypoints
    ]
    body["Avoid"] = _validate_avoid(options.avoid)
    return prune_payload(body)


def _validate_optimize_for(value: str) -> str:
    """Returns a supported OptimizeRoutingFor value."""
    if value not in OPTIMIZE_FOR:
        raise ValueError(
            f"OptimizeRoutingFor must be one of: {', '.join(OPTIMIZE_FOR)}."
        )
    return value


def _validate_thresholds(options: IsolineOptions) -> list[int]:
    """Returns isoline thresholds in seconds or meters."""
    if options.threshold_type not in THRESHOLD_TYPES:
        raise ValueError(
            f"Threshold type must be one of: {', '.join(THRESHOLD_TYPES)}."
        )
    if not 1 <= len(options.thresholds) <= MAX_ISOLINE_THRESHOLDS:
        raise ValueError(
            f"Set between 1 and {MAX_ISOLINE_THRESHOLDS} threshold values."
        )
    if options.threshold_type == "Time":
        maximum, unit = MAX_THRESHOLD_SECONDS, "seconds"
    else:
        maximum, unit = MAX_THRESHOLD_METERS, "meters"
    values = []
    for value in options.thresholds:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Threshold values must be integers.")
        if value < 1:
            raise ValueError("Threshold values must be at least 1.")
        if value > maximum:
            raise ValueError(f"Threshold values must be at most {maximum} {unit}.")
        values.append(value)
    return values


def build_isolines_body(options: IsolineOptions) -> dict[str, Any]:
    """Builds a CalculateIsolines request body."""
    if options.direction not in ISOLINE_DIRECTIONS:
        raise ValueError(f"Direction must be one of: {', '.join(ISOLINE_DIRECTIONS)}.")
    _validate_travel_mode(options.travel_mode, TRAVEL_MODES)
    body: dict[str, Any] = {
        options.direction: _validate_position(options.center, options.direction),
        "Thresholds": {options.threshold_type: _validate_thresholds(options)},
        "TravelMode": options.travel_mode,
        # The API default is FlexiblePolyline, which the plugin cannot decode.
        "IsolineGeometryFormat": "Simple",
    }
    return prune_payload(body)


def _validate_trace_points(points: tuple[TracePoint, ...]) -> list[dict[str, Any]]:
    """Returns TracePoints within the count and total-distance limits."""
    if not MIN_TRACE_POINTS <= len(points) <= MAX_TRACE_POINTS:
        raise ValueError(
            f"TracePoints must contain between {MIN_TRACE_POINTS} and "
            f"{MAX_TRACE_POINTS} points (got {len(points)})."
        )

    body_points = []
    for point in points:
        body_points.append(
            prune_payload(
                {
                    "Position": _validate_position(point.position, "TracePoint"),
                    "Timestamp": _validate_iso_time(point.timestamp, "Timestamp"),
                    "Heading": _validate_heading(point.heading),
                    "Speed": _validate_speed(point.speed),
                }
            )
        )

    total = 0.0
    for previous, current in zip(points, points[1:]):
        total += haversine_meters(previous.position, current.position)
    if total > MAX_TRACE_DISTANCE_METERS * TRACE_DISTANCE_SAFETY_FACTOR:
        raise ValueError(
            "The total distance between the trace points exceeds the "
            "500 km SnapToRoads usage limit (the plugin checks an "
            "approximate distance with a small safety margin)."
        )
    return body_points


def _validate_heading(value: float | None) -> float | None:
    """Returns a heading between 0 and 360 degrees, or ``None``."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Heading must be a number of degrees.")
    if not 0 <= value <= 360:
        raise ValueError("Heading must be between 0 and 360 degrees.")
    return float(value)


def _validate_speed(value: float | None) -> float | None:
    """Returns a non-negative speed, or ``None``."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Speed must be a number.")
    if value < 0 or not math.isfinite(value):
        raise ValueError("Speed must be zero or more.")
    return float(value)


def build_snap_body(options: SnapOptions) -> dict[str, Any]:
    """Builds a SnapToRoads request body."""
    _validate_travel_mode(options.travel_mode, TRAVEL_MODES)
    if isinstance(options.snap_radius, bool) or not isinstance(
        options.snap_radius, int
    ):
        raise ValueError("SnapRadius must be an integer number of meters.")
    if not MIN_SNAP_RADIUS <= options.snap_radius <= MAX_SNAP_RADIUS:
        raise ValueError(
            f"SnapRadius must be between {MIN_SNAP_RADIUS} and "
            f"{MAX_SNAP_RADIUS} meters."
        )
    return prune_payload(
        {
            "TracePoints": _validate_trace_points(options.trace_points),
            "SnapRadius": options.snap_radius,
            "TravelMode": options.travel_mode,
            # The API default is FlexiblePolyline, which the plugin cannot decode.
            "SnappedGeometryFormat": "Simple",
        }
    )


def build_matrix_body(options: MatrixOptions) -> dict[str, Any]:
    """Builds an Unbounded CalculateRouteMatrix request body."""
    _validate_travel_mode(options.travel_mode, TRAVEL_MODES)
    if not options.origins or not options.destinations:
        raise ValueError("Set at least one origin and one destination.")
    cells = len(options.origins) * len(options.destinations)
    if cells > MAX_MATRIX_CELLS:
        raise ValueError(
            f"The matrix size (origins x destinations) must not exceed "
            f"{MAX_MATRIX_CELLS}; every cell is one billed route calculation."
        )

    origins = [_validate_position(point, "Origin") for point in options.origins]
    destinations = [
        _validate_position(point, "Destination") for point in options.destinations
    ]
    for origin_index, origin in enumerate(origins, start=1):
        for destination_index, destination in enumerate(destinations, start=1):
            if (
                haversine_meters(tuple(origin), tuple(destination))
                > MAX_MATRIX_DISTANCE_METERS
            ):
                raise ValueError(
                    f"The straight-line distance between origin {origin_index} and "
                    f"destination {destination_index} exceeds the 10,000 km "
                    "CalculateRouteMatrix usage limit."
                )

    return prune_payload(
        {
            "Origins": [{"Position": point} for point in origins],
            "Destinations": [{"Position": point} for point in destinations],
            "TravelMode": options.travel_mode,
            "RoutingBoundary": {"Unbounded": True},
        }
    )
