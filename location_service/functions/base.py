from __future__ import annotations

from urllib.parse import quote

from ..utils.configuration_handler import ConfigurationHandler
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

    def build_endpoint(self, path: str, query: str = "") -> str:
        """
        Builds the full URL for a path such as ``"v2/geocode"``.

        The URL carries the ``?key=`` authorization parameter, and ``query``
        is appended after it with ``&``. Raises ``ConfigurationError`` when
        the region or API key is missing or invalid.
        """
        region, apikey = self.configuration_handler.get_credentials()
        host = self.SERVICE_HOST.format(region=region)
        # The key is user-provided free text; encode it so characters like
        # "&" or "#" cannot alter the query structure.
        url = f"https://{host}/{path}?key={quote(apikey, safe='')}"
        if query:
            url = f"{url}&{query}"
        return url
