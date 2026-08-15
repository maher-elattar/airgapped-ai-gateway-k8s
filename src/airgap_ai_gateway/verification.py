"""Verification skeletons."""

from __future__ import annotations

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
