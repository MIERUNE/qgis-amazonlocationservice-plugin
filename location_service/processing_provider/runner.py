from __future__ import annotations

from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingOutputLayerDefinition,
    QgsProject,
    QgsReferencedPointXY,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant

from .base import LocationServiceAlgorithm

WGS84_CRS = "EPSG:4326"


def wgs84_point(position) -> QgsReferencedPointXY:
    """Returns a ``(lon, lat)`` position as a point parameter value."""
    return QgsReferencedPointXY(
        QgsPointXY(float(position[0]), float(position[1])),
        QgsCoordinateReferenceSystem(WGS84_CRS),
    )


def temporary_output() -> QgsProcessingOutputLayerDefinition:
    """Returns a memory output that the runner can take and style."""
    return QgsProcessingOutputLayerDefinition("memory:", QgsProject.instance())


def rows_layer(
    rows: list[dict[str, Any]], order_field: QgsField | None = None
) -> QgsVectorLayer:
    """
    Returns input snapshot rows as a WGS 84 point layer.

    The layer keeps the row order as its feature order and carries the
    ``id``, ``order``, ``timestamp``, ``heading`` and ``speed`` values, so an
    algorithm reading it sends exactly the snapshot a dialog confirmed.
    ``order_field`` is the source order field, whose type ``order`` keeps.
    """
    layer = QgsVectorLayer(f"Point?crs={WGS84_CRS}", "input", "memory")
    order = QgsField(order_field) if order_field is not None else None
    if order is not None:
        order.setName("order")
    else:
        order = QgsField("order", QVariant.String)
    fields = [
        QgsField("id", QVariant.String),
        order,
        QgsField("timestamp", QVariant.String),
        QgsField("heading", QVariant.Double),
        QgsField("speed", QVariant.Double),
    ]
    if not layer.dataProvider().addAttributes(fields):
        raise RuntimeError("Could not prepare the input points.")
    layer.updateFields()
    features = []
    for row in rows:
        feature = QgsFeature(layer.fields())
        lon, lat = row["position"]
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
        feature.setAttributes(
            [
                row.get("id"),
                row.get("order"),
                row.get("timestamp"),
                row.get("heading"),
                row.get("speed"),
            ]
        )
        features.append(feature)
    if features:
        added, _stored = layer.dataProvider().addFeatures(features)
        if not added:
            raise RuntimeError("Could not prepare the input points.")
    return layer


class AlgorithmRun:
    """
    Runs one provider algorithm synchronously for a dialog.

    The dialog owns this instance, so the original exception of a failed run
    and the pricing bucket stay available after QGIS has turned the failure
    into a ``QgsProcessingException``. ``cancel`` aborts the active request.
    """

    def __init__(self, algorithm_class: type[LocationServiceAlgorithm]) -> None:
        """Creates a fresh, initialized algorithm instance."""
        self.algorithm = algorithm_class().create()
        self.context = QgsProcessingContext()
        self.context.setProject(QgsProject.instance())
        # Dialogs report their own messages, so do not copy them to the log.
        self.feedback = QgsProcessingFeedback(False)

    @property
    def pricing_bucket(self) -> str | None:
        """Returns the pricing bucket of the last response, if any."""
        return self.algorithm.pricing_bucket

    def cancel(self) -> None:
        """Cancels the run and aborts its active request."""
        self.feedback.cancel()

    def run(self, parameters: dict[str, Any]) -> dict[str, Any]:
        """
        Runs the algorithm and returns its results.

        A failure re-raises the original exception, such as a
        ``ConfigurationError``, ``ValueError`` or ``ApiError``.
        """
        ok, message = self.algorithm.checkParameterValues(parameters, self.context)
        if not ok:
            raise ValueError(message)
        if not self.algorithm.prepare(parameters, self.context, self.feedback):
            raise self._error("The algorithm could not be prepared.")
        try:
            results = self.algorithm.runPrepared(
                parameters, self.context, self.feedback
            )
        except QgsProcessingException as error:
            raise self._error(str(error)) from None
        self.algorithm.postProcess(self.context, self.feedback)
        return results

    def _error(self, message: str) -> Exception:
        """Returns the original error of the run, or a generic one."""
        error = self.algorithm.last_error
        if error is None or isinstance(error, QgsProcessingException):
            return RuntimeError(message)
        return error

    def take_layer(self, results: dict[str, Any], name: str) -> QgsVectorLayer | None:
        """
        Takes an output layer, named and styled like a loaded output.

        The layer is not added to the project; the caller decides that.
        """
        dest_id = results.get(name)
        if not dest_id:
            return None
        layer = self.context.takeResultLayer(dest_id)
        if not isinstance(layer, QgsVectorLayer):
            return None
        if self.context.willLoadLayerOnCompletion(dest_id):
            details = self.context.layerToLoadOnCompletionDetails(dest_id)
            if details.name:
                layer.setName(details.name)
            processor = details.postProcessor()
            if processor is not None:
                processor.postProcessLayer(layer, self.context, self.feedback)
        return layer
