from __future__ import annotations

from airgap_ai_gateway.redaction import REDACTED, redact_mapping


def test_redaction_hides_sensitive_mapping_values() -> None:
    payload = {
        "consumer": "internal-chat",
        "api_key": "example-only-do-not-use",
        "nested": {
            "Authorization": "Bearer example-only-do-not-use",
            "safe": "visible",
        },
    }

    assert redact_mapping(payload) == {
        "consumer": "internal-chat",
        "api_key": REDACTED,
        "nested": {
            "Authorization": REDACTED,
            "safe": "visible",
        },
    }
