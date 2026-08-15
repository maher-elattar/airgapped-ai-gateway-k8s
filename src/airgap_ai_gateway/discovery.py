"""Offline discovery skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from airgap_ai_gateway.models import GatewayConfig


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    """A reviewable discovery result."""

    platform: str
    expected_context: str
    model_count: int
    consumer_count: int
    status: str


def discover(config: GatewayConfig) -> DiscoveryReport:
    """Return an offline discovery report.

    The real implementation will inspect cluster state only through a disposable
    and verified context. The scaffold intentionally stays offline.
    """

    return DiscoveryReport(
        platform=config.platform.name,
        expected_context=config.platform.cluster.expected_context,
        model_count=len(config.models),
        consumer_count=len(config.consumers),
        status="offline-discovery-skeleton",
    )
