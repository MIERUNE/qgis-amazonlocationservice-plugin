import os
import re
from typing import Optional

from qgis.core import QgsProject
from qgis.PyQt import sip, uic
from qgis.PyQt.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QLineEdit,
    QWidget,
)
from qgis.utils import iface

from ...functions.places import (
    PlacesFunctions,
    PlacesOperationCancelledError,
)
from ...functions.places_storage import drawable_result_items
from ...utils.click_handler import (
    MapClickCoordinateUpdater,
    parse_lonlat,
    release_map_tool,
)
from ...utils.configuration_handler import ConfigurationError
from ...utils.feedback import (
    INFO,
    SUCCESS,
    busy_operation,
    push_message,
    show_config_error,
    show_error,
    show_warning,
)
from ...utils.localization_preferences import LocalizationPreferences
from ..maps.constants import LANGUAGES, POLITICAL_VIEWS
from ..style_loader import load_style
from . import constants


class PlacesUi(QDialog):
    """Places search dialog."""

    UI_PATH = os.path.join(os.path.dirname(__file__), "places.ui")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Loads the dialog, connects its controls, and populates the options."""
        super().__init__(parent)
        uic.loadUi(self.UI_PATH, self)
        self._apply_style()
        self.canvas = iface.mapCanvas()
        self.places = PlacesFunctions()
        self.localization_preferences = LocalizationPreferences()
        self._map_click = None
        self._cancelled = False
        self._busy = False
        self._active_function = None
        self._limited_region: Optional[bool] = None
        self._language_was_forced = False
        self._max_results = constants.DEFAULT_MAX_RESULTS.copy()
        self._pagination_request = None
        self._pagination_layer = None
        self._next_token = None
        self._pagination_place_ids: set[str] = set()

        self._populate_places_options()
        self._restore_localization_preferences()
        self._apply_region_capabilities()
        self._on_function_changed(self.places_comboBox.currentText())

        self.button_click.clicked.connect(self._click)
        self.button_position_clear.clicked.connect(self._clear_position)
        self.button_search.clicked.connect(self._search)
        self.button_enrich.clicked.connect(self._enrich)
        self.button_load_more.clicked.connect(self._load_more)
        self.button_cancel.clicked.connect(self._cancel)
        self.places_comboBox.currentTextChanged.connect(self._on_function_changed)
        self.language_comboBox.activated.connect(self._on_language_selected)
        self.language_comboBox.lineEdit().textEdited.connect(self._on_language_selected)

    def _apply_style(self) -> None:
        """Applies QSS locally across the scroll tree for QGIS 3 dark themes."""
        scroll_contents = self.scrollArea.widget()
        # QGIS 3's application theme can override an inherited stylesheet at
        # the QScrollArea boundary. Giving each descendant a local stylesheet
        # keeps the plugin theme authoritative on Qt 5 as well as Qt 6.
        # Editors embedded in spin and combo boxes are skipped: styling them
        # directly would apply the QLineEdit padding inside their parent.
        children = [
            child
            for child in scroll_contents.findChildren(QWidget)
            if not isinstance(child.parentWidget(), (QAbstractSpinBox, QComboBox))
        ]
        load_style(
            self,
            self.scrollArea,
            self.scrollArea.viewport(),
            scroll_contents,
            *children,
        )

    def showEvent(self, event) -> None:
        """Reapplies the Places theme after QGIS finishes polishing the window."""
        super().showEvent(event)
        self._apply_style()
        if event.spontaneous():
            return
        self._restore_localization_preferences()
        self._apply_region_capabilities()

    def _populate_places_options(self) -> None:
        """Populates the function selector and the option combo boxes."""
        for function in constants.FUNCTIONS:
            self.places_comboBox.addItem(function)
        for label, value in constants.SEARCH_TRAVEL_MODES:
            self.st_travelmode_comboBox.addItem(label, value)
        for label, value in constants.ADDRESS_NAMES_MODES:
            self.gc_addressnames_comboBox.addItem(label, value)
        for label, value in constants.POSTAL_CODE_MODES:
            self.gc_postalcode_comboBox.addItem(label, value)
        for language in LANGUAGES:
            self.language_comboBox.addItem(language)
        for label, value in POLITICAL_VIEWS:
            self.political_view_comboBox.addItem(label, value)

    def _on_function_changed(self, function: str) -> None:
        """Updates the parameter page and defaults for the selected function."""
        if self._active_function:
            self._max_results[self._active_function] = self.maxresults_spinBox.value()
        self.params_stackedWidget.setCurrentIndex(
            constants.FUNCTION_PAGE.get(function, 0)
        )
        self.position_label.setText(constants.POSITION_LABEL.get(function, "Position"))
        self.maxresults_spinBox.setValue(self._max_results.get(function, 10))
        self.button_search.setText(
            constants.SEARCH_BUTTON_LABEL.get(function, "Search")
        )
        self._active_function = function

    def _restore_localization_preferences(self) -> None:
        """Restores values last used successfully by Maps or Places."""
        language, political_view = self.localization_preferences.load()
        self._language_was_forced = False
        self.language_comboBox.setEditText(language or constants.LANGUAGE_DEFAULT)
        political_index = self.political_view_comboBox.findData(political_view)
        self.political_view_comboBox.setCurrentIndex(max(political_index, 0))

    def _on_language_selected(self, *_args) -> None:
        """Marks the displayed language as an explicit user selection."""
        self._language_was_forced = False

    def _save_localization_preferences(
        self,
        language: Optional[str],
        political_view: Optional[str],
        save_political_view: bool = True,
        save_language: bool = True,
    ) -> None:
        """Shares the localization values sent by a successful request."""
        if not save_language or not save_political_view:
            saved_language, saved_political_view = self.localization_preferences.load()
            if not save_language:
                language = saved_language
            if not save_political_view:
                political_view = saved_political_view
        self.localization_preferences.save(language, political_view)
        if save_language:
            self._language_was_forced = False

    def _configured_region(self) -> str:
        """Returns the configured region without prompting for credentials."""
        handler = self.places.configuration_handler
        return str(handler.get_setting(handler.KEY_REGION) or "").strip()

    def refresh_region_capabilities(self) -> None:
        """Cancels active work and refreshes controls after settings change."""
        if self._busy:
            self._cancelled = True
            self.places.api_handler.abort()
        self._clear_pagination()
        self._apply_region_capabilities()

    def _apply_region_capabilities(self) -> None:
        """Disables controls that the configured Places provider cannot use."""
        limited = self._configured_region() in constants.LIMITED_REGIONS
        if self._limited_region and not limited:
            self._restore_localization_preferences()
        self._limited_region = limited

        geocode_index = self.places_comboBox.findText("Geocode")
        geocode_item = self.places_comboBox.model().item(geocode_index)
        if geocode_item is not None:
            geocode_item.setEnabled(not limited)
        if limited and self._current_function() == "Geocode":
            self.places_comboBox.setCurrentText("SearchText")

        self._apply_language_region_capabilities(limited)

        self.political_view_comboBox.setEnabled(not limited)
        if limited:
            self.political_view_comboBox.setCurrentIndex(0)

        max_radius = (
            constants.LIMITED_MAX_RADIUS if limited else constants.DEFAULT_MAX_RADIUS
        )
        self.rg_radius_spinBox.setMaximum(max_radius)
        self.nb_radius_spinBox.setMaximum(max_radius)

        self.button_enrich.setEnabled(not limited)
        if limited:
            self.button_enrich.setToolTip(
                "Update Selected Details cannot persist GetPlace results in this "
                "region."
            )
        else:
            self.button_enrich.setToolTip(constants.UPDATE_DETAILS_TOOLTIP)

    def _apply_language_region_capabilities(self, limited: bool) -> None:
        """Disables preset languages that the configured provider cannot use."""
        current_language = self.language_comboBox.currentText().strip()
        language_model = self.language_comboBox.model()
        for index in range(self.language_comboBox.count()):
            language = self.language_comboBox.itemText(index)
            item = language_model.item(index)
            if item is not None:
                item.setEnabled(
                    not limited
                    or language == constants.LANGUAGE_DEFAULT
                    or language in constants.LIMITED_LANGUAGES
                )
        language_is_unsupported = (
            current_language not in ("", constants.LANGUAGE_DEFAULT)
            and current_language not in constants.LIMITED_LANGUAGES
        )
        if limited and language_is_unsupported:
            current_language = constants.LANGUAGE_DEFAULT
            self._language_was_forced = True

        language_index = self.language_comboBox.findText(current_language)
        if language_index >= 0:
            self.language_comboBox.setCurrentIndex(language_index)
        else:
            self.language_comboBox.setEditText(current_language)

    def _current_function(self) -> str:
        """Returns the currently selected function name."""
        return self.places_comboBox.currentText()

    def _parse_position(self) -> Optional[list]:
        """Returns the picked ``[lon, lat]`` pair, or ``None`` when both are blank."""
        longitude = self.lon_lineEdit.text().strip()
        latitude = self.lat_lineEdit.text().strip()
        if not longitude and not latitude:
            return None
        position = parse_lonlat(longitude, latitude)
        if position is None:
            raise ValueError(
                "Enter valid longitude and latitude values, or clear both fields."
            )
        return position

    def _language(self) -> Optional[str]:
        """
        Returns the BCP 47 language code, or ``None`` for the API default.

        Raises ``ValueError`` when an edited value is not a BCP 47 code.
        """
        text = self.language_comboBox.currentText().strip()
        if not text or text == constants.LANGUAGE_DEFAULT:
            return None
        if len(text) > constants.LANGUAGE_MAX_LENGTH or not (
            constants.LANGUAGE_PATTERN.match(text)
        ):
            raise ValueError(constants.LANGUAGE_FORMAT_HINT)
        return text

    def _political_view(self) -> Optional[str]:
        """Returns the selected political view, or ``None`` for the default."""
        return self.political_view_comboBox.currentData() or None

    @staticmethod
    def _require_query_text(line_edit: QLineEdit) -> str:
        """
        Returns the stripped text of a required query field.

        Raises ``ValueError`` when the field is empty.
        """
        text = line_edit.text().strip()
        if not text:
            raise ValueError("QueryText must be filled in.")
        return text

    @staticmethod
    def _parse_countries(text: str) -> Optional[list]:
        """
        Parses comma-separated ISO alpha-2/alpha-3 country codes.

        Returns the upper-cased codes, ``None`` for an empty field, and raises
        ``ValueError`` for an entry that is not a 2- or 3-letter code.
        """
        countries = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if not re.fullmatch(r"[A-Za-z]{2,3}", part):
                raise ValueError(
                    f"Invalid country code: {part}. "
                    "Use ISO alpha-2/alpha-3 codes such as JP or JPN."
                )
            countries.append(part.upper())
        return countries or None

    def _search(self) -> None:
        """Runs the selected Places operation and adds the results to the map."""
        if self._busy:
            return
        function = self._current_function()
        try:
            position = self._parse_position()
        except ValueError as error:
            show_error(self, "Input Error", str(error))
            return
        if constants.POSITION_REQUIRED.get(function) and position is None:
            show_error(
                self,
                "Input Error",
                "This function requires a valid position. "
                "Click 'Get Location' and select a point on the map.",
            )
            return
        self._cancelled = False
        self._busy = True
        try:
            request = self._build_request(
                function, position, self.maxresults_spinBox.value()
            )
            if self._cancelled:
                return
            intended_use = "Storage"
            self._validate_request_options(request, intended_use)
            with busy_operation(self.button_search, "Searching…"):
                result = self._send_request(request, intended_use)
            if self._cancelled:
                return
            self._save_localization_preferences(
                request["language"],
                request["political_view"],
                save_political_view=request["political_view_enabled"],
                save_language=request.get("save_language", True),
            )
            if not self._has_drawable_results(result):
                push_message(INFO, f"{function} returned no drawable results.")
                return
            layer = self.places.add_point_layer(
                result,
                function,
                intended_use=intended_use,
            )
            iface.setActiveLayer(layer)
            self._set_pagination(request, layer, result)
        except Exception as e:
            self._report_search_error(e)
            return
        finally:
            self._busy = False
            # busy_operation restores the label captured on entry; reapply it in
            # case the user switched functions while the request was running.
            self.button_search.setText(
                constants.SEARCH_BUTTON_LABEL.get(self._current_function(), "Search")
            )
        push_message(SUCCESS, f"Added the “{function}” layer.")

    @staticmethod
    def _has_drawable_results(result: dict) -> bool:
        """Returns whether a response contains at least one drawable item."""
        return bool(drawable_result_items(result))

    def _build_request(
        self, function: str, position: Optional[list], max_results: int
    ) -> dict:
        """Captures one complete Places request from the current inputs."""
        language = self._language()
        request = {
            "function": function,
            "position": list(position) if position else None,
            "max_results": max_results,
            "language": language,
            "save_language": not self._language_was_forced or language is not None,
            "political_view": self._political_view(),
            "political_view_enabled": self.political_view_comboBox.isEnabled(),
        }
        if function == "SearchText":
            request.update(
                {
                    "text": self._require_query_text(self.text_lineEdit),
                    "countries": self._parse_countries(
                        self.st_countries_lineEdit.text()
                    ),
                    "travel_mode": self.st_travelmode_comboBox.currentData() or None,
                }
            )
        elif function == "Geocode":
            request.update(
                {
                    "text": self._require_query_text(self.gc_text_lineEdit),
                    "countries": self._parse_countries(
                        self.gc_countries_lineEdit.text()
                    ),
                    "address_names_mode": self.gc_addressnames_comboBox.currentData()
                    or None,
                    "postal_code_mode": self.gc_postalcode_comboBox.currentData()
                    or None,
                }
            )
        elif function == "ReverseGeocode":
            request["query_radius"] = self.rg_radius_spinBox.value() or None
        elif function == "SearchNearby":
            request["query_radius"] = self.nb_radius_spinBox.value()
        else:
            raise ValueError(f"Unknown function: {function}")
        credentials = self.places.configuration_handler.get_credentials()
        request["region"] = credentials[0]
        request["credentials"] = credentials
        request["additional_features"] = constants.automatic_additional_features(
            credentials[0], function
        )
        return request

    def _send_request(
        self,
        request: dict,
        intended_use: Optional[str],
        next_token: Optional[str] = None,
    ) -> dict:
        """Validates region capabilities and sends one captured request."""
        function = request["function"]
        position = request["position"]
        credentials = request["credentials"]
        if credentials != self.places.configuration_handler.get_credentials():
            raise ValueError(
                "The configured region or API key changed after this Places request "
                "started. "
                "Run the search again."
            )
        self._validate_request_options(request, intended_use)

        if function == "SearchText":
            lon, lat = position
            return self.places.search_text(
                request["text"],
                request["max_results"],
                lon,
                lat,
                include_countries=request["countries"],
                travel_mode=request["travel_mode"],
                additional_features=request["additional_features"],
                political_view=request["political_view"],
                language=request["language"],
                intended_use=intended_use,
                next_token=next_token,
                credentials=credentials,
            )
        if function == "Geocode":
            lon, lat = position if position else (None, None)
            return self.places.geocode(
                request["text"],
                request["max_results"],
                lon,
                lat,
                include_countries=request["countries"],
                address_names_mode=request["address_names_mode"],
                postal_code_mode=request["postal_code_mode"],
                political_view=request["political_view"],
                language=request["language"],
                intended_use=intended_use,
                additional_features=request["additional_features"],
                credentials=credentials,
            )
        if function == "ReverseGeocode":
            lon, lat = position
            return self.places.reverse_geocode(
                lon,
                lat,
                request["max_results"],
                request["query_radius"],
                political_view=request["political_view"],
                language=request["language"],
                intended_use=intended_use,
                additional_features=request["additional_features"],
                credentials=credentials,
            )
        if function == "SearchNearby":
            lon, lat = position
            return self.places.search_nearby(
                lon,
                lat,
                request["query_radius"],
                request["max_results"],
                additional_features=request["additional_features"],
                political_view=request["political_view"],
                language=request["language"],
                intended_use=intended_use,
                next_token=next_token,
                credentials=credentials,
            )
        raise ValueError(f"Unknown function: {function}")

    @staticmethod
    def _validate_request_options(request: dict, intended_use: Optional[str]) -> None:
        """Validates provider-specific options before a request is sent."""
        constants.validate_region_options(
            request["region"],
            request["function"],
            language=request["language"],
            political_view=request["political_view"],
            additional_features=request.get("additional_features"),
            intended_use=intended_use,
            query_radius=request.get("query_radius"),
        )

    def _set_pagination(self, request: dict, layer, result: dict) -> None:
        """Stores the state required by the explicit Load More action."""
        token = result.get("NextToken")
        if request["function"] not in {"SearchText", "SearchNearby"} or not token:
            self._clear_pagination()
            return
        self._pagination_request = request
        self._pagination_layer = layer
        self._next_token = token
        self._pagination_place_ids = self._layer_place_ids(layer)
        self.button_load_more.setToolTip(
            f"Load the next page into “{layer.name()}”. This sends a Storage request."
        )
        self.button_load_more.setEnabled(True)

    def _clear_pagination(self) -> None:
        """Clears a previous response token and disables Load More."""
        self._pagination_request = None
        self._pagination_layer = None
        self._next_token = None
        self._pagination_place_ids = set()
        self.button_load_more.setToolTip(
            "Loads the next page into the most recently added Places layer."
        )
        self.button_load_more.setEnabled(False)

    def _pagination_layer_is_available(self, layer) -> bool:
        """Returns whether the saved pagination layer is still in this project."""
        if layer is None or sip.isdeleted(layer):
            return False
        if not self.places.is_places_layer(layer):
            return False
        return QgsProject.instance().mapLayer(layer.id()) is layer

    @staticmethod
    def _layer_place_ids(layer) -> set[str]:
        """Returns the non-empty PlaceIds currently stored in a result layer."""
        return {
            str(feature[PlacesFunctions.FIELD_PLACE_ID])
            for feature in layer.getFeatures()
            if feature[PlacesFunctions.FIELD_PLACE_ID]
        }

    def _loaded_page_target_is_valid(self, layer, place_ids_before: set[str]) -> bool:
        """Checks that the pagination layer stayed safe while the request ran."""
        if not self._pagination_layer_is_available(layer):
            self._clear_pagination()
            show_warning(
                self,
                "Load More",
                "The Places layer is no longer available because it was removed "
                "while the page was loading. The page was not added. Run the "
                "search again.",
            )
            return False
        if layer.isEditable():
            self._clear_pagination()
            show_warning(
                self,
                "Load More",
                "The Places layer entered edit mode while the page was loading. "
                "The page was not added. Run the search again before loading more "
                "results.",
            )
            return False
        if self._layer_place_ids(layer) != place_ids_before:
            self._clear_pagination()
            show_warning(
                self,
                "Load More",
                "The Places layer changed while the page was loading. "
                "Run the search again before loading more results.",
            )
            return False
        return True

    def _pagination_credentials_are_current(self, request: dict) -> bool:
        """Checks a saved page request and reports changed credentials."""
        try:
            captured = request.get("credentials")
            matches = False
            if captured is not None:
                current = self.places.configuration_handler.get_credentials()
                matches = captured == current
        except ConfigurationError as error:
            self._clear_pagination()
            show_config_error(self, error)
            return False

        if matches:
            return True
        self._clear_pagination()
        show_warning(
            self,
            "Load More",
            "The configured region or API key changed. Run the Places search again.",
        )
        return False

    def _load_more(self) -> None:
        """Appends the next SearchText or SearchNearby page to its layer."""
        if self._busy or not self._pagination_request or not self._next_token:
            return
        layer = self._pagination_layer
        request = self._pagination_request
        if not self._pagination_layer_is_available(layer):
            self._clear_pagination()
            show_warning(
                self,
                "Load More",
                "The Places layer for this result page is no longer available.",
            )
            return
        if layer.isEditable():
            show_warning(
                self,
                "Load More",
                "Finish or roll back the Places layer edits before loading "
                "another page. No request was sent.",
            )
            return
        place_ids_before = self._layer_place_ids(layer)
        if place_ids_before != self._pagination_place_ids:
            self._clear_pagination()
            show_warning(
                self,
                "Load More",
                "The Places layer changed after the search. Run the search again "
                "before loading more results.",
            )
            return
        self._cancelled = False
        self._busy = True
        try:
            if not self._pagination_credentials_are_current(request) or self._cancelled:
                return
            with busy_operation(self.button_load_more, "Loading…"):
                result = self._send_request(request, "Storage", self._next_token)
            if self._cancelled:
                self._clear_pagination()
                return
            if (
                self._pagination_request is not request
                or self._pagination_layer is not layer
            ):
                return
            if not self._loaded_page_target_is_valid(layer, place_ids_before):
                return
            added_count = self._append_result_page(layer, request, result)
            self._show_page_added_message(added_count)
        except Exception as error:
            self._report_search_error(error)
        finally:
            self._busy = False
            self.button_load_more.setEnabled(
                bool(self._pagination_request and self._next_token)
            )

    def _append_result_page(self, layer, request: dict, result: dict) -> Optional[int]:
        """Validates and appends one page, then advances its token."""
        try:
            new_items, new_place_ids = self._new_page_items(result)
            added_count = self.places.add_features(
                layer,
                {"ResultItems": new_items},
                request["function"],
            )
            self._pagination_place_ids.update(new_place_ids)
            layer.updateExtents()
            layer.triggerRepaint()

            next_token = result.get("NextToken")
            self._next_token = None if next_token == self._next_token else next_token
        except Exception:
            self._clear_pagination()
            raise
        return added_count

    @staticmethod
    def _show_page_added_message(added_count: Optional[int]) -> None:
        """Reports a successfully appended page."""
        if added_count is not None:
            push_message(SUCCESS, f"Added {added_count} more place result(s).")

    def _new_page_items(self, result: dict) -> tuple[list, set[str]]:
        """Returns drawable page items not already present in the layer."""
        items = []
        place_ids = set()
        for item in drawable_result_items(result):
            place_id = str(item.get("PlaceId") or "")
            if place_id and (
                place_id in self._pagination_place_ids or place_id in place_ids
            ):
                continue
            items.append(item)
            if place_id:
                place_ids.add(place_id)
        return items, place_ids

    def _report_search_error(self, error: Exception) -> None:
        """Handles a search failure and clears pagination after cancellation."""
        if self._cancelled:
            self._clear_pagination()
            return
        # ConfigurationError subclasses ValueError, so it must be checked first.
        if isinstance(error, ConfigurationError):
            show_config_error(self, error)
        elif isinstance(error, ValueError):
            show_error(self, "Input Error", str(error))
        else:
            show_error(self, "Search Error", f"Failed to search places: {error}")

    def _enrich(self) -> None:
        """Adds GetPlace details to the selected features of the active layer."""
        if self._busy:
            return
        layer = iface.activeLayer()
        self._cancelled = False
        self._busy = True
        try:
            language = self._language()
            political_view = self._political_view()
            political_view_enabled = self.political_view_comboBox.isEnabled()
            intended_use = "Storage"
            targets = self.places.enrichment_targets(layer)
            credentials = self.places.configuration_handler.get_credentials()
            with busy_operation(self.button_enrich, "Updating…"):
                values = self._fetch_detail_values(
                    layer,
                    language,
                    political_view,
                    intended_use,
                    targets,
                    credentials,
                )
            if self._cancelled:
                return
            self._save_localization_preferences(
                language,
                political_view,
                political_view_enabled,
                save_language=not self._language_was_forced or language is not None,
            )

            count = self.places.apply_feature_details(layer, values)
            push_message(
                SUCCESS,
                f"Enriched {count} feature(s). The layer remains in edit mode; "
                "use QGIS Undo to revert the change.",
                duration=8,
            )
        except Exception as e:
            self._report_enrich_error(e)
        finally:
            self._busy = False
            self._apply_region_capabilities()

    def _fetch_detail_values(
        self,
        layer,
        language: Optional[str],
        political_view: Optional[str],
        intended_use: Optional[str],
        targets: list[tuple[int, str]],
        credentials: tuple[str, str],
    ) -> dict:
        """Validates and fetches GetPlace values without changing the layer."""
        constants.validate_region_options(
            credentials[0],
            "GetPlace",
            language=language,
            political_view=political_view,
            additional_features=list(PlacesFunctions.ENRICH_FEATURES),
            intended_use=intended_use,
        )
        return self.places.fetch_selected_feature_details(
            layer,
            political_view=political_view,
            language=language,
            intended_use=intended_use,
            should_cancel=lambda: self._cancelled,
            targets=targets,
            credentials=credentials,
        )

    def _report_enrich_error(self, error: Exception) -> None:
        """Shows a detail update failure unless the dialog was closed mid-request."""
        if self._cancelled:
            return
        # ConfigurationError subclasses ValueError, so it must be checked first.
        if isinstance(error, ConfigurationError):
            show_config_error(self, error)
        elif isinstance(error, PlacesOperationCancelledError):
            show_warning(self, "Update Details", str(error))
        elif isinstance(error, ValueError):
            # Input problems such as a missing selection or PlaceId field.
            show_warning(self, "Update Details", str(error))
        else:
            show_error(
                self, "Update Details Error", f"Failed to update place details: {error}"
            )

    def _cancel(self) -> None:
        """Closes the dialog and cancels active work."""
        self.close()

    def _clear_position(self) -> None:
        """Clears the picked position so the next search runs without it."""
        self.lon_lineEdit.clear()
        self.lat_lineEdit.clear()

    def _click(self) -> None:
        """Activates the map tool for choosing search coordinates."""
        if self._map_click is None:
            self._map_click = MapClickCoordinateUpdater(
                self.canvas, self.lon_lineEdit, self.lat_lineEdit
            )
        self._map_click.arm()

    def hideEvent(self, event) -> None:
        """Cancels active work when hidden, except during minimization."""
        super().hideEvent(event)
        if event.spontaneous():
            return
        self._cancelled = True
        self.places.api_handler.abort()
        release_map_tool(self._map_click)
