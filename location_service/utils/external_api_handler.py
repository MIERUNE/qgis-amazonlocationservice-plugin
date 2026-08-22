from __future__ import annotations

import json
from typing import Any

from qgis.core import QgsNetworkAccessManager
from qgis.PyQt.QtCore import QEventLoop, QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from .redaction import redact_secrets

try:
    _ContentTypeHeader = QNetworkRequest.ContentTypeHeader
except AttributeError:
    _ContentTypeHeader = QNetworkRequest.KnownHeaders.ContentTypeHeader

try:
    _NoError = QNetworkReply.NoError
except AttributeError:
    _NoError = QNetworkReply.NetworkError.NoError


class ExternalApiHandler:
    """Sends JSON requests through QGIS's network manager."""

    JSON_CONTENT_TYPE = "application/json"
    UTF8_ENCODING = "utf-8"
    TIMEOUT_MS = 30000

    def __init__(self) -> None:
        """Initializes the QGIS network manager and reply state."""
        self.network_manager = QgsNetworkAccessManager.instance()
        self._active_reply: QNetworkReply | None = None

    def abort(self) -> None:
        """Aborts the reply currently being awaited, if any."""
        if self._active_reply is not None:
            self._active_reply.abort()

    def _build_request(self, url: str) -> QNetworkRequest:
        """Builds a request and sets a timeout when Qt supports it."""
        request = QNetworkRequest(QUrl(url))
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(self.TIMEOUT_MS)
        return request

    def send_json_post_request(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Posts JSON and returns the decoded response.

        Network failures raise a redacted ``RuntimeError``; serialization and
        decoding errors propagate unchanged.
        """
        request = self._build_request(url)
        request.setHeader(_ContentTypeHeader, self.JSON_CONTENT_TYPE)
        # Reject NaN and infinity instead of sending non-standard JSON literals.
        encoded_data = json.dumps(data, allow_nan=False).encode(self.UTF8_ENCODING)
        reply = self.network_manager.post(request, encoded_data)
        return self._execute_reply(reply)

    def _execute_reply(self, reply: QNetworkReply) -> dict[str, Any]:
        """Waits for a reply while exposing it to ``abort()``, then parses it."""
        event_loop = QEventLoop()
        reply.finished.connect(event_loop.quit)
        self._active_reply = reply
        try:
            event_loop.exec()
        finally:
            self._active_reply = None
        return self.handle_network_reply(reply)

    def handle_network_reply(self, reply: QNetworkReply) -> dict[str, Any]:
        """
        Decodes a reply and schedules it for deletion.

        Network failures raise a redacted ``RuntimeError``; malformed response
        data propagates its decode error.
        """
        try:
            if reply.error() == _NoError:  # type: ignore
                response_data = reply.readAll().data().decode(self.UTF8_ENCODING)
                return json.loads(response_data)
            else:
                # Redact credential-like query parameters in Qt and server messages.
                detail = self._extract_error_detail(reply)
                error_msg = f"Network error occurred: {reply.errorString()}"
                if detail:
                    error_msg = f"{error_msg} ({detail})"
                raise RuntimeError(redact_secrets(error_msg))
        finally:
            reply.deleteLater()

    def _extract_error_detail(self, reply: QNetworkReply) -> str:
        """Returns the message from an Amazon Location error body, if present."""
        try:
            body = reply.readAll().data().decode(self.UTF8_ENCODING)
            if not body:
                return ""
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                return parsed.get("message") or parsed.get("Message") or ""
        except (ValueError, UnicodeDecodeError):
            return ""
        return ""
