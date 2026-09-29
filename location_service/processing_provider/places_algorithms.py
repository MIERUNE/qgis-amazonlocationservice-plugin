from __future__ import annotations

from typing import Any

from qgis.core import (
    QgsCsException,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QVariant

from ..functions import places_capabilities as capabilities
from ..functions.places import (
    PlacesFunctions,
    first_contact,
    opening_hours,
    time_zone_name,
)
from ..functions.places_requests import MAX_RADIUS, MAX_RESULTS, parse_country_codes
from ..functions.places_storage import drawable_result_items
from ..ui.maps.constants import POLITICAL_VIEWS
from . import compat
from .base import WGS84, LocationServiceAlgorithm
from .inputs import field_text

# Every Places request is sent with IntendedUse=Storage, which allows the
# results to be kept in QGIS layers and files.
INTENDED_USE = "Storage"
MAX_PAGES = 5

PLACES_PERMISSION_HINT = (
    "The request was rejected (HTTP 403). Check that the API key allows the "
    "geo-places actions used by this algorithm on "
    "arn:aws:geo-places:Region::provider/default, and check the configured "
    "region, the key's expiry, and any client restrictions on the key."
)
STORAGE_NOTE = (
    "Requests are sent with IntendedUse=Storage so the results can be saved. "
    "Each request is billed by Amazon Location Service."
)


class PlacesAlgorithm(LocationServiceAlgorithm):
    """Shared parameters and output handling of the Places algorithms."""

    GROUP = "Places"
    GROUP_ID = "places"
    ICON = "ui/places/places.png"
    PERMISSION_HINT = PLACES_PERMISSION_HINT

    LANGUAGE = "LANGUAGE"
    POLITICAL_VIEW = "POLITICAL_VIEW"
    MAX_RESULTS = "MAX_RESULTS"
    OUTPUT = "OUTPUT"

    def add_localization_parameters(self) -> None:
        """Adds the Language and Political View parameters."""
        self.addParameter(
            QgsProcessingParameterString(
                self.LANGUAGE,
                "Language (BCP 47 code such as ja or en-US; empty for default)",
                defaultValue="",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.POLITICAL_VIEW,
                "Political view",
                options=[label for label, _value in POLITICAL_VIEWS],
                defaultValue=0,
            )
        )

    def add_max_results_parameter(self) -> None:
        """Adds the MaxResults parameter with the operation's default."""
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_RESULTS,
                "Maximum results",
                type=compat.NUMBER_INTEGER,
                defaultValue=capabilities.DEFAULT_MAX_RESULTS.get(self.OPERATION, 10),
                minValue=1,
                maxValue=MAX_RESULTS,
            )
        )

    def add_output_parameter(self) -> None:
        """Adds the point output."""
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, self.OPERATION, type=compat.SOURCE_POINT
            )
        )

    def localization(
        self, parameters: dict[str, Any], context: QgsProcessingContext
    ) -> tuple[str | None, str | None]:
        """Returns the validated ``(language, political view)`` values."""
        language = capabilities.parse_language(
            self.parameterAsString(parameters, self.LANGUAGE, context)
        )
        index = self.parameterAsEnum(parameters, self.POLITICAL_VIEW, context)
        political_view = (
            POLITICAL_VIEWS[index][1] if 0 <= index < len(POLITICAL_VIEWS) else ""
        )
        return language, political_view or None

    def position(
        self, parameters: dict[str, Any], name: str, context: QgsProcessingContext
    ) -> list[float] | None:
        """Returns a point parameter as WGS 84 ``[lon, lat]``, or ``None``."""
        if parameters.get(name) in (None, ""):
            return None
        try:
            point = self.parameterAsPoint(parameters, name, context, WGS84)
        except QgsCsException as error:
            raise ValueError("The point could not be transformed to WGS 84.") from error
        return [point.x(), point.y()]

    def validate_region(self, **options: Any) -> None:
        """Checks the options against the configured region."""
        capabilities.validate_region_options(self.region, self.OPERATION, **options)

    def publish(
        self,
        parameters: dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
        places: PlacesFunctions,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Writes the result items to the output and reports the count."""
        layer = places.build_result_layer(
            {"ResultItems": items}, self.OPERATION, INTENDED_USE
        )
        count = layer.featureCount()
        if count:
            feedback.pushInfo(f"{self.OPERATION} returned {count} place(s).")
        else:
            feedback.pushWarning(f"{self.OPERATION} returned no drawable results.")
        dest_id = self.write_layer(parameters, self.OUTPUT, context, layer)
        return {self.OUTPUT: dest_id}

    def fetch_pages(
        self,
        places: PlacesFunctions,
        request,
        max_pages: int,
        feedback: QgsProcessingFeedback,
    ) -> list[dict[str, Any]]:
        """
        Requests up to ``max_pages`` pages and returns the unique items.

        ``request`` takes a NextToken (``None`` for the first page). Items
        whose PlaceId was already returned by an earlier page are skipped.
        """
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        token = None
        for page in range(max_pages):
            if page:
                feedback.pushInfo(f"Requesting result page {page + 1}.")
            result = self.send(
                places.api_handler, lambda token=token: request(token), feedback
            )
            for item in drawable_result_items(result):
                place_id = str(item.get("PlaceId") or "")
                if place_id and place_id in seen:
                    continue
                if place_id:
                    seen.add(place_id)
                items.append(item)
            next_token = result.get("NextToken")
            if not next_token or next_token == token:
                break
            token = next_token
            feedback.setProgress(100 * (page + 1) / max_pages)
        return items


class SearchTextAlgorithm(PlacesAlgorithm):
    """SearchText: free-text place search around a bias position."""

    OPERATION = "SearchText"
    HELP = (
        "Searches for places by free text around a bias position.\n\n"
        f"{STORAGE_NOTE} With more than one page, each page is one request."
    )

    QUERY = "QUERY"
    BIAS_POSITION = "BIAS_POSITION"
    COUNTRIES = "COUNTRIES"
    TRAVEL_MODE = "TRAVEL_MODE"
    PAGES = "PAGES"

    def initAlgorithm(self, config=None) -> None:
        """Defines the SearchText parameters."""
        self.addParameter(QgsProcessingParameterString(self.QUERY, "Query text"))
        self.addParameter(
            QgsProcessingParameterPoint(self.BIAS_POSITION, "Bias position")
        )
        self.add_max_results_parameter()
        self.addParameter(
            QgsProcessingParameterString(
                self.COUNTRIES,
                "Countries (comma-separated ISO codes such as JP, JPN)",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.TRAVEL_MODE,
                "Travel mode",
                options=[label for label, _value in capabilities.SEARCH_TRAVEL_MODES],
                defaultValue=0,
            )
        )
        self.add_localization_parameters()
        self.addParameter(
            compat.set_advanced(
                QgsProcessingParameterNumber(
                    self.PAGES,
                    "Maximum result pages",
                    type=compat.NUMBER_INTEGER,
                    defaultValue=1,
                    minValue=1,
                    maxValue=MAX_PAGES,
                )
            )
        )
        self.add_output_parameter()

    def execute(self, parameters, context, feedback) -> dict[str, Any]:
        """Sends SearchText and writes the results."""
        query = self.parameterAsString(parameters, self.QUERY, context)
        bias = self.position(parameters, self.BIAS_POSITION, context)
        if bias is None:
            raise ValueError("SearchText requires a bias position.")
        max_results = self.parameterAsInt(parameters, self.MAX_RESULTS, context)
        countries = parse_country_codes(
            self.parameterAsString(parameters, self.COUNTRIES, context)
        )
        travel_index = self.parameterAsEnum(parameters, self.TRAVEL_MODE, context)
        travel_mode = capabilities.SEARCH_TRAVEL_MODES[travel_index][1] or None
        language, political_view = self.localization(parameters, context)
        pages = self.parameterAsInt(parameters, self.PAGES, context)
        features = capabilities.automatic_additional_features(
            self.region, self.OPERATION
        )
        self.validate_region(
            language=language,
            political_view=political_view,
            additional_features=features,
            intended_use=INTENDED_USE,
        )
        places = PlacesFunctions()

        def request(token):
            return places.search_text(
                query,
                max_results,
                bias[0],
                bias[1],
                include_countries=countries,
                travel_mode=travel_mode,
                additional_features=features,
                political_view=political_view,
                language=language,
                intended_use=INTENDED_USE,
                next_token=token,
                credentials=self.credentials,
            )

        items = self.fetch_pages(places, request, pages, feedback)
        return self.publish(parameters, context, feedback, places, items)


class GeocodeAlgorithm(PlacesAlgorithm):
    """Geocode: converts an address or place name into coordinates."""

    OPERATION = "Geocode"
    HELP = f"Converts an address or place name into coordinates.\n\n{STORAGE_NOTE}"

    QUERY = "QUERY"
    BIAS_POSITION = "BIAS_POSITION"
    COUNTRIES = "COUNTRIES"
    ADDRESS_NAMES_MODE = "ADDRESS_NAMES_MODE"
    POSTAL_CODE_MODE = "POSTAL_CODE_MODE"

    def initAlgorithm(self, config=None) -> None:
        """Defines the Geocode parameters."""
        self.addParameter(
            QgsProcessingParameterString(self.QUERY, "Address or place name")
        )
        self.addParameter(
            QgsProcessingParameterPoint(
                self.BIAS_POSITION, "Bias position", optional=True
            )
        )
        self.add_max_results_parameter()
        self.addParameter(
            QgsProcessingParameterString(
                self.COUNTRIES,
                "Countries (comma-separated ISO codes such as JP, JPN)",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.ADDRESS_NAMES_MODE,
                "Address names mode",
                options=[label for label, _value in capabilities.ADDRESS_NAMES_MODES],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.POSTAL_CODE_MODE,
                "Postal code mode",
                options=[label for label, _value in capabilities.POSTAL_CODE_MODES],
                defaultValue=0,
            )
        )
        self.add_localization_parameters()
        self.add_output_parameter()

    def execute(self, parameters, context, feedback) -> dict[str, Any]:
        """Sends Geocode and writes the results."""
        query = self.parameterAsString(parameters, self.QUERY, context)
        bias = self.position(parameters, self.BIAS_POSITION, context)
        lon, lat = bias if bias is not None else (None, None)
        max_results = self.parameterAsInt(parameters, self.MAX_RESULTS, context)
        countries = parse_country_codes(
            self.parameterAsString(parameters, self.COUNTRIES, context)
        )
        address_index = self.parameterAsEnum(
            parameters, self.ADDRESS_NAMES_MODE, context
        )
        postal_index = self.parameterAsEnum(parameters, self.POSTAL_CODE_MODE, context)
        language, political_view = self.localization(parameters, context)
        features = capabilities.automatic_additional_features(
            self.region, self.OPERATION
        )
        self.validate_region(
            language=language,
            political_view=political_view,
            additional_features=features,
            intended_use=INTENDED_USE,
        )
        places = PlacesFunctions()
        result = self.send(
            places.api_handler,
            lambda: places.geocode(
                query,
                max_results,
                lon,
                lat,
                include_countries=countries,
                address_names_mode=(
                    capabilities.ADDRESS_NAMES_MODES[address_index][1] or None
                ),
                postal_code_mode=capabilities.POSTAL_CODE_MODES[postal_index][1]
                or None,
                political_view=political_view,
                language=language,
                intended_use=INTENDED_USE,
                additional_features=features,
                credentials=self.credentials,
            ),
            feedback,
        )
        items = drawable_result_items(result)
        return self.publish(parameters, context, feedback, places, items)


class ReverseGeocodeAlgorithm(PlacesAlgorithm):
    """ReverseGeocode: converts a position into the nearest address(es)."""

    OPERATION = "ReverseGeocode"
    HELP = (
        "Converts a position into the nearest address(es). A query radius of "
        f"0 leaves the radius unset.\n\n{STORAGE_NOTE}"
    )

    POSITION = "POSITION"
    QUERY_RADIUS = "QUERY_RADIUS"

    def initAlgorithm(self, config=None) -> None:
        """Defines the ReverseGeocode parameters."""
        self.addParameter(QgsProcessingParameterPoint(self.POSITION, "Query position"))
        self.addParameter(
            QgsProcessingParameterNumber(
                self.QUERY_RADIUS,
                "Query radius (meters, 0 = unset)",
                type=compat.NUMBER_INTEGER,
                defaultValue=0,
                minValue=0,
                maxValue=MAX_RADIUS,
            )
        )
        self.add_max_results_parameter()
        self.add_localization_parameters()
        self.add_output_parameter()

    def execute(self, parameters, context, feedback) -> dict[str, Any]:
        """Sends ReverseGeocode and writes the results."""
        position = self.position(parameters, self.POSITION, context)
        if position is None:
            raise ValueError("ReverseGeocode requires a query position.")
        radius = self.parameterAsInt(parameters, self.QUERY_RADIUS, context) or None
        max_results = self.parameterAsInt(parameters, self.MAX_RESULTS, context)
        language, political_view = self.localization(parameters, context)
        features = capabilities.automatic_additional_features(
            self.region, self.OPERATION
        )
        self.validate_region(
            language=language,
            political_view=political_view,
            additional_features=features,
            intended_use=INTENDED_USE,
            query_radius=radius,
        )
        places = PlacesFunctions()
        result = self.send(
            places.api_handler,
            lambda: places.reverse_geocode(
                position[0],
                position[1],
                max_results,
                radius,
                political_view=political_view,
                language=language,
                intended_use=INTENDED_USE,
                additional_features=features,
                credentials=self.credentials,
            ),
            feedback,
        )
        items = drawable_result_items(result)
        return self.publish(parameters, context, feedback, places, items)


class SearchNearbyAlgorithm(PlacesAlgorithm):
    """SearchNearby: points of interest within a radius of a position."""

    OPERATION = "SearchNearby"
    HELP = (
        "Searches for points of interest within a radius of a position.\n\n"
        f"{STORAGE_NOTE} With more than one page, each page is one request."
    )

    POSITION = "POSITION"
    QUERY_RADIUS = "QUERY_RADIUS"
    PAGES = "PAGES"

    def initAlgorithm(self, config=None) -> None:
        """Defines the SearchNearby parameters."""
        self.addParameter(QgsProcessingParameterPoint(self.POSITION, "Query position"))
        self.addParameter(
            QgsProcessingParameterNumber(
                self.QUERY_RADIUS,
                "Query radius (meters)",
                type=compat.NUMBER_INTEGER,
                defaultValue=1000,
                minValue=1,
                maxValue=MAX_RADIUS,
            )
        )
        self.add_max_results_parameter()
        self.add_localization_parameters()
        self.addParameter(
            compat.set_advanced(
                QgsProcessingParameterNumber(
                    self.PAGES,
                    "Maximum result pages",
                    type=compat.NUMBER_INTEGER,
                    defaultValue=1,
                    minValue=1,
                    maxValue=MAX_PAGES,
                )
            )
        )
        self.add_output_parameter()

    def execute(self, parameters, context, feedback) -> dict[str, Any]:
        """Sends SearchNearby and writes the results."""
        position = self.position(parameters, self.POSITION, context)
        if position is None:
            raise ValueError("SearchNearby requires a query position.")
        radius = self.parameterAsInt(parameters, self.QUERY_RADIUS, context)
        max_results = self.parameterAsInt(parameters, self.MAX_RESULTS, context)
        language, political_view = self.localization(parameters, context)
        pages = self.parameterAsInt(parameters, self.PAGES, context)
        features = capabilities.automatic_additional_features(
            self.region, self.OPERATION
        )
        self.validate_region(
            language=language,
            political_view=political_view,
            additional_features=features,
            intended_use=INTENDED_USE,
            query_radius=radius,
        )
        places = PlacesFunctions()

        def request(token):
            return places.search_nearby(
                position[0],
                position[1],
                radius,
                max_results,
                additional_features=features,
                political_view=political_view,
                language=language,
                intended_use=INTENDED_USE,
                next_token=token,
                credentials=self.credentials,
            )

        items = self.fetch_pages(places, request, pages, feedback)
        return self.publish(parameters, context, feedback, places, items)


class GetPlaceAlgorithm(PlacesAlgorithm):
    """GetPlace: adds contact, opening-hours and time-zone details."""

    OPERATION = "GetPlace"
    HELP = (
        "Copies a point layer and fills the Phone, Website, OpeningHours and "
        "TimeZone fields with GetPlace details for each PlaceId. Existing "
        "detail fields are overwritten.\n\n"
        f"Each unique PlaceId sends one billable Storage request; at most "
        f"{PlacesFunctions.MAX_ENRICH_FEATURES} unique places are accepted. "
        "Use 'Selected features only' to limit the input."
    )

    INPUT = "INPUT"
    PLACE_ID_FIELD = "PLACE_ID_FIELD"

    def displayName(self) -> str:
        """Returns the toolbox name, which says what the algorithm adds."""
        return "GetPlace (add place details)"

    def initAlgorithm(self, config=None) -> None:
        """Defines the GetPlace parameters."""
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT, "Places layer", [compat.SOURCE_POINT]
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.PLACE_ID_FIELD,
                "PlaceId field",
                defaultValue=PlacesFunctions.FIELD_PLACE_ID,
                parentLayerParameterName=self.INPUT,
                type=compat.FIELD_ANY,
            )
        )
        self.add_localization_parameters()
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, "GetPlace details", type=compat.SOURCE_POINT
            )
        )

    def execute(self, parameters, context, feedback) -> dict[str, Any]:
        """Requests the details of each unique PlaceId and writes the copy."""
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(
                self.invalidSourceError(parameters, self.INPUT)
            )
        place_id_field = self.parameterAsString(
            parameters, self.PLACE_ID_FIELD, context
        )
        if source.fields().indexOf(place_id_field) < 0:
            raise ValueError("Select the field that contains the PlaceId values.")
        language, political_view = self.localization(parameters, context)
        features = list(PlacesFunctions.ENRICH_FEATURES)
        self.validate_region(
            language=language,
            political_view=political_view,
            additional_features=features,
            intended_use=INTENDED_USE,
        )

        # Check the output schema before any billed request is sent.
        fields = self._detail_fields(source.fields())
        input_features = list(source.getFeatures())
        place_ids = self._unique_place_ids(input_features, place_id_field)
        details = self._fetch_details(
            place_ids, language, political_view, features, feedback
        )

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            source.wkbType(),
            source.sourceCrs(),
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        output = []
        for feature in input_features:
            copy = QgsFeature(fields)
            copy.setGeometry(feature.geometry())
            for field in source.fields():
                copy.setAttribute(field.name(), feature[field.name()])
            for name, value in details[field_text(feature, place_id_field)].items():
                copy.setAttribute(name, value)
            output.append(copy)
        if output and not sink.addFeatures(output):
            raise QgsProcessingException("Could not write the GetPlace output.")
        feedback.pushInfo(
            f"Added details for {len(details)} unique place(s) to "
            f"{len(output)} feature(s)."
        )
        return {self.OUTPUT: dest_id}

    @staticmethod
    def _unique_place_ids(features: list[QgsFeature], place_id_field: str) -> list:
        """Returns the unique PlaceIds in input order, within the request cap."""
        place_ids = []
        for feature in features:
            place_id = field_text(feature, place_id_field)
            if not place_id:
                raise ValueError("One or more features have no PlaceId value.")
            if place_id not in place_ids:
                place_ids.append(place_id)
        if len(place_ids) > PlacesFunctions.MAX_ENRICH_FEATURES:
            raise ValueError(
                f"{len(place_ids)} unique places were given. GetPlace sends one "
                "billable Storage request per unique PlaceId; use at most "
                f"{PlacesFunctions.MAX_ENRICH_FEATURES} unique places."
            )
        return place_ids

    def _fetch_details(
        self,
        place_ids: list[str],
        language: str | None,
        political_view: str | None,
        features: list[str],
        feedback: QgsProcessingFeedback,
    ) -> dict[str, dict[str, str]]:
        """Sends one GetPlace request per PlaceId and returns the detail values."""
        places = PlacesFunctions()
        details: dict[str, dict[str, str]] = {}
        for index, place_id in enumerate(place_ids):
            detail = self.send(
                places.api_handler,
                lambda place_id=place_id: places.get_place(
                    place_id,
                    features,
                    political_view,
                    language,
                    INTENDED_USE,
                    credentials=self.credentials,
                ),
                feedback,
            )
            details[place_id] = {
                PlacesFunctions.FIELD_PHONE: first_contact(detail, "Phones"),
                PlacesFunctions.FIELD_WEBSITE: first_contact(detail, "Websites"),
                PlacesFunctions.FIELD_OPENING_HOURS: opening_hours(detail),
                PlacesFunctions.FIELD_TIMEZONE: time_zone_name(detail),
            }
            feedback.setProgress(100 * (index + 1) / len(place_ids))
        return details

    @staticmethod
    def _detail_fields(source_fields: QgsFields) -> QgsFields:
        """Returns the input fields plus any missing text detail fields."""
        fields = QgsFields(source_fields)
        for name in PlacesFunctions.DETAIL_FIELDS:
            index = fields.indexOf(name)
            if index >= 0 and fields.field(index).type() != QVariant.String:
                raise ValueError(
                    f"The existing {name} field must be a text field before "
                    "adding details."
                )
            if index < 0:
                fields.append(QgsField(name, QVariant.String))
        return fields
