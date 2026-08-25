from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsSimpleMarkerSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from .base import ServiceFunctionsBase
from .places_requests import (
    build_geocode_body,
    build_reverse_geocode_body,
    build_search_nearby_body,
    build_search_text_body,
)
from .places_storage import normalize_country_code, result_position


def category_names(result: dict[str, Any]) -> str:
    """Joins the category names of a place result with ``"; "``."""
    names = []
    for category in result.get("Categories") or []:
        name = (category or {}).get("Name") or ""
        if name:
            names.append(name)
    return "; ".join(names)


def first_contact(detail: dict[str, Any], kind: str) -> str:
    """Returns the first contact value of the given kind (e.g. ``"Phones"``)."""
    for entry in (detail.get("Contacts") or {}).get(kind) or []:
        value = (entry or {}).get("Value") or ""
        if value:
            return value
    return ""


def opening_hours(detail: dict[str, Any]) -> str:
    """Joins the displayable opening-hours strings of a place with ``"; "``."""
    displays: list[str] = []
    for entry in detail.get("OpeningHours") or []:
        displays.extend((entry or {}).get("Display") or [])
    return "; ".join(displays)


def time_zone_name(detail: dict[str, Any]) -> str:
    """Returns the time-zone name of a place, or an empty string."""
    return (detail.get("TimeZone") or {}).get("Name") or ""


def country_code_for_feature(country: dict[str, Any]) -> str:
    """Returns a normalized Code3, falling back to Code2 when needed."""
    code3 = normalize_country_code(country.get("Code3"))
    if code3 is not None and len(code3) == 3:
        return code3

    code2 = normalize_country_code(country.get("Code2"))
    if code2 is not None and len(code2) == 2:
        return code2
    return ""


class PlacesOperationCancelledError(RuntimeError):
    """Raised when cancellation or a layer change stops a Places operation."""


