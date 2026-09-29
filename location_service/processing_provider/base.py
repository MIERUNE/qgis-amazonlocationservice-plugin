from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingOutputString,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QIcon

from ..utils.configuration_handler import (
    AuthDatabaseLockedError,
    ConfigurationError,
    ConfigurationHandler,
)
from ..utils.external_api_handler import ApiError, ExternalApiHandler
from ..utils.redaction import redact_secrets

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
CONFIG_MISSING_HINT = "Open the Config menu and set your AWS region and API key first."
_PLUGIN_DIRECTORY = os.path.dirname(os.path.dirname(__file__))

# QGIS does not take ownership of layer post-processors, so keep the most
# recent ones alive until QGIS has loaded the output layers.
_POST_PROCESSORS: deque = deque(maxlen=100)


class OperationCancelledError(RuntimeError):
    """Raised when the user cancels a running algorithm."""


class LayerStyle:
    """
    A thread-safe snapshot of a built layer's presentation and provenance.

    Result layers are built and validated as memory layers inside the worker
    thread; this snapshot carries their renderer, labels, field aliases,
    custom properties and metadata rights to the output layer QGIS loads
    in the main thread.
    """

    def __init__(self, layer: QgsVectorLayer) -> None:
        """Captures the presentation of ``layer``."""
        renderer = layer.renderer()
        self.renderer = renderer.clone() if renderer is not None else None
        labeling = layer.labeling()
        self.labeling = labeling.clone() if labeling is not None else None
        self.labels_enabled = layer.labelsEnabled()
        self.aliases = {
            field.name(): field.alias() for field in layer.fields() if field.alias()
        }
        self.properties = {
            key: layer.customProperty(key) for key in layer.customPropertyKeys()
        }
        self.rights = list(layer.metadata().rights())

    def apply(self, layer: QgsVectorLayer) -> None:
        """Applies the captured presentation to ``layer``."""
        if self.renderer is not None:
            layer.setRenderer(self.renderer.clone())
        if self.labeling is not None:
            layer.setLabeling(self.labeling.clone())
            layer.setLabelsEnabled(self.labels_enabled)
        fields = layer.fields()
        for name, alias in self.aliases.items():
            index = fields.indexOf(name)
            if index >= 0:
                layer.setFieldAlias(index, alias)
        for key, value in self.properties.items():
            layer.setCustomProperty(key, value)
        if self.rights:
            metadata = layer.metadata()
            metadata.setRights(self.rights)
            layer.setMetadata(metadata)
        layer.triggerRepaint()


class _LayerPostProcessor(QgsProcessingLayerPostProcessorInterface):
    """Applies a ``LayerStyle`` to an output layer after QGIS loads it."""

    def __init__(self, style: LayerStyle) -> None:
        super().__init__()
        self._style = style

    def postProcessLayer(self, layer, context, feedback) -> None:
        """Styles the loaded output layer in the main thread."""
        if isinstance(layer, QgsVectorLayer):
            self._style.apply(layer)


