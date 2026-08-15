"""Verification skeletons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from airgap_ai_gateway.errors import VerificationError
from airgap_ai_gateway.models import GatewayConfig


def verification_plan(config: GatewayConfig) -> dict[str, object]:
    """Return checks that a future implementation must perform."""

    return {
        "status": "verification-skeleton",
        "expected_context": config.platform.cluster.expected_context,
        "checks": [
            "gateway-programmed",
            "routes-attached",
            "policies-attached",
            "missing-key-401",
            "denied-consumer-403",
            "allowed-consumer-200",
            "rate-limit-429",
        ],
    }


@dataclass(frozen=True, slots=True)
class HttpProbeSpec:
    """HTTP probe settings for public verification paths."""

    url: str
    verify_tls: bool = True
    timeout_seconds: int = 30


def verify_embedding_response(status_code: int, payload: dict[str, Any]) -> None:
    """Require a valid embedding vector in a successful embedding response."""

    if status_code != 200:
        msg = f"embedding verification expected HTTP 200, got {status_code}"
        raise VerificationError(msg)
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        msg = "embedding verification failed: response has no data array"
        raise VerificationError(msg)
    first = data[0]
    if not isinstance(first, dict):
        msg = "embedding verification failed: first data item is not an object"
        raise VerificationError(msg)
    embedding = first.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        msg = "embedding verification failed: HTTP 200 response did not include a vector"
        raise VerificationError(msg)
    if not all(isinstance(value, int | float) for value in embedding):
        msg = "embedding verification failed: vector contains non-numeric values"
        raise VerificationError(msg)
