"""Deterministic plan generation for safe gateway operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Self, cast

from airgap_ai_gateway.errors import ExecutionError, PlanError
from airgap_ai_gateway.ledger import LedgerState, ResourceRef, StateLedger
from airgap_ai_gateway.manifest import OVERLAYS, build_overlay, overlay_path
from airgap_ai_gateway.models import GatewayConfig
from airgap_ai_gateway.safety import mutation_state

APPLY_MODES = frozenset({"server-side-dry-run", "live"})
PLAN_SCHEMA_VERSION = "airgap.ai.gateway.plan/v1"
DEFAULT_OVERLAY = "retained-nginx-edge"


@dataclass(frozen=True, slots=True)
class Plan:
    """Compact plan used by non-mutating lifecycle commands."""

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


@dataclass(frozen=True, slots=True)
class PlanAction:
    """One executor action listed in an approved plan."""

    id: str
    kind: str
    description: str
    command: tuple[str, ...] = ()
    mutating: bool = False
    resource: ResourceRef | None = None
    payload: dict[str, object] | None = None
    sensitive_output: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Self:
        """Build an action from JSON data."""

        resource_payload = payload.get("resource")
        resource = None
        if isinstance(resource_payload, dict):
            resource = ResourceRef.from_dict(cast(dict[str, object], resource_payload))
        command_payload = payload.get("command", [])
        if not isinstance(command_payload, list) or not all(
            isinstance(item, str) for item in command_payload
        ):
            msg = "plan action command must be a list of strings"
            raise ExecutionError(msg)
        payload_value = payload.get("payload")
        action_payload: dict[str, object] | None = None
        if isinstance(payload_value, dict):
            action_payload = cast(dict[str, object], payload_value)
        return cls(
            id=_required_string(payload, "id"),
            kind=_required_string(payload, "kind"),
            description=_required_string(payload, "description"),
            command=tuple(command_payload),
            mutating=_bool(payload.get("mutating", False), "mutating"),
            resource=resource,
            payload=action_payload,
            sensitive_output=_bool(payload.get("sensitive_output", False), "sensitive_output"),
        )

    def to_dict(self) -> dict[str, object]:
        """Convert the action to deterministic JSON data."""

        return {
            "command": list(self.command),
            "description": self.description,
            "id": self.id,
            "kind": self.kind,
            "mutating": self.mutating,
            "payload": self.payload or {},
            "resource": self.resource.to_dict() if self.resource else None,
            "sensitive_output": self.sensitive_output,
        }


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """A saved and reviewable plan for state-changing operations."""

    schema_version: str
    plan_id: str
    command: str
    platform: str
    baseline: str
    overlay: str
    namespace: str
    apply_mode: str
    expected_context: str
    tls_verify: bool
    actions: tuple[PlanAction, ...]
    resources: tuple[ResourceRef, ...]
    notes: tuple[str, ...] = ()

    @classmethod
    def from_file(cls, path: Path) -> Self:
        """Load a plan from JSON."""

        if not path.exists():
            msg = f"plan file does not exist: {path}"
            raise ExecutionError(msg)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            msg = "plan file must contain a JSON object"
            raise ExecutionError(msg)
        return cls.from_dict(cast(dict[str, object], payload))

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Self:
        """Build an execution plan from JSON data."""

        actions_payload = payload.get("actions", [])
        resources_payload = payload.get("resources", [])
        notes_payload = payload.get("notes", [])
        if not isinstance(actions_payload, list):
            msg = "plan actions must be a list"
            raise ExecutionError(msg)
        if not isinstance(resources_payload, list):
            msg = "plan resources must be a list"
            raise ExecutionError(msg)
        if not isinstance(notes_payload, list) or not all(
            isinstance(item, str) for item in notes_payload
        ):
            msg = "plan notes must be a list of strings"
            raise ExecutionError(msg)

        actions: list[PlanAction] = []
        for item in actions_payload:
            if not isinstance(item, dict):
                msg = "plan actions must be objects"
                raise ExecutionError(msg)
            actions.append(PlanAction.from_dict(cast(dict[str, object], item)))

        resources: list[ResourceRef] = []
        for item in resources_payload:
            if not isinstance(item, dict):
                msg = "plan resources must be objects"
                raise ExecutionError(msg)
            resources.append(ResourceRef.from_dict(cast(dict[str, object], item)))

        return cls(
            schema_version=_required_string(payload, "schema_version"),
            plan_id=_required_string(payload, "plan_id"),
            command=_required_string(payload, "command"),
            platform=_required_string(payload, "platform"),
            baseline=_required_string(payload, "baseline"),
            overlay=_required_string(payload, "overlay"),
            namespace=_required_string(payload, "namespace"),
            apply_mode=_required_string(payload, "apply_mode"),
            expected_context=_required_string(payload, "expected_context"),
            tls_verify=_bool(payload.get("tls_verify", True), "tls_verify"),
            actions=tuple(actions),
            resources=tuple(resources),
            notes=tuple(notes_payload),
        )

    def to_dict(self, *, include_plan_id: bool = True) -> dict[str, object]:
        """Convert the plan to deterministic JSON data."""

        payload: dict[str, object] = {
            "actions": [action.to_dict() for action in self.actions],
            "apply_mode": self.apply_mode,
            "baseline": self.baseline,
            "command": self.command,
            "expected_context": self.expected_context,
            "namespace": self.namespace,
            "notes": list(self.notes),
            "overlay": self.overlay,
            "platform": self.platform,
            "resources": [resource.to_dict() for resource in sorted(self.resources)],
            "schema_version": self.schema_version,
            "tls_verify": self.tls_verify,
        }
        if include_plan_id:
            payload["plan_id"] = self.plan_id
        return payload

    def to_json(self) -> str:
        """Serialize the plan deterministically."""

        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        """Render a human-reviewable Markdown summary."""

        lines = [
            f"# {self.command.title()} Plan",
            "",
            f"- Plan ID: `{self.plan_id}`",
            f"- Platform: `{self.platform}`",
            f"- Baseline: `{self.baseline}`",
            f"- Overlay: `{self.overlay}`",
            f"- Namespace: `{self.namespace}`",
            f"- Apply mode: `{self.apply_mode}`",
            f"- Expected context: `{self.expected_context}`",
            f"- Public TLS verification: `{'enabled' if self.tls_verify else 'disabled'}`",
            "",
            "## Actions",
            "",
        ]
        for action in self.actions:
            mutation = "mutating" if action.mutating else "read-only"
            lines.append(f"- `{action.id}`: {action.description} ({mutation})")
        if self.resources:
            lines.extend(["", "## Planned resources", ""])
            for resource in sorted(self.resources):
                lines.append(f"- `{resource.identity}`")
        if self.notes:
            lines.extend(["", "## Notes", ""])
            lines.extend(f"- {note}" for note in self.notes)
        return "\n".join(lines) + "\n"

    def with_computed_id(self) -> ExecutionPlan:
        """Return a plan with a deterministic ID over all material fields."""

        canonical = json.dumps(self.to_dict(include_plan_id=False), sort_keys=True)
        plan_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return ExecutionPlan(
            schema_version=self.schema_version,
            plan_id=plan_id,
            command=self.command,
            platform=self.platform,
            baseline=self.baseline,
            overlay=self.overlay,
            namespace=self.namespace,
            apply_mode=self.apply_mode,
            expected_context=self.expected_context,
            tls_verify=self.tls_verify,
            actions=self.actions,
            resources=self.resources,
            notes=self.notes,
        )


def build_plan(config: GatewayConfig, action: str, *, mutating: bool = False) -> Plan:
    """Build a deterministic compatibility plan for non-state-changing commands."""

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


def build_execution_plan(
    config: GatewayConfig,
    *,
    command: str,
    overlay: str = DEFAULT_OVERLAY,
    apply_mode: str = "server-side-dry-run",
    skip_ratelimit: bool = False,
) -> ExecutionPlan:
    """Build a deterministic execution plan without contacting Kubernetes."""

    _validate_apply_mode(apply_mode)
    _validate_overlay(overlay)
    if command not in {"deploy apply", "cutover apply", "destroy apply"}:
        msg = f"execution planning is not supported for {command}"
        raise PlanError(msg)
    if skip_ratelimit and config.platform.rate_limit.enabled:
        msg = "skip-ratelimit conflicts with enabled rate-limit policy"
        raise PlanError(msg)

    namespace = config.platform.gateway.namespace
    resources = _resources_for_overlay(overlay)
    actions: list[PlanAction] = [_verify_context_action()]
    notes = [
        "The plan contains no runtime credential material.",
        "Generated data-plane resources are verified after reconciliation.",
    ]

    if command == "deploy apply":
        actions.extend(
            [
                PlanAction(
                    id="kubectl-diff",
                    kind="kubectl-diff",
                    description=f"Compare authored manifests for overlay {overlay}.",
                    command=("kubectl", "diff", "-k", str(overlay_path(overlay))),
                    mutating=False,
                ),
                PlanAction(
                    id="kubectl-apply",
                    kind="kubectl-apply",
                    description=f"Apply authored manifests for overlay {overlay}.",
                    command=_apply_command(overlay, apply_mode),
                    mutating=True,
                ),
                PlanAction(
                    id="poll-gateway",
                    kind="poll-gateway",
                    description="Wait for the Gateway to report Programmed=True.",
                    payload={
                        "name": config.platform.gateway.name,
                        "namespace": namespace,
                    },
                ),
            ]
        )
        actions.extend(_route_poll_actions(overlay))
        actions.extend(_policy_poll_actions(overlay))
        actions.extend(_deployment_poll_actions(overlay))
    elif command == "cutover apply":
        actions.extend(
            [
                PlanAction(
                    id="read-ingress-state",
                    kind="read-ingress-state",
                    description="Read current edge Ingress state before changing traffic.",
                    command=("kubectl", "-n", namespace, "get", "ingress", "-o", "json"),
                ),
                PlanAction(
                    id="kubectl-apply-cutover",
                    kind="kubectl-apply",
                    description=f"Apply the cutover overlay {overlay}.",
                    command=_apply_command(overlay, apply_mode),
                    mutating=True,
                ),
            ]
        )
    else:
        actions.extend(
            [
                PlanAction(
                    id="read-ingress-state",
                    kind="read-ingress-state",
                    description="Read current edge Ingress state before cleanup.",
                    command=("kubectl", "-n", namespace, "get", "ingress", "-o", "json"),
                ),
                PlanAction(
                    id="kubectl-delete",
                    kind="kubectl-delete",
                    description=f"Delete resources rendered by overlay {overlay}.",
                    command=(
                        "kubectl",
                        "delete",
                        "-k",
                        str(overlay_path(overlay)),
                        "--ignore-not-found=false",
                    ),
                    mutating=True,
                ),
            ]
        )

    return ExecutionPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        plan_id="",
        command=command,
        platform=config.platform.name,
        baseline=config.platform.baseline.agentgateway_version,
        overlay=overlay,
        namespace=namespace,
        apply_mode=apply_mode,
        expected_context=config.platform.cluster.expected_context,
        tls_verify=True,
        actions=tuple(actions),
        resources=resources,
        notes=tuple(notes),
    ).with_computed_id()


def build_rollback_execution_plan(
    config: GatewayConfig,
    *,
    ledger: StateLedger,
    run_id: str,
    apply_mode: str = "server-side-dry-run",
) -> ExecutionPlan:
    """Build a rollback plan from a state ledger without discovering live state."""

    _validate_apply_mode(apply_mode)
    entries = ledger.for_run(run_id).entries
    if not entries:
        msg = f"no ledger entries found for run {run_id}"
        raise PlanError(msg)

    actions: list[PlanAction] = [_verify_context_action()]
    resources: list[ResourceRef] = []
    for index, entry in enumerate(sorted(entries, key=lambda item: item.ref.identity), start=1):
        resources.append(entry.ref)
        if entry.state in (LedgerState.UPDATED, LedgerState.PRE_EXISTING):
            if entry.before is None:
                msg = f"ledger entry {entry.ref.identity} has no pre-change resource to restore"
                raise PlanError(msg)
            actions.append(
                PlanAction(
                    id=f"restore-{index:03d}",
                    kind="kubectl-restore",
                    description=f"Restore pre-change {entry.ref.kind} {entry.ref.name}.",
                    command=("kubectl", "apply", "-f", "-"),
                    mutating=True,
                    resource=entry.ref,
                    payload={"restore_identity": entry.ref.identity},
                    sensitive_output=entry.ref.kind == "Secret",
                )
            )
        elif entry.state is LedgerState.CREATED:
            actions.append(
                PlanAction(
                    id=f"delete-created-{index:03d}",
                    kind="kubectl-delete-resource",
                    description=f"Delete resource created by run {run_id}: {entry.ref.identity}.",
                    command=_delete_resource_command(entry.ref),
                    mutating=True,
                    resource=entry.ref,
                    sensitive_output=entry.ref.kind == "Secret",
                )
            )
        else:
            msg = f"unsupported ledger state {entry.state}"
            raise PlanError(msg)

    return ExecutionPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        plan_id="",
        command="rollback apply",
        platform=config.platform.name,
        baseline=config.platform.baseline.agentgateway_version,
        overlay="ledger",
        namespace=config.platform.gateway.namespace,
        apply_mode=apply_mode,
        expected_context=config.platform.cluster.expected_context,
        tls_verify=True,
        actions=tuple(actions),
        resources=tuple(resources),
        notes=("Rollback only acts on resources recorded for the selected run.",),
    ).with_computed_id()


def write_plan_files(plan: ExecutionPlan, output_dir: Path) -> tuple[Path, Path]:
    """Write deterministic JSON and Markdown plan artifacts."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "plan.json"
    markdown_path = output_dir / "plan.md"
    json_path.write_text(plan.to_json(), encoding="utf-8")
    markdown_path.write_text(plan.to_markdown(), encoding="utf-8")
    return json_path, markdown_path


