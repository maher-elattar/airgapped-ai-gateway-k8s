"""Plan generation for safe CLI skeleton commands."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from airgap_ai_gateway.models import GatewayConfig
from airgap_ai_gateway.safety import mutation_state


@dataclass(frozen=True, slots=True)
class Plan:
    """A small plan object returned by skeleton commands."""

    action: str
    platform: str
    baseline: str
    gateway_namespace: str
    models: tuple[str, ...]
    consumers: tuple[str, ...]
    state: str
    mutating: bool

    def to_dict(self) -> dict[str, object]:
        """Convert the plan to a JSON-friendly dictionary."""

        return asdict(self)


def build_plan(config: GatewayConfig, action: str, *, mutating: bool = False) -> Plan:
    """Build a deterministic plan for a CLI command."""

    return Plan(
        action=action,
        platform=config.platform.name,
        baseline=config.platform.baseline.agentgateway_version,
        gateway_namespace=config.platform.gateway.namespace,
        models=tuple(model.key for model in config.models),
        consumers=tuple(consumer.key for consumer in config.consumers),
        state=mutation_state(action),
        mutating=mutating,
    )
