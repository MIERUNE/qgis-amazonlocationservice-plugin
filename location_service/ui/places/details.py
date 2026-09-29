from __future__ import annotations

from qgis.core import QgsFeature, QgsField, QgsVectorLayer
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QVariant

from ...functions.places import PlacesFunctions, PlacesOperationCancelledError
from ...processing_provider.inputs import field_text
from ...processing_provider.runner import AlgorithmRun, temporary_output
from ..maps.constants import POLITICAL_VIEWS

# Layer signals after which the frozen GetPlace targets are checked again.
_CHANGE_SIGNALS = (
    "attributeValueChanged",
    "attributeAdded",
    "attributeDeleted",
    "featureDeleted",
    "updatedFields",
)


def political_view_index(political_view: str | None) -> int:
    """Returns the Processing enum index of a political view API value."""
    for index, (_label, value) in enumerate(POLITICAL_VIEWS):
        if value == (political_view or ""):
            return index
    raise ValueError(f"Unsupported political view: {political_view!r}")


class _LayerChangeWatcher:
    """
    Cancels a run as soon as a layer change invalidates the targets.

    The old per-request checks become signal handlers, so the request in
    flight is aborted and no further request is sent.
    """

    def __init__(
        self,
        places: PlacesFunctions,
        layer: QgsVectorLayer,
        targets: list[tuple[int, str]],
        run: AlgorithmRun,
    ) -> None:
        self.error: Exception | None = None
        self._places = places
        self._layer = layer
        self._targets = targets
        self._run = run
        self._signals = [getattr(layer, name) for name in _CHANGE_SIGNALS]
        for signal in self._signals:
            signal.connect(self._check)
        layer.willBeDeleted.connect(self._closed)

    def _check(self, *_args) -> None:
        """Cancels the run when the targets are no longer valid."""
        if self.error is not None:
            return
        try:
            self._places._validate_enrichment_state(self._layer, self._targets)
        except Exception as error:
            self.error = error
            self._run.cancel()

    def _closed(self) -> None:
        """Cancels the run when the layer is about to be deleted."""
        if self.error is None:
            self.error = PlacesOperationCancelledError("The Places layer was closed.")
        self._run.cancel()

    def disconnect(self) -> None:
        """Stops watching the layer."""
        if sip.isdeleted(self._layer):
            return
        for signal in self._signals:
            signal.disconnect(self._check)
        self._layer.willBeDeleted.disconnect(self._closed)


def _target_layer(layer: QgsVectorLayer, targets: list[tuple[int, str]]):
    """Returns the frozen targets as a point layer the algorithm can read."""
    target_layer = QgsVectorLayer("Point", "GetPlace targets", "memory")
    target_layer.setCrs(layer.crs())
    provider = target_layer.dataProvider()
    provider.addAttributes([QgsField(PlacesFunctions.FIELD_PLACE_ID, QVariant.String)])
    target_layer.updateFields()
    features = []
    for feature_id, place_id in targets:
        feature = QgsFeature(target_layer.fields())
        feature.setGeometry(layer.getFeature(feature_id).geometry())
        feature.setAttribute(PlacesFunctions.FIELD_PLACE_ID, place_id)
        features.append(feature)
    if features:
        added, _stored = provider.addFeatures(features)
        if not added:
            raise RuntimeError("Could not prepare the GetPlace targets.")
    return target_layer


def fetch_detail_values(
    places: PlacesFunctions,
    layer: QgsVectorLayer,
    targets: list[tuple[int, str]],
    run: AlgorithmRun,
    *,
    language: str | None = None,
    political_view: str | None = None,
    intended_use: str | None = None,
) -> dict[int, dict[str, str]]:
    """
    Runs the GetPlace algorithm for frozen targets without changing the layer.

    The algorithm sends one Storage request per unique PlaceId. A change to
    the layer while the requests run cancels the run before the next
    request, and nothing is returned for a layer that no longer matches the
    targets.
    """
    if intended_use != "Storage":
        raise ValueError(
            "GetPlace details can only be applied after a Storage request."
        )
    places._validate_enrichment_state(layer, targets)
    watcher = _LayerChangeWatcher(places, layer, targets, run)
    try:
        results = run.run(
            {
                "INPUT": _target_layer(layer, targets),
                "PLACE_ID_FIELD": PlacesFunctions.FIELD_PLACE_ID,
                "LANGUAGE": language or "",
                "POLITICAL_VIEW": political_view_index(political_view),
                "OUTPUT": temporary_output(),
            }
        )
    except Exception as error:
        # Report what changed, such as a PlaceId or a detail field type.
        if watcher.error is not None:
            raise watcher.error from error
        if run.feedback.isCanceled():
            raise PlacesOperationCancelledError(
                "The GetPlace request was cancelled."
            ) from error
        raise
    finally:
        watcher.disconnect()
    if watcher.error is not None:
        raise watcher.error
    output = run.take_layer(results, "OUTPUT")
    if output is None:
        raise RuntimeError("The GetPlace algorithm returned no layer.")
    details = {
        field_text(feature, PlacesFunctions.FIELD_PLACE_ID): {
            name: field_text(feature, name) for name in PlacesFunctions.DETAIL_FIELDS
        }
        for feature in output.getFeatures()
    }
    places._validate_enrichment_state(layer, targets)
    values = {}
    for feature_id, place_id in targets:
        if place_id not in details:
            raise PlacesOperationCancelledError(
                "The selected Places features changed during the detail update."
            )
        values[feature_id] = details[place_id]
    return values
