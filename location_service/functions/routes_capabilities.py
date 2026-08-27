from __future__ import annotations

from .routes_requests import (
    AVOID_KEYS,
    MAX_ALTERNATIVES,
    ROUTES_TRAVEL_MODES,
    TRAVEL_MODES,
)

# Routes in these regions use the more limited GrabMaps feature set.
GRAB_REGIONS = {"ap-southeast-1", "ap-southeast-5"}

OPERATIONS = (
    "CalculateRoutes",
    "CalculateIsolines",
    "SnapToRoads",
    "CalculateRouteMatrix",
)
GRAB_OPERATIONS = ("CalculateRoutes", "CalculateRouteMatrix")

GRAB_TRAVEL_MODES = ("Car", "Pedestrian", "Scooter")
GRAB_AVOID_KEYS = ("TollRoads", "Ferries", "ControlledAccessHighways")
GRAB_MAX_ALTERNATIVES = 3

# Per-axis AWS limits for an Unbounded route matrix. The plugin also caps
# the total cell count at routes_requests.MAX_MATRIX_CELLS in every region.
MAX_MATRIX_ORIGINS = 15
MAX_MATRIX_DESTINATIONS = 100
GRAB_MAX_MATRIX_ORIGINS = 350
GRAB_MAX_MATRIX_DESTINATIONS = 350


def is_grab_region(region: str) -> bool:
    """Returns whether the region uses the GrabMaps data provider."""
    return region in GRAB_REGIONS


def available_operations(region: str) -> tuple[str, ...]:
    """Returns the Routes operations available in the region."""
    if is_grab_region(region):
        return GRAB_OPERATIONS
    return OPERATIONS


def allowed_travel_modes(region: str, operation: str) -> tuple[str, ...]:
    """Returns the travel modes the region and operation support."""
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown operation: {operation}")
    if is_grab_region(region):
        return GRAB_TRAVEL_MODES
    if operation == "CalculateRoutes":
        return ROUTES_TRAVEL_MODES
    return TRAVEL_MODES


def allowed_avoid_keys(region: str) -> tuple[str, ...]:
    """Returns the CalculateRoutes avoidance options the region supports."""
    if is_grab_region(region):
        return GRAB_AVOID_KEYS
    return AVOID_KEYS


def max_alternatives(region: str) -> int:
    """Returns the MaxAlternatives limit for the region."""
    if is_grab_region(region):
        return GRAB_MAX_ALTERNATIVES
    return MAX_ALTERNATIVES


def arrival_time_allowed(region: str) -> bool:
    """Returns whether CalculateRoutes accepts ArrivalTime in the region."""
    return not is_grab_region(region)


def matrix_axis_limits(region: str) -> tuple[int, int]:
    """Returns the AWS ``(max origins, max destinations)`` for the region."""
    if is_grab_region(region):
        return GRAB_MAX_MATRIX_ORIGINS, GRAB_MAX_MATRIX_DESTINATIONS
    return MAX_MATRIX_ORIGINS, MAX_MATRIX_DESTINATIONS


def validate_region_options(
    region: str,
    operation: str,
    travel_mode: str | None = None,
    avoid: tuple[str, ...] = (),
    max_alternatives_value: int | None = None,
    arrival_time: str | None = None,
    origins_count: int | None = None,
    destinations_count: int | None = None,
) -> None:
    """
    Raises ``ValueError`` for options the configured region does not support.

    This module is the single source of region capabilities: the dialog uses
    it to enable and disable controls, and the same checks run again right
    before a request is sent.
    """
    if operation not in available_operations(region):
        raise ValueError(f"{operation} is not supported in {region}.")
    if travel_mode and travel_mode not in allowed_travel_modes(region, operation):
        raise ValueError(f"Travel mode {travel_mode!r} is not supported in {region}.")
    unsupported_avoid = set(avoid) - set(allowed_avoid_keys(region))
    if unsupported_avoid:
        raise ValueError(
            f"Avoid options not supported in {region}: "
            f"{', '.join(sorted(unsupported_avoid))}."
        )
    if max_alternatives_value is not None and max_alternatives_value > max_alternatives(
        region
    ):
        raise ValueError(
            f"MaxAlternatives must be at most {max_alternatives(region)} in {region}."
        )
    if arrival_time and not arrival_time_allowed(region):
        raise ValueError(f"ArrivalTime is not supported in {region}.")
    max_origins, max_destinations = matrix_axis_limits(region)
    if origins_count is not None and origins_count > max_origins:
        raise ValueError(f"Origins must contain at most {max_origins} points.")
    if destinations_count is not None and destinations_count > max_destinations:
        raise ValueError(
            f"Destinations must contain at most {max_destinations} points."
        )
