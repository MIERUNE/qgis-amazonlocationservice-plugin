from __future__ import annotations

import re

# Places operations exposed as Processing algorithms.
FUNCTIONS = ("SearchText", "Geocode", "ReverseGeocode", "SearchNearby")

# Default MaxResults per function (ReverseGeocode returns one place by default).
DEFAULT_MAX_RESULTS = {
    "SearchText": 10,
    "Geocode": 10,
    "ReverseGeocode": 1,
    "SearchNearby": 20,
}

# An empty language (or "Default") omits the parameter, and BCP 47 codes
# such as "ja", "en-US", or "en-US-u-ca-gregory" are accepted. The API caps
# the value at 35 characters.
LANGUAGE_DEFAULT = "Default"
LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*\Z")
LANGUAGE_MAX_LENGTH = 35
LANGUAGE_FORMAT_HINT = "Language must be a BCP 47 code such as ja, en or en-US."

# (display label, API value) vocabularies for the optional request parameters.
ADDRESS_NAMES_MODES = (
    ("Default (normalized)", ""),
    ("Matched (as typed)", "Matched"),
    ("Administrative (US only)", "Administrative"),
)
POSTAL_CODE_MODES = (
    ("Default", ""),
    ("Merge all spanned localities", "MergeAllSpannedLocalities"),
    ("Enumerate spanned localities", "EnumerateSpannedLocalities"),
    ("Enumerate spanned districts", "EnumerateSpannedDistricts"),
)
SEARCH_TRAVEL_MODES = (
    ("Default", ""),
    ("Car", "Car"),
    ("Scooter", "Scooter"),
    ("Truck", "Truck"),
)
# Places in these regions use the more limited GrabMaps feature set.
LIMITED_REGIONS = {"ap-southeast-1", "ap-southeast-5"}
LIMITED_LANGUAGES = {"en", "id", "km", "lo", "ms", "my", "pt", "th", "tl", "vi", "zh"}
LIMITED_MAX_RADIUS = 100_000
DEFAULT_MAX_RADIUS = 21_000_000
ADDITIONAL_FEATURES_BY_OPERATION = {
    "SearchText": ("Contact", "TimeZone"),
    "Geocode": ("TimeZone",),
    "ReverseGeocode": ("TimeZone",),
    "SearchNearby": ("Contact", "TimeZone"),
}


def automatic_additional_features(region: str, operation: str) -> list[str]:
    """Returns the details supported by an operation in the configured region."""
    features = ADDITIONAL_FEATURES_BY_OPERATION.get(operation, ())
    if region in LIMITED_REGIONS:
        return [feature for feature in features if feature == "TimeZone"]
    return list(features)


def validate_region_options(
    region: str,
    operation: str,
    language: str | None = None,
    political_view: str | None = None,
    additional_features: list[str] | None = None,
    intended_use: str | None = None,
    query_radius: int | None = None,
) -> None:
    """Raises ``ValueError`` for options unsupported in limited regions."""
    if region not in LIMITED_REGIONS:
        return
    if operation == "Geocode":
        raise ValueError(f"Geocode is not supported in {region}.")
    if operation == "GetPlace" and intended_use:
        raise ValueError(f"IntendedUse is not supported for GetPlace in {region}.")
    if political_view:
        raise ValueError(f"Political View is not supported in {region}.")
    if language and language not in LIMITED_LANGUAGES:
        raise ValueError(
            f"Language {language!r} is not supported in {region}. "
            f"Choose one of: {', '.join(sorted(LIMITED_LANGUAGES))}."
        )
    unsupported_features = set(additional_features or []) - {"TimeZone"}
    if unsupported_features:
        raise ValueError(
            f"Only the TimeZone additional feature is supported in {region}."
        )
    if query_radius is not None and query_radius > LIMITED_MAX_RADIUS:
        raise ValueError(
            f"QueryRadius must be {LIMITED_MAX_RADIUS:,} meters or less in {region}."
        )


def parse_language(text: str | None) -> str | None:
    """
    Returns the BCP 47 language code, or ``None`` for the API default.

    Raises ``ValueError`` when the value is not a BCP 47 code.
    """
    text = str(text or "").strip()
    if not text or text == LANGUAGE_DEFAULT:
        return None
    if len(text) > LANGUAGE_MAX_LENGTH or not LANGUAGE_PATTERN.match(text):
        raise ValueError(LANGUAGE_FORMAT_HINT)
    return text
