"""Rollback planning skeleton."""

from __future__ import annotations

from airgap_ai_gateway.models import GatewayConfig


def rollback_plan(config: GatewayConfig) -> dict[str, object]:
    """Return the rollback sequence without touching live state."""

    return {
        "status": "rollback-plan-skeleton",
        "first_action": "restore-previous-edge-path",
        "gateway_namespace": config.platform.gateway.namespace,
        "keep_model_workloads": True,
    }
