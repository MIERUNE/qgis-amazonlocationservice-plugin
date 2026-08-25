from __future__ import annotations

import math
import re
from typing import Any

from .payload import prune_payload

MIN_RADIUS = 1
MAX_RADIUS = 21_000_000
MIN_RESULTS = 1
MAX_RESULTS = 100
MAX_NEXT_TOKEN_LENGTH = 2_000
MAX_QUERY_TEXT_LENGTH = 200
MAX_COUNTRIES = 100


def _validate_query_text(text: str) -> str:
    """Returns a non-empty Places query of at most 200 characters."""
    if not isinstance(text, str):
        raise ValueError("QueryText must be a string.")
    text = text.strip()
    if not text:
        raise ValueError("QueryText must be filled in.")
    if len(text) > MAX_QUERY_TEXT_LENGTH:
        raise ValueError(
            f"QueryText must not exceed {MAX_QUERY_TEXT_LENGTH} characters."
        )
    return text


def _validate_countries(countries: list[str] | None) -> list[str]:
    """Returns country codes accepted by Places filters."""
    if countries is None:
        return []
    if not isinstance(countries, (list, tuple)):
        raise ValueError("IncludeCountries must be a list of country codes.")
    if len(countries) > MAX_COUNTRIES:
        raise ValueError(
            f"IncludeCountries must contain at most {MAX_COUNTRIES} codes."
        )
    for code in countries:
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2,3}", code):
            raise ValueError(
                "IncludeCountries must use upper-case ISO alpha-2 or alpha-3 codes."
            )
    return list(countries)


def _validate_position(position: list[float], name: str) -> list[float]:
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


def _validate_bounding_box(bounding_box: list[float]) -> list[float]:
    """Returns a validated ``[west, south, east, north]`` bounding box."""
    if not isinstance(bounding_box, (list, tuple)) or len(bounding_box) != 4:
        raise ValueError("BoundingBox must contain west, south, east and north.")

    west, south, east, north = bounding_box
    values = (west, south, east, north)
    if any(isinstance(value, bool) for value in values):
        raise ValueError("BoundingBox must contain numeric coordinates.")
    if any(not isinstance(value, (int, float)) for value in values):
        raise ValueError("BoundingBox must contain numeric coordinates.")
    try:
        finite = all(math.isfinite(value) for value in values)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("BoundingBox must contain finite coordinates.")
    if not -180 <= west <= 180 or not -180 <= east <= 180:
        raise ValueError("BoundingBox longitude is outside the WGS 84 range.")
    if not -90 <= south <= 90 or not -90 <= north <= 90:
        raise ValueError("BoundingBox latitude is outside the WGS 84 range.")
    if south > north:
        raise ValueError("BoundingBox south must not be north of north.")

    return [float(west), float(south), float(east), float(north)]


def _validate_radius(radius: int, name: str) -> int:
    """Returns a radius accepted by the Places V2 APIs."""
    if isinstance(radius, bool) or not isinstance(radius, int):
        raise ValueError(f"{name} must be an integer number of metres.")
    if not MIN_RADIUS <= radius <= MAX_RADIUS:
        raise ValueError(f"{name} must be between {MIN_RADIUS} and {MAX_RADIUS}.")
    return radius


def _validate_max_results(max_results: int) -> int:
    """Returns a valid Places V2 page size."""
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise ValueError("MaxResults must be an integer.")
    if not MIN_RESULTS <= max_results <= MAX_RESULTS:
        raise ValueError(f"MaxResults must be between {MIN_RESULTS} and {MAX_RESULTS}.")
    return max_results


def _validate_next_token(next_token: str | None) -> str | None:
    """Returns a non-empty pagination token, or ``None`` when it is omitted."""
    if next_token in (None, ""):
        return None
    if not isinstance(next_token, str):
        raise ValueError("NextToken must be a string.")
    if len(next_token) > MAX_NEXT_TOKEN_LENGTH:
        raise ValueError(
            f"NextToken must not exceed {MAX_NEXT_TOKEN_LENGTH} characters."
        )
    return next_token


def _validate_circle(circle: dict[str, Any]) -> dict[str, Any]:
    """Returns a validated SearchText circle filter."""
    if not isinstance(circle, dict):
        raise ValueError("Circle must contain Center and Radius.")
    if "Center" not in circle or "Radius" not in circle:
        raise ValueError("Circle must contain Center and Radius.")
    return {
        "Center": _validate_position(circle["Center"], "Circle.Center"),
        "Radius": _validate_radius(circle["Radius"], "Circle.Radius"),
    }


