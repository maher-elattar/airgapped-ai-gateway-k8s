"""Redaction helpers used by reports and command logs."""

from __future__ import annotations

import re

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
        "value",
    }
)

REDACTED = "<redacted>"

_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(token\s*[:=]\s*)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(secret\s*[:=]\s*)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"example-secret-value-do-not-leak"),
)


def redact_text(value: str) -> str:
    """Redact secret-looking content from free-form text."""

    result = value
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        result = pattern.sub(_replace_sensitive_match, result)
    return result


def redact_mapping(value: object) -> object:
    """Recursively redact likely secret-bearing mapping keys."""

    if isinstance(value, dict):
        if value.get("kind") == "Secret":
            redacted_secret: dict[object, object] = {}
            for key, item in value.items():
                if str(key) in {"data", "stringData"}:
                    redacted_secret[key] = REDACTED
                else:
                    redacted_secret[key] = redact_mapping(item)
            return redacted_secret
        redacted: dict[object, object] = {}
        for key, item in value.items():
            key_text = str(key).lower().replace("-", "_")
            if any(sensitive in key_text for sensitive in SENSITIVE_KEYS):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_mapping(item)
        return redacted
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _replace_sensitive_match(match: re.Match[str]) -> str:
    if match.lastindex:
        return f"{match.group(1)}{REDACTED}"
    return REDACTED
