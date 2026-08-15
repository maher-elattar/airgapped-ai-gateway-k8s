"""Redaction helpers used by reports and future command logs."""

from __future__ import annotations

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "client key data",
        "credential",
        "password",
        "secret",
        "token",
    }
)

REDACTED = "<redacted>"


def redact_mapping(value: object) -> object:
    """Recursively redact likely secret-bearing mapping keys."""

    if isinstance(value, dict):
        redacted: dict[object, object] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(sensitive in key_text for sensitive in SENSITIVE_KEYS):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_mapping(item)
        return redacted
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value