def build_search_text_body(
    text: str,
    max_results: int,
    bias_position: list[float] | None = None,
    include_countries: list[str] | None = None,
    travel_mode: str | None = None,
    additional_features: list[str] | None = None,
    political_view: str | None = None,
    language: str | None = None,
    intended_use: str | None = None,
    *,
    next_token: str | None = None,
    bounding_box: list[float] | None = None,
    circle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds a SearchText body with exactly one geographic context."""
    contexts = (bias_position, bounding_box, circle)
    if sum(value is not None for value in contexts) != 1:
        raise ValueError(
            "SearchText requires exactly one of BiasPosition, BoundingBox or Circle."
        )

    filter_values: dict[str, Any] = {
        "IncludeCountries": _validate_countries(include_countries),
    }
    if bounding_box is not None:
        filter_values["BoundingBox"] = _validate_bounding_box(bounding_box)
    if circle is not None:
        filter_values["Circle"] = _validate_circle(circle)

    return prune_payload(
        {
            "QueryText": _validate_query_text(text),
            "MaxResults": _validate_max_results(max_results),
            "BiasPosition": (
                _validate_position(bias_position, "BiasPosition")
                if bias_position is not None
                else None
            ),
            "Filter": filter_values,
            "TravelMode": travel_mode,
            "AdditionalFeatures": additional_features,
            "PoliticalView": political_view,
            "Language": language,
            "IntendedUse": intended_use,
            "NextToken": _validate_next_token(next_token),
        }
    )


def build_geocode_body(
    text: str,
    max_results: int,
    bias_position: list[float] | None = None,
    include_countries: list[str] | None = None,
    address_names_mode: str | None = None,
    postal_code_mode: str | None = None,
    political_view: str | None = None,
    language: str | None = None,
    intended_use: str | None = None,
    additional_features: list[str] | None = None,
) -> dict[str, Any]:
    """Builds a Geocode request body."""
    return prune_payload(
        {
            "QueryText": _validate_query_text(text),
            "MaxResults": _validate_max_results(max_results),
            "BiasPosition": (
                _validate_position(bias_position, "BiasPosition")
                if bias_position is not None
                else None
            ),
            "Filter": {"IncludeCountries": _validate_countries(include_countries)},
            "AddressNamesMode": address_names_mode,
            "PostalCodeMode": postal_code_mode,
            "AdditionalFeatures": additional_features,
            "PoliticalView": political_view,
            "Language": language,
            "IntendedUse": intended_use,
        }
    )


def build_reverse_geocode_body(
    lon: float,
    lat: float,
    max_results: int = 1,
    query_radius: int | None = None,
    political_view: str | None = None,
    language: str | None = None,
    intended_use: str | None = None,
    additional_features: list[str] | None = None,
) -> dict[str, Any]:
    """Builds a ReverseGeocode request body."""
    return prune_payload(
        {
            "QueryPosition": _validate_position([lon, lat], "QueryPosition"),
            "MaxResults": _validate_max_results(max_results),
            "QueryRadius": (
                _validate_radius(query_radius, "QueryRadius")
                if query_radius is not None
                else None
            ),
            "AdditionalFeatures": additional_features,
            "PoliticalView": political_view,
            "Language": language,
            "IntendedUse": intended_use,
        }
    )


def build_search_nearby_body(
    lon: float,
    lat: float,
    query_radius: int,
    max_results: int,
    additional_features: list[str] | None = None,
    political_view: str | None = None,
    language: str | None = None,
    intended_use: str | None = None,
    *,
    next_token: str | None = None,
) -> dict[str, Any]:
    """Builds a SearchNearby request body."""
    return prune_payload(
        {
            "QueryPosition": _validate_position([lon, lat], "QueryPosition"),
            "QueryRadius": _validate_radius(query_radius, "QueryRadius"),
            "MaxResults": _validate_max_results(max_results),
            "AdditionalFeatures": additional_features,
            "PoliticalView": political_view,
            "Language": language,
            "IntendedUse": intended_use,
            "NextToken": _validate_next_token(next_token),
        }
    )
