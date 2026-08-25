from __future__ import annotations

import json
from typing import Any

from qgis.core import QgsNetworkAccessManager
from qgis.PyQt.QtCore import QEventLoop, QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from .redaction import redact_secrets

_ContentTypeHeader = QNetworkRequest.KnownHeaders.ContentTypeHeader
_NoError = QNetworkReply.NetworkError.NoError
_HttpStatusCodeAttribute = QNetworkRequest.Attribute.HttpStatusCodeAttribute
_CacheLoadControlAttribute = QNetworkRequest.Attribute.CacheLoadControlAttribute
_CacheSaveControlAttribute = QNetworkRequest.Attribute.CacheSaveControlAttribute
_RedirectPolicyAttribute = QNetworkRequest.Attribute.RedirectPolicyAttribute
_AlwaysNetwork = QNetworkRequest.CacheLoadControl.AlwaysNetwork
_SameOriginRedirectPolicy = QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy


class ApiError(RuntimeError):
    """An API request error with the HTTP details needed by callers."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        if status_code is not None:
            message = f"{message} [HTTP {status_code}]"
        super().__init__(message)
        self.status_code = status_code


class ExternalApiHandler:
    """Sends JSON requests through QGIS's network manager."""

    JSON_CONTENT_TYPE = "application/json"
    UTF8_ENCODING = "utf-8"
    TIMEOUT_MS = 30000

    def __init__(self) -> None:
        """Initializes the QGIS network manager and reply state."""
        self.network_manager = QgsNetworkAccessManager.instance()
        self._active_reply: QNetworkReply | None = None
        self.last_pricing_bucket: str | None = None

    def abort(self) -> None:
        """Cancels the current reply, if any."""
        if self._active_reply is not None:
            self._active_reply.abort()

    def _build_request(self, url: str) -> QNetworkRequest:
        """Builds a request with safe network defaults."""
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(_CacheLoadControlAttribute, _AlwaysNetwork)
        request.setAttribute(_CacheSaveControlAttribute, False)
        request.setAttribute(_RedirectPolicyAttribute, _SameOriginRedirectPolicy)
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(self.TIMEOUT_MS)
        return request

    def send_json_post_request(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Posts JSON and returns the decoded response.

        Network failures and malformed response data raise a redacted
        ``RuntimeError``; serialization errors propagate unchanged.
        """
        # Reject NaN and infinity instead of sending non-standard JSON literals.
        encoded_data = json.dumps(data, allow_nan=False).encode(self.UTF8_ENCODING)
        return self._send_request("POST", url, encoded_data)

    def send_json_get_request(self, url: str) -> dict[str, Any]:
        """
        Gets JSON and returns the decoded response.

        Network failures and malformed response data raise a redacted
        ``RuntimeError``.
        """
        return self._send_request("GET", url)

    def _send_request(
        self, method: str, url: str, encoded_data: bytes | None = None
    ) -> dict[str, Any]:
        """Sends one request without automatic retries."""
        self.last_pricing_bucket = None

        request = self._build_request(url)
        if method == "POST":
            request.setHeader(_ContentTypeHeader, self.JSON_CONTENT_TYPE)
            reply = self.network_manager.post(request, encoded_data)
        else:
            reply = self.network_manager.get(request)
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

        Network failures and malformed response data raise a redacted
        ``RuntimeError``.
        """
        try:
            status_code = self._http_status_code(reply)
            self.last_pricing_bucket = self._header_value(
                reply, "x-amz-geo-pricing-bucket"
            )
            if reply.error() == _NoError:  # type: ignore
                try:
                    response_data = reply.readAll().data().decode(self.UTF8_ENCODING)
                    response = json.loads(response_data)
                    if not isinstance(response, dict):
                        raise ValueError("the top-level JSON value is not an object")
                    return response
                except (UnicodeDecodeError, ValueError) as e:
                    raise ApiError(
                        redact_secrets(f"Received an invalid response: {e}"),
                        status_code=status_code,
                    ) from e
            else:
                # Redact credential-like query parameters in Qt and server messages.
                detail = self._extract_error_detail(reply)
                error_msg = f"Network error occurred: {reply.errorString()}"
                if detail:
                    error_msg = f"{error_msg} ({detail})"
                raise ApiError(
                    redact_secrets(error_msg),
                    status_code=status_code,
                )
        finally:
            reply.deleteLater()

    def _http_status_code(self, reply: QNetworkReply) -> int | None:
        """Returns the HTTP status code when Qt provides one."""
        status_code = reply.attribute(_HttpStatusCodeAttribute)
        try:
            return int(status_code)
        except (TypeError, ValueError):
            return None

    def _header_value(self, reply: QNetworkReply, name: str) -> str | None:
        """Reads a response header from either supported Qt version."""
        value = reply.rawHeader(name.encode("ascii"))
        if hasattr(value, "data"):
            value = value.data()
        if isinstance(value, bytes):
            value = value.decode("ascii", errors="replace")
        value = str(value).strip()
        return value or None

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
