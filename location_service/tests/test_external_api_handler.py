import unittest
from unittest.mock import Mock

from location_service.tests import HAS_QGIS

if HAS_QGIS:
    from location_service.utils import external_api_handler
    from location_service.utils.external_api_handler import ApiError, ExternalApiHandler


class _FakeBuffer:
    """Mimics the QByteArray returned by QNetworkReply.readAll()."""

    def __init__(self, body):
        self._body = body

    def data(self):
        return self._body


class _FakeReply:
    """Stands in for QNetworkReply without any network activity."""

    def __init__(
        self, error, body=b"", error_string="", status_code=None, headers=None
    ):
        self._error = error
        self._body = body
        self._error_string = error_string
        self._status_code = status_code
        self._headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.deleted = False

    def error(self):
        return self._error

    def errorString(self):
        return self._error_string

    def readAll(self):
        return _FakeBuffer(self._body)

    def attribute(self, _attribute):
        return self._status_code

    def rawHeader(self, name):
        return _FakeBuffer(self._headers.get(name.decode("ascii").lower(), b""))

    def deleteLater(self):
        self.deleted = True


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestHandleNetworkReply(unittest.TestCase):
    """Tests reply handling without touching the network."""

    def _handler(self):
        # Skip __init__ so no QgsNetworkAccessManager instance is required.
        handler = ExternalApiHandler.__new__(ExternalApiHandler)
        handler._active_reply = None
        handler.last_pricing_bucket = None
        return handler

    def test_decodes_successful_reply(self):
        reply = _FakeReply(
            external_api_handler._NoError,
            b'{"ResultItems": []}',
            status_code=200,
            headers={"x-amz-geo-pricing-bucket": b"PlacesCore"},
        )
        handler = self._handler()
        assert handler.handle_network_reply(reply) == {"ResultItems": []}
        assert handler.last_pricing_bucket == "PlacesCore"
        assert reply.deleted

    def test_error_includes_server_message_and_redacts_key(self):
        reply = _FakeReply(
            error=1,
            body=b'{"message": "Invalid request"}',
            error_string=(
                "Error transferring "
                "https://places.geo.ap-northeast-1.amazonaws.com/v2/geocode"
                "?key=v1.public.secret - server replied: Bad Request"
            ),
            status_code=400,
        )
        with self.assertRaises(ApiError) as context:
            self._handler().handle_network_reply(reply)
        assert context.exception.status_code == 400
        message = str(context.exception)
        assert "(Invalid request)" in message
        assert "[HTTP 400]" in message
        assert "key=***" in message
        assert "v1.public.secret" not in message
        assert reply.deleted

    def test_malformed_success_body_raises_runtime_error(self):
        reply = _FakeReply(external_api_handler._NoError, b"<html>not json</html>")
        with self.assertRaises(RuntimeError) as context:
            self._handler().handle_network_reply(reply)
        assert "Received an invalid response" in str(context.exception)
        assert reply.deleted

    def test_non_object_success_body_raises_runtime_error(self):
        reply = _FakeReply(external_api_handler._NoError, b"[]")
        with self.assertRaises(RuntimeError) as context:
            self._handler().handle_network_reply(reply)
        assert "top-level JSON value is not an object" in str(context.exception)
        assert reply.deleted

    def test_error_without_body_has_no_detail(self):
        reply = _FakeReply(error=1, body=b"", error_string="Connection refused")
        with self.assertRaises(RuntimeError) as context:
            self._handler().handle_network_reply(reply)
        assert str(context.exception) == "Network error occurred: Connection refused"
        assert reply.deleted

    def test_build_request_sets_safe_network_options(self):
        request = self._handler()._build_request("https://example.com/")
        assert request.attribute(external_api_handler._CacheLoadControlAttribute) == (
            external_api_handler._AlwaysNetwork
        )
        assert (
            request.attribute(external_api_handler._CacheSaveControlAttribute) is False
        )
        assert request.attribute(external_api_handler._RedirectPolicyAttribute) == (
            external_api_handler._SameOriginRedirectPolicy
        )
        if hasattr(request, "transferTimeout"):
            assert request.transferTimeout() == ExternalApiHandler.TIMEOUT_MS


class _FakeNetworkManager:
    """Records requests without performing network activity."""

    def __init__(self):
        self.get_count = 0
        self.post_count = 0
        self.posted_data = []

    def get(self, _request):
        self.get_count += 1
        return object()

    def post(self, _request, _data):
        self.post_count += 1
        self.posted_data.append(_data)
        return object()


@unittest.skipUnless(HAS_QGIS, "QGIS runtime is required")
class TestRequestSending(unittest.TestCase):
    """Tests that each call sends one request without starting an event loop."""

    def _handler(self):
        handler = ExternalApiHandler.__new__(ExternalApiHandler)
        handler.network_manager = _FakeNetworkManager()
        handler._active_reply = None
        handler.last_pricing_bucket = None
        handler._build_request = Mock(return_value=Mock())
        return handler

    def test_get_returns_dict_and_sends_once(self):
        handler = self._handler()
        handler._execute_reply = Mock(return_value={"ResultItems": []})

        result = handler.send_json_get_request("https://example.com/")

        assert result == {"ResultItems": []}
        assert handler.network_manager.get_count == 1

    def test_post_still_returns_dict_and_sends_json(self):
        handler = self._handler()
        handler._execute_reply = Mock(return_value={"Routes": []})

        result = handler.send_json_post_request(
            "https://example.com/", {"MaxAlternatives": 0}
        )

        assert result == {"Routes": []}
        assert handler.network_manager.post_count == 1
        assert handler.network_manager.posted_data == [b'{"MaxAlternatives": 0}']

    def test_get_errors_are_not_retried(self):
        for status_code in (400, 429, 500):
            with self.subTest(status_code=status_code):
                handler = self._handler()
                handler._execute_reply = Mock(
                    side_effect=ApiError("Request failed", status_code=status_code)
                )

                with self.assertRaises(ApiError):
                    handler.send_json_get_request("https://example.com/")

                assert handler.network_manager.get_count == 1

    def test_abort_stops_active_reply(self):
        handler = self._handler()
        handler._active_reply = Mock()

        handler.abort()

        handler._active_reply.abort.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