def _validate_apply_mode(apply_mode: str) -> None:
    if apply_mode not in APPLY_MODES:
        msg = (
            f"unsupported apply mode {apply_mode}; expected one of {', '.join(sorted(APPLY_MODES))}"
        )
        raise PlanError(msg)


def _validate_overlay(overlay: str) -> None:
    if overlay not in OVERLAYS:
        msg = f"unknown overlay {overlay}; expected one of {', '.join(OVERLAYS)}"
        raise PlanError(msg)


def _verify_context_action() -> PlanAction:
    return PlanAction(
        id="verify-context",
        kind="verify-context",
        description="Verify the exact Kubernetes context before any state change.",
        command=("kubectl", "config", "current-context"),
    )


def _apply_command(overlay: str, apply_mode: str) -> tuple[str, ...]:
    base = ("kubectl", "apply", "--server-side")
    if apply_mode == "server-side-dry-run":
        return (*base, "--dry-run=server", "-k", str(overlay_path(overlay)))
    return (*base, "-k", str(overlay_path(overlay)))


def _delete_resource_command(ref: ResourceRef) -> tuple[str, ...]:
    resource = f"{ref.kind}.{ref.api_version}/{ref.name}"
    if ref.namespace:
        return ("kubectl", "-n", ref.namespace, "delete", resource, "--ignore-not-found=false")
    return ("kubectl", "delete", resource, "--ignore-not-found=false")