class LocationServiceAlgorithm(QgsProcessingAlgorithm):
    """
    Shared behavior of the Amazon Location Service algorithms.

    Credentials are captured in ``prepareAlgorithm``, which QGIS runs in the
    main thread before a background task starts, so reading an encrypted
    API key can prompt for the master password safely. Network requests
    then run in the worker thread and are aborted when the user cancels.
    The Places and Routes dialogs run the same algorithms synchronously
    through ``runner.AlgorithmRun``.
    """

    PRICING_BUCKET = "PRICING_BUCKET"

    OPERATION = ""
    GROUP = ""
    GROUP_ID = ""
    ICON = ""
    HELP = ""
    PERMISSION_HINT = ""

    def __init__(self) -> None:
        super().__init__()
        self._credentials: tuple[str, str] | None = None
        self._outputs: dict[str, tuple[str, LayerStyle]] = {}
        # The original exception of a failed run and the pricing bucket of
        # the last response, kept for callers that run this instance directly.
        self.last_error: Exception | None = None
        self.pricing_bucket: str | None = None

    def createInstance(self) -> LocationServiceAlgorithm:
        """Returns a fresh copy of this algorithm."""
        return type(self)()

    def name(self) -> str:
        """Returns the algorithm id, the lowercase AWS operation name."""
        return self.OPERATION.lower()

    def displayName(self) -> str:
        """Returns the AWS operation name shown in the toolbox."""
        return self.OPERATION

    def group(self) -> str:
        """Returns the toolbox group name."""
        return self.GROUP

    def groupId(self) -> str:
        """Returns the toolbox group id."""
        return self.GROUP_ID

    def icon(self) -> QIcon:
        """Returns the group icon."""
        return QIcon(os.path.join(_PLUGIN_DIRECTORY, self.ICON))

    def shortHelpString(self) -> str:
        """Returns the help shown next to the parameters."""
        return self.HELP

    def tags(self) -> list[str]:
        """Returns search tags for the toolbox."""
        return ["aws", "amazon", "location", self.GROUP.lower(), self.OPERATION]

    @property
    def region(self) -> str:
        """Returns the region captured for this run."""
        return self.credentials[0]

    @property
    def credentials(self) -> tuple[str, str]:
        """Returns the credentials captured for this run."""
        if self._credentials is None:
            raise QgsProcessingException("The algorithm was not prepared.")
        return self._credentials

    def prepareAlgorithm(
        self,
        parameters: dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> bool:
        """Captures the configured region and API key before the run starts."""
        try:
            self._credentials = ConfigurationHandler().get_credentials()
        except ConfigurationError as error:
            self.last_error = error
            raise QgsProcessingException(self.error_message(error)) from error
        return True

    def add_pricing_output(self) -> None:
        """Adds the pricing bucket reported by the last response."""
        self.addOutput(QgsProcessingOutputString(self.PRICING_BUCKET, "Pricing bucket"))

    def processAlgorithm(
        self,
        parameters: dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> dict[str, Any]:
        """Runs the operation and reports failures as Processing errors."""
        try:
            results = self.execute(parameters, context, feedback)
        except QgsProcessingException as error:
            self.last_error = error
            raise
        except Exception as error:
            self.last_error = error
            raise QgsProcessingException(self.error_message(error)) from error
        results[self.PRICING_BUCKET] = self.pricing_bucket or ""
        return results

    def error_message(self, error: Exception) -> str:
        """Returns the redacted Processing message for a failure."""
        if isinstance(error, AuthDatabaseLockedError):
            message = str(error)
        elif isinstance(error, ConfigurationError):
            message = f"{error} {CONFIG_MISSING_HINT}"
        elif isinstance(error, (OperationCancelledError, ValueError)):
            message = str(error)
        else:
            message = f"{self.OPERATION} failed: {error}"
            if (
                isinstance(error, ApiError)
                and error.status_code == 403
                and self.PERMISSION_HINT
            ):
                message = f"{message} {self.PERMISSION_HINT}"
        return redact_secrets(message)

    def execute(
        self,
        parameters: dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback,
    ) -> dict[str, Any]:
        """Runs the operation; implemented by each algorithm."""
        raise NotImplementedError

    def postProcessAlgorithm(
        self, context: QgsProcessingContext, feedback: QgsProcessingFeedback
    ) -> dict[str, Any]:
        """Names and styles the output layers QGIS loads into the project."""
        for dest_id, (layer_name, style) in self._outputs.items():
            if not context.willLoadLayerOnCompletion(dest_id):
                continue
            details = context.layerToLoadOnCompletionDetails(dest_id)
            details.name = layer_name
            processor = _LayerPostProcessor(style)
            _POST_PROCESSORS.append(processor)
            details.setPostProcessor(processor)
        return {}

    @staticmethod
    def raise_if_cancelled(feedback: QgsProcessingFeedback | None) -> None:
        """Stops the run when the user cancelled it."""
        if feedback is not None and feedback.isCanceled():
            raise OperationCancelledError("The operation was cancelled.")

    def send(
        self,
        api_handler: ExternalApiHandler,
        request: Callable[[], dict[str, Any]],
        feedback: QgsProcessingFeedback,
    ) -> dict[str, Any]:
        """
        Sends one request and aborts it when the user cancels.

        The cancel signal is connected here, in the worker thread, so Qt
        queues ``abort`` onto the event loop that waits for the reply.
        """
        self.raise_if_cancelled(feedback)
        api_handler.last_pricing_bucket = None
        feedback.canceled.connect(api_handler.abort)
        try:
            try:
                result = request()
            except ApiError:
                self.raise_if_cancelled(feedback)
                raise
        finally:
            feedback.canceled.disconnect(api_handler.abort)
            bucket = api_handler.last_pricing_bucket
            self.pricing_bucket = bucket
            if bucket:
                feedback.pushInfo(
                    f"The {self.OPERATION} request used the {bucket} pricing bucket."
                )
        self.raise_if_cancelled(feedback)
        return result

    def write_layer(
        self,
        parameters: dict[str, Any],
        name: str,
        context: QgsProcessingContext,
        layer: QgsVectorLayer,
        *,
        optional: bool = False,
    ) -> str | None:
        """
        Copies a validated memory layer into an output sink.

        Returns the destination id, or ``None`` when an optional output was
        not requested.
        """
        sink, dest_id = self.parameterAsSink(
            parameters, name, context, layer.fields(), layer.wkbType(), layer.crs()
        )
        if sink is None:
            if optional:
                return None
            raise QgsProcessingException(self.invalidSinkError(parameters, name))
        features = list(layer.getFeatures())
        if features and not sink.addFeatures(features):
            raise QgsProcessingException(f"Could not write the {layer.name()} output.")
        self._outputs[dest_id] = (layer.name(), LayerStyle(layer))
        return dest_id
