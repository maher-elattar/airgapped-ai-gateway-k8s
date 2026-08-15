"""Offline discovery skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from airgap_ai_gateway.errors import DiscoveryError
from airgap_ai_gateway.models import GatewayConfig


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    """A reviewable discovery result."""

    platform: str
    expected_context: str
    model_count: int
    consumer_count: int
    status: str


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    """One read-only discovery candidate."""

    name: str
    namespace: str
    score: int
    details: str


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


def select_unique_candidate(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    override: str | None = None,
    field_name: str = "resource",
) -> DiscoveryCandidate:
    """Select a unique highest-scoring candidate or fail with override details."""

    if not candidates:
        msg = f"no {field_name} candidates discovered"
        raise DiscoveryError(msg)
    if override is not None:
        for candidate in candidates:
            if candidate.name == override or f"{candidate.namespace}/{candidate.name}" == override:
                return candidate
        msg = f"{field_name} override {override!r} did not match any discovered candidate"
        raise DiscoveryError(msg)

    sorted_candidates = tuple(
        sorted(candidates, key=lambda item: (-item.score, item.namespace, item.name))
    )
    winner = sorted_candidates[0]
    tied = tuple(candidate for candidate in sorted_candidates if candidate.score == winner.score)
    if len(tied) > 1:
        candidate_details = ", ".join(
            f"{candidate.namespace}/{candidate.name} score={candidate.score} ({candidate.details})"
            for candidate in tied
        )
        msg = (
            f"ambiguous {field_name} discovery: {candidate_details}; "
            f"provide an explicit {field_name} override"
        )
        raise DiscoveryError(msg)
    return winner
