from __future__ import annotations

from urllib.parse import quote

from ..utils.configuration_handler import ConfigurationError, ConfigurationHandler
from ..utils.external_api_handler import ExternalApiHandler


class ServiceFunctionsBase:
    """
    Shared base for the Amazon Location Service feature classes.

    Holds the configuration and network handlers and builds the per-service
    endpoint URLs from the validated region and API key.
    """

    SERVICE_HOST = ""

    def __init__(self) -> None:
        """Initializes the configuration and API handlers shared by every operation."""
        self.configuration_handler = ConfigurationHandler()
        self.api_handler = ExternalApiHandler()

    def build_endpoint(
        self,
        path: str,
        query: str = "",
        *,
        credentials: tuple[str, str] | None = None,
    ) -> str:
        """
        Builds the full URL for a path such as ``"v2/geocode"``.

        The URL carries the ``?key=`` authorization parameter, and ``query``
        is appended after it with ``&``. When ``credentials`` is given, the
        captured ``(region, api_key)`` pair is validated and used without
        re-reading the settings, so a request keeps the credentials it
        started with; otherwise the current configuration is read. Either
        way a missing or invalid region or API key raises
        ``ConfigurationError``.
        """
        if credentials is None:
            credentials = self.configuration_handler.get_credentials()
        region, apikey = credentials
        # Captured credentials skip the settings read, not the safety checks:
        # the region is interpolated into a hostname below.
        if not ConfigurationHandler.REGION_PATTERN.match(region or ""):
            raise ConfigurationError("The captured region is not a valid region code.")
        apikey = (apikey or "").strip()
        if not apikey:
            raise ConfigurationError("The captured API key is empty.")
        host = self.SERVICE_HOST.format(region=region)
        # The key is user-provided free text; encode it so characters like
        # "&" or "#" cannot alter the query structure.
        url = f"https://{host}/{path}?key={quote(apikey, safe='')}"
        if query:
            url = f"{url}&{query}"
        return url
