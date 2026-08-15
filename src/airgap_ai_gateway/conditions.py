"""Bounded Kubernetes condition polling helpers."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol, cast

from airgap_ai_gateway.command import CommandResult
from airgap_ai_gateway.errors import VerificationError


class ConditionRunner(Protocol):
    """Command runner surface required by condition polling."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        sensitive_output: bool = False,
    ) -> CommandResult:
        """Run a command and return captured output."""


def poll_gateway_programmed(
    runner: ConditionRunner,
    *,
    namespace: str,
    name: str,
    attempts: int = 5,
) -> None:
    """Wait for Gateway Programmed=True."""

    _poll_condition(
        runner,
        command=("kubectl", "-n", namespace, "get", "gateway", name, "-o", "json"),
        checker=lambda payload: _has_condition(payload, "Programmed"),
        label=f"Gateway {namespace}/{name} Programmed=True",
        attempts=attempts,
    )


def poll_httproute_ready(
    runner: ConditionRunner,
    *,
    namespace: str,
    name: str,
    attempts: int = 5,
) -> None:
    """Wait for HTTPRoute Accepted=True and ResolvedRefs=True."""

    _poll_condition(
        runner,
        command=("kubectl", "-n", namespace, "get", "httproute", name, "-o", "json"),
        checker=lambda payload: (
            _has_condition(payload, "Accepted") and _has_condition(payload, "ResolvedRefs")
        ),
        label=f"HTTPRoute {namespace}/{name} Accepted=True and ResolvedRefs=True",
        attempts=attempts,
    )


def poll_policy_ready(
    runner: ConditionRunner,
    *,
    namespace: str,
    name: str,
    attempts: int = 5,
) -> None:
    """Wait for AgentgatewayPolicy Accepted=True and Attached=True."""

    _poll_condition(
        runner,
        command=(
            "kubectl",
            "-n",
            namespace,
            "get",
            "agentgatewaypolicy",
            name,
            "-o",
            "json",
        ),
        checker=lambda payload: (
            _has_condition(payload, "Accepted") and _has_condition(payload, "Attached")
        ),
        label=f"AgentgatewayPolicy {namespace}/{name} Accepted=True and Attached=True",
        attempts=attempts,
    )


def poll_deployment_ready(
    runner: ConditionRunner,
    *,
    namespace: str,
    name: str,
    attempts: int = 5,
) -> None:
    """Wait for Deployment rollout readiness."""

    _poll_condition(
        runner,
        command=("kubectl", "-n", namespace, "get", "deployment", name, "-o", "json"),
        checker=_deployment_is_ready,
        label=f"Deployment {namespace}/{name} rollout readiness",
        attempts=attempts,
    )


def _poll_condition(
    runner: ConditionRunner,
    *,
    command: tuple[str, ...],
    checker: Callable[[dict[str, Any]], bool],
    label: str,
    attempts: int,
) -> None:
    if attempts < 1:
        msg = "condition polling attempts must be positive"
        raise VerificationError(msg)
    last_error = ""
    for _ in range(attempts):
        result = runner.run(command)
        if result.returncode != 0:
            last_error = result.stderr.strip() or f"command exited {result.returncode}"
            continue
        payload = _parse_json_object(result.stdout, label)
        if checker(payload):
            return
        last_error = "condition not ready"
    suffix = f": {last_error}" if last_error else ""
    msg = f"timed out waiting for {label}{suffix}"
    raise VerificationError(msg)


def _has_condition(payload: dict[str, Any], condition_type: str) -> bool:
    for condition in _status_conditions(payload):
        if condition.get("type") == condition_type and condition.get("status") == "True":
            return True
    return False


def _deployment_is_ready(payload: dict[str, Any]) -> bool:
    spec = payload.get("spec", {})
    status = payload.get("status", {})
    metadata = payload.get("metadata", {})
    if not isinstance(spec, dict) or not isinstance(status, dict) or not isinstance(metadata, dict):
        return False
    desired = spec.get("replicas", 1)
    generation = metadata.get("generation", 0)
    observed = status.get("observedGeneration", -1)
    available = status.get("availableReplicas", 0)
    if not all(isinstance(value, int) for value in (desired, generation, observed, available)):
        return False
    return int(observed) >= int(generation) and int(available) >= int(desired)


def _status_conditions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    status = payload.get("status", {})
    if not isinstance(status, dict):
        return []
    parents = status.get("parents")
    if isinstance(parents, list):
        conditions: list[dict[str, Any]] = []
        for parent in parents:
            if not isinstance(parent, dict):
                continue
            parent_conditions = parent.get("conditions", [])
            if isinstance(parent_conditions, list):
                conditions.extend(
                    cast(dict[str, Any], item)
                    for item in parent_conditions
                    if isinstance(item, dict)
                )
        return conditions
    conditions_value = status.get("conditions", [])
    if not isinstance(conditions_value, list):
        return []
    return [cast(dict[str, Any], item) for item in conditions_value if isinstance(item, dict)]


def _parse_json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"{label} returned invalid JSON"
        raise VerificationError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"{label} returned non-object JSON"
        raise VerificationError(msg)
    return cast(dict[str, Any], payload)