class PlacesFunctions(ServiceFunctionsBase):
    """Runs Amazon Location Places V2 searches and creates their point layers."""

    SERVICE_HOST = "places.geo.{region}.amazonaws.com"

    DEFAULT_MAX_RESULTS = 10
    # Detail updates send one billable Storage request per unique PlaceId.
    MAX_ENRICH_FEATURES = 25
    ENRICH_FEATURES = ("Contact", "TimeZone")

    WGS84_CRS = "EPSG:4326"
    LAYER_TYPE = "Point"

    FIELD_TITLE = "Title"
    FIELD_PLACE_ID = "PlaceId"
    FIELD_PLACE_TYPE = "PlaceType"
    FIELD_REGION = "Region"
    FIELD_LOCALITY = "Locality"
    FIELD_LABEL = "Label"
    FIELD_COUNTRY_CODE = "CountryCode"
    FIELD_POSTAL_CODE = "PostalCode"
    FIELD_SOURCE_OPERATION = "SourceOperation"
    FIELD_DISTANCE = "Distance"
    FIELD_CATEGORIES = "Categories"

    FIELD_PHONE = "Phone"
    FIELD_WEBSITE = "Website"
    FIELD_OPENING_HOURS = "OpeningHours"
    FIELD_TIMEZONE = "TimeZone"
    DETAIL_FIELDS = (FIELD_PHONE, FIELD_WEBSITE, FIELD_OPENING_HOURS, FIELD_TIMEZONE)

    RESULT_FIELDS = (
        (FIELD_TITLE, QVariant.String),
        (FIELD_PLACE_ID, QVariant.String),
        (FIELD_PLACE_TYPE, QVariant.String),
        (FIELD_REGION, QVariant.String),
        (FIELD_LOCALITY, QVariant.String),
        (FIELD_LABEL, QVariant.String),
        (FIELD_COUNTRY_CODE, QVariant.String),
        (FIELD_POSTAL_CODE, QVariant.String),
        (FIELD_SOURCE_OPERATION, QVariant.String),
        (FIELD_DISTANCE, QVariant.Double),
        (FIELD_CATEGORIES, QVariant.String),
    )

    # Layer custom properties recording where a result layer came from.
    # Credentials, URLs, and raw responses are deliberately never stored.
    PROPERTY_MARKER = "als_places/layer"
    PROPERTY_SCHEMA_VERSION = "als_places/schema_version"
    PROPERTY_SOURCE_OPERATION = "als_places/source_operation"
    PROPERTY_INTENDED_USE = "als_places/intended_use"
    PROPERTY_RETRIEVED_AT = "als_places/retrieved_at"
    SCHEMA_VERSION = 1

    SYMBOL_SHAPE = QgsSimpleMarkerSymbolLayer.Shape.Circle
    SYMBOL_COLOR = QColor(0, 124, 191)
    SYMBOL_SIZE = 3.0
    LABEL_TEXT_COLOR = QColor("black")
    LABEL_TEXT_SIZE = 10

    # Keep the existing class-level API while the pure functions remain usable
    # without importing QGIS.
    build_search_text_body = staticmethod(build_search_text_body)
    build_geocode_body = staticmethod(build_geocode_body)
    build_reverse_geocode_body = staticmethod(build_reverse_geocode_body)
    build_search_nearby_body = staticmethod(build_search_nearby_body)

    def search_text(
        self,
        text: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        lon: float | None = None,
        lat: float | None = None,
        include_countries: list[str] | None = None,
        travel_mode: str | None = None,
        additional_features: list[str] | None = None,
        political_view: str | None = None,
        language: str | None = None,
        intended_use: str | None = None,
        next_token: str | None = None,
        *,
        credentials: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Searches for places matching free text, optionally biased to a position."""
        bias = [lon, lat] if lon is not None and lat is not None else None
        body = self.build_search_text_body(
            text,
            max_results,
            bias,
            include_countries,
            travel_mode,
            additional_features,
            political_view,
            language,
            intended_use,
            next_token=next_token,
        )
        return self.api_handler.send_json_post_request(
            self._build_places_endpoint("v2/search-text", credentials=credentials), body
        )

    def geocode(
        self,
        text: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        lon: float | None = None,
        lat: float | None = None,
        include_countries: list[str] | None = None,
        address_names_mode: str | None = None,
        postal_code_mode: str | None = None,
        political_view: str | None = None,
        language: str | None = None,
        intended_use: str | None = None,
        additional_features: list[str] | None = None,
        *,
        credentials: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Converts an address or place name into coordinates."""
        if (lon is None) != (lat is None):
            raise ValueError("BiasPosition requires both longitude and latitude.")
        bias = [lon, lat] if lon is not None and lat is not None else None
        body = self.build_geocode_body(
            text,
            max_results,
            bias,
            include_countries,
            address_names_mode,
            postal_code_mode,
            political_view,
            language,
            intended_use,
            additional_features,
        )
        return self.api_handler.send_json_post_request(
            self._build_places_endpoint("v2/geocode", credentials=credentials), body
        )

    def reverse_geocode(
        self,
        lon: float,
        lat: float,
        max_results: int = 1,
        query_radius: int | None = None,
        political_view: str | None = None,
        language: str | None = None,
        intended_use: str | None = None,
        additional_features: list[str] | None = None,
        *,
        credentials: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Converts a position into the nearest address(es)."""
        body = self.build_reverse_geocode_body(
            lon,
            lat,
            max_results,
            query_radius,
            political_view,
            language,
            intended_use,
            additional_features,
        )
        return self.api_handler.send_json_post_request(
            self._build_places_endpoint("v2/reverse-geocode", credentials=credentials),
            body,
        )

    def search_nearby(
        self,
        lon: float,
        lat: float,
        query_radius: int,
        max_results: int = DEFAULT_MAX_RESULTS,
        additional_features: list[str] | None = None,
        political_view: str | None = None,
        language: str | None = None,
        intended_use: str | None = None,
        next_token: str | None = None,
        *,
        credentials: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Searches for points of interest within a radius of a position."""
        body = self.build_search_nearby_body(
            lon,
            lat,
            query_radius,
            max_results,
            additional_features,
            political_view,
            language,
            intended_use,
            next_token=next_token,
        )
        return self.api_handler.send_json_post_request(
            self._build_places_endpoint("v2/search-nearby", credentials=credentials),
            body,
        )

    def get_place(
        self,
        place_id: str,
        additional_features: list[str] | None = None,
        political_view: str | None = None,
        language: str | None = None,
        intended_use: str | None = None,
        *,
        credentials: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Retrieves detailed information for a single place by its PlaceId."""
        params: list[tuple[str, str]] = []
        if additional_features:
            # Each feature is its own query parameter: the API takes a list of
            # enum values, not one comma-joined value.
            params += [("additional-features", value) for value in additional_features]
        if political_view:
            params.append(("political-view", political_view))
        if language:
            params.append(("language", language))
        if intended_use:
            params.append(("intended-use", intended_use))
        path = f"v2/place/{quote(place_id, safe='')}"
        query = urlencode(params)
        url = self._build_places_endpoint(path, query, credentials)
        return self.api_handler.send_json_get_request(url)

    def _build_places_endpoint(
        self,
        path: str,
        query: str = "",
        credentials: tuple[str, str] | None = None,
    ) -> str:
        """Builds an endpoint, optionally using one captured credential pair."""
        if credentials is None:
            return self.build_endpoint(path, query)
        region, api_key = credentials
        host = self.SERVICE_HOST.format(region=region)
        url = f"https://{host}/{path}?key={quote(api_key, safe='')}"
        if query:
            url = f"{url}&{query}"
        return url

    def add_point_layer(
        self,
        data: dict[str, Any],
        operation: str,
        intended_use: str | None = None,
    ) -> QgsVectorLayer:
        """Adds search results to the current project as a point layer."""
        layer = QgsVectorLayer(
            f"{self.LAYER_TYPE}?crs={self.WGS84_CRS}", operation, "memory"
        )
        self.setup_layer(layer, data, operation, intended_use)
        return layer

    def setup_layer(
        self,
        layer: QgsVectorLayer,
        data: dict[str, Any],
        operation: str,
        intended_use: str | None = None,
    ) -> None:
        """Populates, styles, and adds the point layer to the project."""
        if intended_use != "Storage":
            raise ValueError(
                "Places results can only be added to QGIS after a Storage request."
            )
        self.add_attributes(layer)
        self.add_features(layer, data, operation)
        self.apply_layer_style(layer)
        self.apply_label_style(layer)
        self.record_layer_source(layer, operation, intended_use)
        layer.triggerRepaint()
        QgsProject.instance().addMapLayer(layer)

    def add_attributes(self, layer: QgsVectorLayer) -> None:
        """Adds place-result fields to the layer."""
        fields = QgsFields()
        for name, field_type in self.RESULT_FIELDS:
            fields.append(QgsField(name, field_type))
        for name in self.DETAIL_FIELDS:
            fields.append(QgsField(name, QVariant.String))
        layer.dataProvider().addAttributes(fields)
        layer.updateFields()

    def add_features(
        self,
        layer: QgsVectorLayer,
        data: dict[str, Any],
        operation: str = "",
    ) -> int:
        """Adds drawable results, ignoring items without a usable position."""
        features = []
        for result in data.get("ResultItems") or []:
            if not isinstance(result, dict):
                continue
            position = result_position(result)
            if position is None:
                continue
            address = result.get("Address") or {}
            country = address.get("Country") or {}
            region = address.get("Region") or {}
            feature = QgsFeature(layer.fields())
            feature.setGeometry(
                QgsGeometry.fromPointXY(QgsPointXY(position[0], position[1]))
            )
            attributes = {
                self.FIELD_TITLE: result.get("Title") or "",
                self.FIELD_PLACE_ID: result.get("PlaceId") or "",
                self.FIELD_PLACE_TYPE: result.get("PlaceType") or "",
                self.FIELD_REGION: region.get("Name") or "",
                self.FIELD_LOCALITY: address.get("Locality") or "",
                self.FIELD_LABEL: address.get("Label") or "",
                self.FIELD_COUNTRY_CODE: country_code_for_feature(country),
                self.FIELD_POSTAL_CODE: address.get("PostalCode") or "",
                self.FIELD_SOURCE_OPERATION: operation,
                # Distance is allowed to stay NULL; it must not become zero.
                self.FIELD_DISTANCE: result.get("Distance"),
                self.FIELD_CATEGORIES: category_names(result),
            }
            attributes.update(
                {
                    self.FIELD_PHONE: first_contact(result, "Phones"),
                    self.FIELD_WEBSITE: first_contact(result, "Websites"),
                    self.FIELD_OPENING_HOURS: opening_hours(result),
                    self.FIELD_TIMEZONE: time_zone_name(result),
                }
            )
            for name, value in attributes.items():
                if layer.fields().indexOf(name) >= 0:
                    feature.setAttribute(name, value)
            features.append(feature)
        if not features:
            return 0
        if layer.isEditable():
            added = layer.addFeatures(features)
            added_count = len(features)
        else:
            added, added_features = layer.dataProvider().addFeatures(features)
            added_count = len(added_features)
        if not added:
            raise RuntimeError("Could not add Places features to the layer.")
        return added_count

    def apply_layer_style(self, layer: QgsVectorLayer) -> None:
        """Applies the point symbol to the layer."""
        symbol_layer = QgsSimpleMarkerSymbolLayer()
        symbol_layer.setShape(self.SYMBOL_SHAPE)
        symbol_layer.setColor(self.SYMBOL_COLOR)
        symbol_layer.setSize(self.SYMBOL_SIZE)
        symbol = QgsMarkerSymbol.createSimple({})
        symbol.changeSymbolLayer(0, symbol_layer)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    def apply_label_style(self, layer: QgsVectorLayer) -> None:
        """Applies title labeling to the layer."""
        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = self.FIELD_TITLE
        label_settings.enabled = True
        text_format = QgsTextFormat()
        text_format.setSize(self.LABEL_TEXT_SIZE)
        text_format.setColor(self.LABEL_TEXT_COLOR)
        label_settings.setFormat(text_format)
        layer.setLabelsEnabled(True)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))

    def record_layer_source(
        self, layer: QgsVectorLayer, operation: str, intended_use: str | None
    ) -> None:
        """Records where the layer came from, without any credentials."""
        layer.setCustomProperty(self.PROPERTY_MARKER, True)
        layer.setCustomProperty(self.PROPERTY_SCHEMA_VERSION, self.SCHEMA_VERSION)
        layer.setCustomProperty(self.PROPERTY_SOURCE_OPERATION, operation)
        layer.setCustomProperty(self.PROPERTY_INTENDED_USE, intended_use)
        layer.setCustomProperty(
            self.PROPERTY_RETRIEVED_AT,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def is_places_layer(self, layer: Any) -> bool:
        """Returns whether the layer can be enriched with place details."""
        if not isinstance(layer, QgsVectorLayer) or sip.isdeleted(layer):
            return False
        marker = layer.customProperty(self.PROPERTY_MARKER)
        if marker not in (True, 1, "true", "1"):
            return False
        try:
            schema_version = int(layer.customProperty(self.PROPERTY_SCHEMA_VERSION))
        except (TypeError, ValueError):
            return False
        if schema_version != self.SCHEMA_VERSION:
            return False
        fields = layer.fields()
        for name, field_type in self.RESULT_FIELDS:
            index = fields.indexOf(name)
            if index < 0 or fields.field(index).type() != field_type:
                return False
        return True

    def fetch_selected_feature_details(
        self,
        layer: Any,
        political_view: str | None = None,
        language: str | None = None,
        intended_use: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
        targets: list[tuple[int, str]] | None = None,
        credentials: tuple[str, str] | None = None,
    ) -> dict[int, dict[str, str]]:
        """Fetches GetPlace values without changing the layer."""
        if targets is None:
            targets = self.enrichment_targets(layer)
        self._validate_enrichment_state(layer, targets)
        if credentials is None:
            credentials = self.configuration_handler.get_credentials()

        values_by_feature: dict[int, dict[str, str]] = {}
        details_by_place_id: dict[str, dict[str, Any]] = {}
        for feature_id, place_id in targets:
            self._raise_if_cancelled(should_cancel)
            if not place_id:
                continue

            detail = details_by_place_id.get(place_id)
            if detail is None:
                self._validate_enrichment_state(layer, targets)
                try:
                    detail = self.get_place(
                        place_id,
                        list(self.ENRICH_FEATURES),
                        political_view,
                        language,
                        intended_use,
                        credentials=credentials,
                    )
                except RuntimeError as error:
                    if should_cancel is not None and should_cancel():
                        raise PlacesOperationCancelledError(
                            "The GetPlace request was cancelled."
                        ) from error
                    raise
                self._raise_if_cancelled(should_cancel)
                self._validate_enrichment_state(layer, targets)
                details_by_place_id[place_id] = detail

            values_by_feature[feature_id] = {
                self.FIELD_PHONE: first_contact(detail, "Phones"),
                self.FIELD_WEBSITE: first_contact(detail, "Websites"),
                self.FIELD_OPENING_HOURS: opening_hours(detail),
                self.FIELD_TIMEZONE: time_zone_name(detail),
            }

        self._raise_if_cancelled(should_cancel)
        self._validate_enrichment_state(layer, targets)
        return values_by_feature

    @staticmethod
    def _raise_if_cancelled(
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        """Stops the current operation when the dialog requested cancellation."""
        if should_cancel is not None and should_cancel():
            raise PlacesOperationCancelledError("The Places operation was cancelled.")

    def apply_feature_details(
        self, layer: QgsVectorLayer, values_by_feature: dict[int, dict[str, str]]
    ) -> int:
        """Applies fetched values as one undoable layer edit command."""
        if sip.isdeleted(layer):
            raise PlacesOperationCancelledError("The Places layer was closed.")
        if not self.is_places_layer(layer):
            raise ValueError("The active layer is not a Places result layer.")
        self._validate_detail_field_types(layer)
        if not values_by_feature:
            return 0
        for feature_id in values_by_feature:
            if not layer.getFeature(feature_id).isValid():
                raise PlacesOperationCancelledError(
                    "A selected Places feature was removed before details were applied."
                )

        started_editing = False
        if not layer.isEditable():
            if not layer.startEditing():
                raise RuntimeError("The Places layer could not enter edit mode.")
            started_editing = True

        layer.beginEditCommand("Apply Amazon Location place details")
        try:
            self._add_detail_fields(layer)
            self._set_detail_values(layer, values_by_feature)
        except Exception:
            layer.destroyEditCommand()
            if started_editing:
                layer.rollBack()
            raise

        layer.endEditCommand()
        layer.triggerRepaint()
        return len(values_by_feature)

    def _add_detail_fields(self, layer: QgsVectorLayer) -> None:
        """Adds any detail fields that are missing from an editable layer."""
        self._validate_detail_field_types(layer)
        for name in self.DETAIL_FIELDS:
            missing = layer.fields().indexOf(name) < 0
            if missing and not layer.addAttribute(QgsField(name, QVariant.String)):
                raise RuntimeError(f"Could not add the {name} field.")
        layer.updateFields()

    @staticmethod
    def _set_detail_values(
        layer: QgsVectorLayer, values_by_feature: dict[int, dict[str, str]]
    ) -> None:
        """Writes fetched detail values to the edit buffer."""
        for feature_id, values in values_by_feature.items():
            for name, value in values.items():
                field_index = layer.fields().indexOf(name)
                changed = field_index >= 0 and layer.changeAttributeValue(
                    feature_id, field_index, value
                )
                if not changed:
                    raise RuntimeError(
                        f"Could not update {name} for feature {feature_id}."
                    )

    def enrich_selected_features(
        self,
        layer: Any,
        political_view: str | None = None,
        language: str | None = None,
        intended_use: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> int:
        """Fetches Storage GetPlace values and applies them without partial updates."""
        if intended_use != "Storage":
            raise ValueError(
                "GetPlace details can only be applied after a Storage request."
            )
        targets = self.enrichment_targets(layer)
        values = self.fetch_selected_feature_details(
            layer,
            political_view,
            language,
            intended_use,
            should_cancel,
            targets,
        )
        self._raise_if_cancelled(should_cancel)
        return self.apply_feature_details(layer, values)

    def enrichment_targets(self, layer: Any) -> list[tuple[int, str]]:
        """
        Validates the layer and selection for Update Selected Details.

        Returns ``(feature id, PlaceId)`` pairs for the selected features, and
        raises ``ValueError`` when the layer or selection cannot be enriched.
        """
        if not self.is_places_layer(layer):
            raise ValueError(
                "The active layer is not a Places result layer. "
                "Run a Places search first."
            )
        if layer.providerType() != "memory":
            raise ValueError(
                "Update Selected Details only supports the temporary layers created "
                "by this plugin."
            )
        self._validate_detail_field_types(layer)
        selected = layer.selectedFeatures()
        if not selected:
            raise ValueError("Select one or more features to enrich.")
        targets = [
            (feature.id(), str(feature[self.FIELD_PLACE_ID] or ""))
            for feature in selected
        ]
        if any(not place_id for _, place_id in targets):
            raise ValueError("One or more selected features have no PlaceId value.")
        unique_place_ids = {str(place_id) for _, place_id in targets}
        if len(unique_place_ids) > self.MAX_ENRICH_FEATURES:
            raise ValueError(
                f"{len(unique_place_ids)} unique places are selected. "
                "Update Selected Details "
                "sends one billable Storage request per unique PlaceId; "
                "select at most "
                f"{self.MAX_ENRICH_FEATURES} unique places."
            )
        return targets

    def _validate_enrichment_targets(
        self, layer: Any, targets: list[tuple[int, str]]
    ) -> None:
        """Ensures a frozen GetPlace target list still matches the layer."""
        if not self.is_places_layer(layer):
            raise PlacesOperationCancelledError("The Places layer was closed.")
        for feature_id, place_id in targets:
            feature = layer.getFeature(feature_id)
            if not feature.isValid():
                raise PlacesOperationCancelledError(
                    "A selected Places feature was removed during the detail update."
                )
            current_place_id = str(feature[self.FIELD_PLACE_ID] or "")
            if current_place_id != place_id:
                raise PlacesOperationCancelledError(
                    "A selected PlaceId changed during the detail update."
                )

    def _validate_enrichment_state(
        self,
        layer: Any,
        targets: list[tuple[int, str]],
    ) -> None:
        """Checks that frozen GetPlace targets are still valid to request."""
        self._validate_enrichment_targets(layer, targets)
        self._validate_detail_field_types(layer)

    def _validate_detail_field_types(self, layer: QgsVectorLayer) -> None:
        """Rejects detail fields whose existing type cannot store text values."""
        fields = layer.fields()
        for name in self.DETAIL_FIELDS:
            index = fields.indexOf(name)
            if index >= 0 and fields.field(index).type() != QVariant.String:
                raise ValueError(
                    f"The existing {name} field must be a text field before "
                    "updating details."
                )
