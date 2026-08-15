"""Safety gates for commands that would eventually mutate infrastructure."""

from __future__ import annotations

from airgap_ai_gateway.errors import SafetyError
from airgap_ai_gateway.models import GatewayConfig

MUTATING_ACTIONS = frozenset(
    {
        "deploy apply",
        "cutover apply",
        "rollback apply",
        "destroy apply",
    }
)


def ensure_mutation_is_confirmed(
    *,
    action: str,
    config: GatewayConfig,
    expected_context: str | None,
    confirmation: str | None,
) -> None:
    """Refuse a future mutation unless the context and token are exact."""

    if action not in MUTATING_ACTIONS:
        return

    configured_context = config.platform.cluster.expected_context
    if expected_context is None or expected_context != configured_context:
        msg = (
            f"{action} refused: pass --expected-context {configured_context!r}. "
            "The CLI does not infer or inspect the current cluster context."
        )
        raise SafetyError(msg)

    configured_token = config.platform.confirmation_token
    if confirmation is None or confirmation != configured_token:
        msg = f"{action} refused: pass --confirm with the exact configured confirmation token."
        raise SafetyError(msg)


def mutation_state(action: str) -> str:
    """Return the current implementation state for a command."""

    if action in MUTATING_ACTIONS:
        return "requires approved plan, exact context, apply mode, confirmation, and pre-change snapshot"
    return "offline plan"
