from __future__ import annotations

import math
from typing import Any

from .routes_requests import parse_iso_time

# Notice containers; RentalLegDetails has no Notices field.
_NOTICE_LEG_DETAILS = (
    "VehicleLegDetails",
    "PedestrianLegDetails",
    "FerryLegDetails",
    "TransitLegDetails",
    "TaxiLegDetails",
)

# Official RouteLeg.Type values and the details container each type uses.
LEG_TYPES = ("Ferry", "Pedestrian", "Vehicle", "Rental", "Taxi", "Transit")
DETAILS_BY_LEG_TYPE = {
    "Ferry": "FerryLegDetails",
    "Pedestrian": "PedestrianLegDetails",
    "Vehicle": "VehicleLegDetails",
    "Rental": "RentalLegDetails",
    "Taxi": "TaxiLegDetails",
    "Transit": "TransitLegDetails",
}
# Official RouteLeg.TravelMode values; unknown values are rejected.
LEG_TRAVEL_MODES = (
    "Car",
    "Ferry",
    "Pedestrian",
    "Scooter",
    "Truck",
    "CarShuttleTrain",
    "AerialTramway",
    "Airplane",
    "Bus",
    "BusRapidTransit",
    "CityTrain",
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
# Leg details that carry required Attributions.
ATTRIBUTION_DETAILS_BY_TYPE = {
    "Transit": "TransitLegDetails",
    "Taxi": "TaxiLegDetails",
    "Rental": "RentalLegDetails",
}

# JSON float round-trips require tolerance when matching trace points.
POSITION_TOLERANCE = 1e-6


class BrokenResponseError(RuntimeError):
    """A response that cannot be joined safely to the request it answers."""


# Distances and durations are AWS Long values in meters and seconds.
MAX_LONG_VALUE = 4_294_967_295

# Official RouteMatrixEntry.Error codes.
MATRIX_ERROR_CODES = (
    "NoMatch",
    "NoMatchDestination",
    "NoMatchOrigin",
    "NoRoute",
    "OutOfBounds",
    "OutOfBoundsDestination",
    "OutOfBoundsOrigin",
    "Other",
    "Violation",
)


def _optional_long(container: dict[str, Any], key: str) -> float | None:
    """
    Returns an optional AWS Long value from a response object.

    An absent value is ``None``; a value that is present but not a finite
    number between 0 and the Long maximum is a broken response.
    """
    value = container.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid_long(key)
    try:
        # isfinite raises OverflowError for integers beyond float range.
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if (
        not finite
        or not 0 <= value <= MAX_LONG_VALUE
        or (isinstance(value, float) and not value.is_integer())
    ):
        raise _invalid_long(key)
    return float(value)


def _invalid_long(key: str) -> BrokenResponseError:
    """Returns the error for a present but unusable Long value."""
    return BrokenResponseError(
        f"The response carries an invalid {key} value; no layer was "
        "created from this response."
    )


def _position(value: object) -> list[float] | None:
    """Returns a drawable ``[lon, lat]`` pair, or ``None`` when unusable."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    lon, lat = value
    if isinstance(lon, bool) or isinstance(lat, bool):
        return None
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        return None
    try:
        finite = math.isfinite(lon) and math.isfinite(lat)
    except OverflowError:
        return None
    if not finite:
        return None
    if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
        return None
    return [float(lon), float(lat)]


def _response_text(container: dict[str, Any], key: str) -> str:
    """Returns a string response field, or an empty string if unusable."""
    value = container.get(key)
    return value if isinstance(value, str) else ""


def _response_object(container: dict[str, Any], key: str, owner: str) -> dict[str, Any]:
    """Returns an optional response object and rejects a malformed one."""
    value = container.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BrokenResponseError(
            f"{owner} carries a malformed {key} value; no layer was created "
            "from this response."
        )
    return value


def _response_list(container: dict[str, Any], key: str, owner: str) -> list:
    """Returns an optional response array and rejects a malformed one."""
    value = container.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise BrokenResponseError(
            f"{owner} carries a malformed {key} value; no layer was created "
            "from this response."
        )
    return value


def validate_notices(container: dict[str, Any], owner: str) -> list[dict[str, Any]]:
    """Returns the optional Notices array after validating its entries."""
    notices = _response_list(container, "Notices", owner)
    if any(not isinstance(notice, dict) for notice in notices):
        raise BrokenResponseError(
            f"{owner} carries a malformed Notices value; no layer was "
            "created from this response."
        )
    return notices


def major_road_names(route: dict[str, Any]) -> str:
    """
    Joins major-road labels, preferring ``RoadName`` over ``RouteNumber``.

    Each nested value may be absent or null.
    """
    labels = route.get("MajorRoadLabels")
    if not isinstance(labels, list):
        return ""
    names = []
    for label in labels:
        if not isinstance(label, dict):
            continue
        road = label.get("RoadName")
        number = label.get("RouteNumber")
        road_name = _response_text(road, "Value") if isinstance(road, dict) else ""
        route_number = (
            _response_text(number, "Value") if isinstance(number, dict) else ""
        )
        if road_name or route_number:
            names.append(road_name or route_number)
    return ", ".join(names)


def leg_line_points(leg: dict[str, Any]) -> list[list[float]] | None:
    """Returns the leg's LineString as drawable points, or ``None``."""
    if not isinstance(leg, dict):
        return None
    geometry = leg.get("Geometry")
    if not isinstance(geometry, dict):
        return None
    line = geometry.get("LineString")
    if not isinstance(line, list) or len(line) < 2:
        return None
    points = [_position(coordinate) for coordinate in line]
    if any(point is None for point in points):
        return None
    return points  # type: ignore[return-value]


def _leg_details(leg: dict[str, Any]) -> dict[str, Any]:
    """
    Returns the details object matching the leg's type, or an empty dict.

    The type decides which container is read, so details of a different leg
    type in the same object are never mistaken for this leg's summary.
    """
    details_key = DETAILS_BY_LEG_TYPE.get(str(leg.get("Type") or ""))
    details = leg.get(details_key) if details_key else None
    return details if isinstance(details, dict) else {}


def _duration_from_times(details: dict[str, Any]) -> float | None:
    """Derives a leg duration in seconds from its departure and arrival times."""
    departure = details.get("Departure")
    arrival = details.get("Arrival")
    start = parse_iso_time(
        departure.get("Time") if isinstance(departure, dict) else None
    )
    end = parse_iso_time(arrival.get("Time") if isinstance(arrival, dict) else None)
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    return seconds if seconds >= 0 else None


def leg_summary(leg: dict[str, Any]) -> tuple[float | None, float | None]:
    """
    Returns the leg's ``(distance, duration)`` in meters and seconds.

    Both the LegDetails Summary and its Overview are optional in the API, so
    missing values are returned as ``None``. When the duration is absent it
    falls back to the departure/arrival times of transit-style legs.
    """
    details = _leg_details(leg)
    summary = _response_object(details, "Summary", "A route leg")
    overview = _response_object(summary, "Overview", "A route leg Summary")
    distance = _optional_long(overview, "Distance")
    duration = _optional_long(overview, "Duration")
    if duration is None:
        duration = _duration_from_times(details)
    return distance, duration


def route_totals(route: dict[str, Any]) -> tuple[float | None, float | None]:
    """
    Returns the route's total ``(distance, duration)``.

    ``Route.Summary`` is optional and may be empty, so missing totals fall
    back to the sum of the available leg summaries, and finally to ``None``.
    """
    summary = _response_object(route, "Summary", "A route")
    distance = _optional_long(summary, "Distance")
    duration = _optional_long(summary, "Duration")
    if distance is not None and duration is not None:
        return distance, duration

    # Avoid partial totals: sum a metric only when every leg provides it.
    # A malformed leg counts as a missing value.
    legs = _response_list(route, "Legs", "A route")
    if any(not isinstance(leg, dict) for leg in legs):
        return distance, duration
    summaries = [leg_summary(leg) for leg in legs]
    if distance is None and legs:
        distances = [leg_distance for leg_distance, _ in summaries]
        if all(value is not None for value in distances):
            distance = sum(distances)
    if duration is None and legs:
        durations = [leg_duration for _, leg_duration in summaries]
        if all(value is not None for value in durations):
            duration = sum(durations)
    # A sum beyond the Long range cannot be a real total; leave it unknown.
    if distance is not None and distance > MAX_LONG_VALUE:
        distance = None
    if duration is not None and duration > MAX_LONG_VALUE:
        duration = None
    return distance, duration


def validate_route_count(data: dict[str, Any], max_alternatives: int) -> None:
    """Rejects a response with more routes than the request allowed."""
    routes = _response_list(data, "Routes", "The response")
    if len(routes) > max_alternatives + 1:
        raise BrokenResponseError(
            "The response contains more routes than the request allowed; "
            "no layer was created from this response."
        )


def transit_leg_attributes(leg: dict[str, Any]) -> dict[str, str]:
    """Returns the transit-style attributes of a leg (empty strings if absent)."""
    details = _leg_details(leg)
    transport = details.get("Transport")
    if not isinstance(transport, dict):
        transport = {}
    agency = details.get("Agency")
    departure = details.get("Departure")
    arrival = details.get("Arrival")
    route_name = _response_text(transport, "RouteName")
    if not route_name:
        route_name = _response_text(transport, "ShortRouteName")
    if not route_name:
        route_name = _response_text(transport, "LongRouteName")
    return {
        "Agency": _response_text(agency, "Name") if isinstance(agency, dict) else "",
        "RouteName": route_name,
        "Headsign": _response_text(transport, "Headsign"),
        "DepartureTime": (
            _response_text(departure, "Time") if isinstance(departure, dict) else ""
        ),
        "ArrivalTime": (
            _response_text(arrival, "Time") if isinstance(arrival, dict) else ""
        ),
    }


def collect_notices(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Collects the response and leg notices of a CalculateRoutes response.

    The notice types differ per travel mode (transit notices have no Details
    and each mode uses its own code vocabulary), so every notice is reduced
    to the common ``Code`` / ``Impact`` pair plus its location in the route.
    """
    notices = []
    for notice in validate_notices(data, "The response"):
        notices.append(_normalized_notice(notice, "response", None, None, ""))
    routes = data.get("Routes")
    if not isinstance(routes, list):
        return notices
    for route_index, route in enumerate(routes):
        if not isinstance(route, dict):
            continue
        legs = route.get("Legs")
        if not isinstance(legs, list):
            continue
        for leg_index, leg in enumerate(legs):
            if isinstance(leg, dict):
                notices.extend(_leg_notices(route_index, leg_index, leg))
    return notices


def _leg_notices(
    route_index: int, leg_index: int, leg: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Returns the normalized notices of one leg.

    Like the summaries and attributions, only the details container matching
    the leg's type is read, so a stray container of another type cannot
    produce notices under this leg's name. Rental legs carry no notices.
    """
    found = []
    leg_type = str(leg.get("Type") or "")
    details_key = DETAILS_BY_LEG_TYPE.get(leg_type)
    if details_key not in _NOTICE_LEG_DETAILS:
        return found
    details = leg.get(details_key)
    if not isinstance(details, dict):
        return found
    for notice in validate_notices(details, "A leg's details"):
        found.append(
            _normalized_notice(notice, "leg", route_index, leg_index, leg_type)
        )
    return found


def _normalized_notice(
    notice: dict[str, Any],
    scope: str,
    route_index: int | None,
    leg_index: int | None,
    leg_type: str,
) -> dict[str, Any]:
    """Returns one notice in the plugin's common shape."""
    return {
        "scope": scope,
        "route_index": route_index,
        "leg_index": leg_index,
        "leg_type": leg_type,
        "code": str(notice.get("Code") or ""),
        "impact": str(notice.get("Impact") or ""),
        "details": notice.get("Details"),
    }


def collect_attributions(data: dict[str, Any]) -> list[dict[str, str]]:
    """
    Collects the attributions the API requires displaying, without duplicates.

    Transit, taxi and rental legs return ``Attributions``; the description
    lives inside the ``WebLink`` and the URL itself is optional.
    """
    attributions = []
    seen = set()
    routes = _response_list(data, "Routes", "The response")
    for route in routes:
        if not isinstance(route, dict):
            continue
        legs = _response_list(route, "Legs", "A route")
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            for entry in _leg_attributions(leg):
                dedup_key = (entry["type"], entry["description"], entry["url"])
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                attributions.append(entry)
    return attributions


def _leg_attributions(leg: dict[str, Any]) -> list[dict[str, str]]:
    """
    Returns the displayable attribution entries of one leg.

    A transit, taxi or rental leg must carry its details object with a
    well-formed ``Attributions`` array: the API requires displaying them, so
    a leg whose details or attributions are missing is a broken response
    rather than "no attributions".
    """
    entries = []
    details_key = ATTRIBUTION_DETAILS_BY_TYPE.get(str(leg.get("Type") or ""))
    if details_key is not None:
        details = leg.get(details_key)
        if not isinstance(details, dict):
            raise BrokenResponseError(
                "A leg is missing its required details; no layer was "
                "created from this response."
            )
        attributions = details.get("Attributions")
        if not isinstance(attributions, list):
            raise BrokenResponseError(
                "A leg is missing its required attributions; no layer was "
                "created from this response."
            )
        for attribution in attributions:
            web_link = None
            if isinstance(attribution, dict):
                web_link = attribution.get("WebLink")
            description = None
            if isinstance(web_link, dict):
                description = web_link.get("Description")
            if not description:
                raise BrokenResponseError(
                    "A required attribution is malformed; no layer was "
                    "created from this response."
                )
            entries.append(
                {
                    "type": str(attribution.get("AttributionType") or ""),
                    "description": str(description),
                    "url": str(web_link.get("Url") or ""),
                }
            )
    return entries


def normalize_isolines(
    data: dict[str, Any],
    threshold_type: str,
    thresholds: tuple[int, ...],
) -> list[dict[str, Any]]:
    """
    Returns drawable isolines that answer the requested thresholds exactly.

    The response must carry one isoline per requested threshold with the
    matching threshold value; each isoline is one or more polygons (detached
    reachable areas) whose rings are closed and have at least four points.
    Anything else is a broken response: publishing a partial reachable area
    would silently misrepresent the result.
    """
    broken = BrokenResponseError(
        "The isolines do not match the requested thresholds; no layer was "
        "created from this response."
    )
    response = data.get("Isolines")
    if not isinstance(response, list) or len(response) != len(thresholds):
        raise broken
    remaining = list(thresholds)
    isolines = []
    for isoline in response:
        if not isinstance(isoline, dict):
            raise broken
        value = _optional_long(isoline, f"{threshold_type}Threshold")
        if value is None or value not in remaining:
            raise broken
        remaining.remove(value)
        geometries = isoline.get("Geometries")
        if not isinstance(geometries, list) or not geometries:
            raise broken
        polygons = [_polygon_rings(geometry) for geometry in geometries]
        isolines.append(
            {
                "threshold_type": threshold_type,
                "threshold_value": float(value),
                "polygons": polygons,
            }
        )
    return isolines


def _polygon_rings(geometry: object) -> list[list[list[float]]]:
    """
    Returns the rings of one response polygon, or raises for a broken one.

    AWS Simple polygons carry at least four coordinates per ring, and the
    first and last coordinate close the ring.
    """
    broken = BrokenResponseError(
        "An isoline polygon is malformed; no layer was created from this response."
    )
    if not isinstance(geometry, dict):
        raise broken
    polygon = geometry.get("Polygon")
    if not isinstance(polygon, list) or not polygon:
        raise broken
    rings = []
    for ring in polygon:
        if not isinstance(ring, list) or len(ring) < 4:
            raise broken
        points = [_position(coordinate) for coordinate in ring]
        if any(point is None for point in points) or points[0] != points[-1]:
            raise broken
        rings.append(points)
    return rings  # type: ignore[return-value]


def snapped_line_points(data: dict[str, Any]) -> list[list[float]] | None:
    """
    Returns the snapped LineString as drawable points, or ``None``.

    A missing or empty line is a legitimate "no drawable line" answer and
    returns ``None``. A present line with fewer than two points, or any other
    malformed geometry, is a broken response.
    """
    geometry = data.get("SnappedGeometry")
    if geometry in (None, {}):
        return None
    if not isinstance(geometry, dict):
        raise BrokenResponseError(
            "The snapped geometry is malformed; no layer was created from "
            "this response."
        )
    line = geometry.get("LineString")
    if line in (None, []):
        return None
    if not isinstance(line, list):
        raise BrokenResponseError(
            "The snapped geometry is malformed; no layer was created from "
            "this response."
        )
    points = [_position(coordinate) for coordinate in line]
    if any(point is None for point in points):
        raise BrokenResponseError(
            "The snapped geometry is malformed; no layer was created from "
            "this response."
        )
    if len(points) < 2:
        raise BrokenResponseError(
            "The snapped geometry is malformed; no layer was created from "
            "this response."
        )
    return points  # type: ignore[return-value]


def normalize_snapped_trace_points(
    data: dict[str, Any],
    sent_positions: list[list[float]],
) -> list[dict[str, Any]]:
    """
    Returns the snapped trace points after checking they match the request.

    The API documents that each entry corresponds to the request trace point
    at the same index, but does not guarantee the array length, so both the
    length and the original positions are verified (within a small tolerance
    for float round-trips). A mismatch means the response cannot be joined
    to the input and is treated as broken.
    """
    points = data.get("SnappedTracePoints")
    if not isinstance(points, list) or len(points) != len(sent_positions):
        raise BrokenResponseError(
            "The snapped trace points do not match the sent trace points; "
            "no layer was created from this response."
        )
    normalized = []
    for index, (point, sent) in enumerate(zip(points, sent_positions)):
        if not isinstance(point, dict):
            raise BrokenResponseError(
                "The snapped trace points do not match the sent trace points; "
                "no layer was created from this response."
            )
        original = _position(point.get("OriginalPosition"))
        snapped = _position(point.get("SnappedPosition"))
        if original is None or snapped is None or not _close(original, sent):
            raise BrokenResponseError(
                "The snapped trace points do not match the sent trace points; "
                "no layer was created from this response."
            )
        confidence = point.get("Confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            finite = False
        else:
            try:
                finite = math.isfinite(confidence)
            except OverflowError:
                finite = False
        if not finite or not 0 <= confidence <= 1:
            raise BrokenResponseError(
                "The snapped trace points carry an invalid confidence value; "
                "no layer was created from this response."
            )
        normalized.append(
            {
                "index": index,
                "confidence": confidence,
                "snapped": snapped,
                "original": original,
            }
        )
    return normalized


def _close(first: list[float], second: list[float]) -> bool:
    """Returns whether two positions match within the float tolerance."""
    return (
        abs(first[0] - second[0]) <= POSITION_TOLERANCE
        and abs(first[1] - second[1]) <= POSITION_TOLERANCE
    )


def normalize_matrix(
    data: dict[str, Any],
    origins_count: int,
    destinations_count: int,
) -> list[list[dict[str, Any]]]:
    """
    Returns the route matrix as rows of ``distance`` / ``duration`` / ``error``.

    Cell-level errors are normal results: their validated distance and
    duration become ``None`` and the error code is kept. A malformed shape,
    cell, error code, distance, or duration is a broken response.
    """
    matrix = data.get("RouteMatrix")
    if not isinstance(matrix, list) or len(matrix) != origins_count:
        raise BrokenResponseError(
            "The route matrix size does not match the sent origins and "
            "destinations; no layer was created from this response."
        )
    rows = []
    for row in matrix:
        if not isinstance(row, list) or len(row) != destinations_count:
            raise BrokenResponseError(
                "The route matrix size does not match the sent origins and "
                "destinations; no layer was created from this response."
            )
        cells = []
        for cell in row:
            if not isinstance(cell, dict):
                raise BrokenResponseError(
                    "The route matrix contains a malformed cell; no layer "
                    "was created from this response."
                )
            error = ""
            if "Error" in cell:
                # Falsy or unknown Error values must not become successes.
                error = cell["Error"]
                if error not in MATRIX_ERROR_CODES:
                    raise BrokenResponseError(
                        "The route matrix contains an invalid error code; no "
                        "layer was created from this response."
                    )
            distance = _optional_long(cell, "Distance")
            duration = _optional_long(cell, "Duration")
            if distance is None or duration is None:
                raise BrokenResponseError(
                    "The route matrix contains a malformed cell; no layer "
                    "was created from this response."
                )
            if error:
                # Validate billed error-cell values, then keep them NULL so a
                # failed pair never looks like a zero-length route.
                distance = None
                duration = None
            cells.append({"distance": distance, "duration": duration, "error": error})
        rows.append(cells)
    return rows
