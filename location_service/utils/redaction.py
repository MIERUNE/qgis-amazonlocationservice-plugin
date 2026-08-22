import re

# Query-parameter values that must be redacted from user-visible text.
_SECRET_PARAM = re.compile(
    r"\b((?:api[-_]?key|key|token|signature)=)[^&\s\"']+", re.IGNORECASE
)


def redact_secrets(text: str) -> str:
    """Redacts credential-like query-parameter values from text."""
    return _SECRET_PARAM.sub(r"\1***", text)