def _resources_for_overlay(overlay: str) -> tuple[ResourceRef, ...]:
    resources = tuple(ResourceRef.from_manifest(document) for document in build_overlay(overlay))
    return tuple(sorted(resources))


def _route_poll_actions(overlay: str) -> list[PlanAction]:
    actions: list[PlanAction] = []
    for resource in _resources_for_overlay(overlay):
        if resource.kind != "HTTPRoute":
            continue
        actions.append(
            PlanAction(
                id=f"poll-route-{resource.name}",
                kind="poll-httproute",
                description=f"Wait for HTTPRoute {resource.name} to report Accepted=True and ResolvedRefs=True.",
                payload={
                    "name": resource.name,
                    "namespace": resource.namespace,
                },
                resource=resource,
            )
        )
    return actions


def _policy_poll_actions(overlay: str) -> list[PlanAction]:
    actions: list[PlanAction] = []
    for resource in _resources_for_overlay(overlay):
        if resource.kind != "AgentgatewayPolicy":
            continue
        actions.append(
            PlanAction(
                id=f"poll-policy-{resource.name}",
                kind="poll-policy",
                description=(
                    f"Wait for AgentgatewayPolicy {resource.name} to report Accepted=True "
                    "and Attached=True."
                ),
                payload={"name": resource.name, "namespace": resource.namespace},
                resource=resource,
            )
        )
    return actions


def _deployment_poll_actions(overlay: str) -> list[PlanAction]:
    actions: list[PlanAction] = []
    for resource in _resources_for_overlay(overlay):
        if resource.kind != "Deployment":
            continue
        actions.append(
            PlanAction(
                id=f"poll-deployment-{resource.name}",
                kind="poll-deployment",
                description=f"Wait for Deployment {resource.name} rollout readiness.",
                payload={"name": resource.name, "namespace": resource.namespace},
                resource=resource,
            )
        )
    return actions


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        msg = f"{key} must be a non-empty string"
        raise ExecutionError(msg)
    return value


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        msg = f"{label} must be a boolean"
        raise ExecutionError(msg)
    return value
