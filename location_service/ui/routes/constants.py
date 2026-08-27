from __future__ import annotations

from ...functions.routes_capabilities import OPERATIONS

# Display order matches the pages in routes.ui; capabilities decide availability.
FUNCTIONS = OPERATIONS

FUNCTION_PAGE = {
    "CalculateRoutes": 0,
    "CalculateIsolines": 1,
    "SnapToRoads": 2,
    "CalculateRouteMatrix": 3,
}

RUN_BUTTON_LABEL = {
    "CalculateRoutes": "Get route",
    "CalculateIsolines": "Get areas",
    "SnapToRoads": "Snap to roads",
    "CalculateRouteMatrix": "Build matrix",
}

ISOLINE_DIRECTIONS = (
    ("Departure (from origin)", "Origin"),
    ("Arrival (to destination)", "Destination"),
)
THRESHOLD_TYPES = (
    ("Time (minutes)", "Time"),
    ("Distance (km)", "Distance"),
)
THRESHOLD_UNIT_LABEL = {
    "Time": "Values (minutes)",
    "Distance": "Values (km)",
}
THRESHOLD_PLACEHOLDER = {
    "Time": "5, 10, 15",
    "Distance": "1, 2, 5",
}
# How the checked transit modes are applied (AllowedModes and ExcludedModes
# cannot be combined in one request, so this is a single choice).
TRANSIT_FILTERS = (
    ("All modes allowed (default)", ""),
    ("Allow only checked modes", "allow"),
    ("Exclude checked modes", "exclude"),
)

AVOID_OPTIONS = (
    ("avoid_tollroads_checkBox", "TollRoads"),
    ("avoid_ferries_checkBox", "Ferries"),
    ("avoid_tunnels_checkBox", "Tunnels"),
    ("avoid_highways_checkBox", "ControlledAccessHighways"),
    ("avoid_dirtroads_checkBox", "DirtRoads"),
    ("avoid_uturns_checkBox", "UTurns"),
)

WAYPOINT_HINT = "Left-click to add waypoints, right-click to finish."
WAYPOINT_BUTTON_ADD = "Add Points"
WAYPOINT_BUTTON_ACTIVE = "Stop adding (right-click to finish)"

TRANSIT_DISABLED_TOOLTIP = "Not applicable to Transit / Intermodal routing."
REGION_DISABLED_TOOLTIP = "Not available in the configured region."
NO_ATTRIBUTIONS_TEXT = "No attributions to display yet."
