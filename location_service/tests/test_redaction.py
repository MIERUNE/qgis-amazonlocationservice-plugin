import unittest

from location_service.utils.redaction import redact_secrets


class TestRedactSecrets(unittest.TestCase):
    """Redacts credential-like query parameters from user-visible messages."""

    def test_masks_key_in_url(self):
        text = (
            "Error transferring https://places.geo.ap-northeast-1.amazonaws.com"
            "/v2/search-text?key=v1.public.abc123 - server replied: Forbidden"
        )
        redacted = redact_secrets(text)
        assert "v1.public.abc123" not in redacted
        assert "key=***" in redacted

    def test_masks_other_credential_params(self):
        assert redact_secrets("url?APIkey=abc") == "url?APIkey=***"
        assert redact_secrets("api_key=abc&x=1") == "api_key=***&x=1"
        assert redact_secrets("token=abc") == "token=***"

    def test_masks_bare_key_in_prose(self):
        assert redact_secrets("Invalid value for key=abc") == (
            "Invalid value for key=***"
        )

    def test_leaves_ordinary_text_alone(self):
        assert redact_secrets("monkey=banana") == "monkey=banana"
        assert redact_secrets("Network error occurred") == "Network error occurred"


if __name__ == "__main__":
    unittest.main()
