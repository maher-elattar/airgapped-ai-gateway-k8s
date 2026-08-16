"""Source-of-truth lifecycle planning for models and consumers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Self, cast

import yaml

from airgap_ai_gateway.configuration import load_config, validate_config
from airgap_ai_gateway.errors import PlanError, SafetyError
from airgap_ai_gateway.models import (
    ConsumerConfig,
    ConsumerRateLimits,
    GatewayConfig,
    ModelConfig,
    ModelKind,
    RouteBackend,
    ServicePort,
    ServiceRef,
)

LIFECYCLE_PLAN_SCHEMA = "airgap.ai.gateway.lifecycle-plan/v1"
FAKE_ROTATED_PLACEHOLDER = "REPLACE_AT_RUNTIME_ROTATED"
REVOKED_PLACEHOLDER = "REVOKED_AT_RUNTIME"


class _LiteralString(str):
    """String marker rendered as a YAML literal block."""


class _LifecycleDumper(yaml.SafeDumper):
    """YAML dumper used for planned source files."""


def _represent_literal_string(
    dumper: yaml.SafeDumper,
    value: _LiteralString,
) -> yaml.nodes.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")


_LifecycleDumper.add_representer(_LiteralString, _represent_literal_string)


@dataclass(frozen=True, slots=True)
class SourceChange:
    """One planned source-file write."""

    path: str
    before_sha256: str | None
    after_sha256: str
    content: str

    @classmethod
    def from_content(cls, path: Path, content: str) -> Self:
        """Create a planned write from the current file state."""

        before = _sha256_text(path.read_text(encoding="utf-8")) if path.exists() else None
        return cls(
            path=path.as_posix(),
            before_sha256=before,
            after_sha256=_sha256_text(content),
            content=content,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Self:
        """Parse a source change."""

        before = payload.get("before_sha256")
        return cls(
            path=_required_string(payload, "path"),
            before_sha256=_optional_string(before, "before_sha256"),
            after_sha256=_required_string(payload, "after_sha256"),
            content=_required_string(payload, "content"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready change."""

        return {
            "after_sha256": self.after_sha256,
            "before_sha256": self.before_sha256,
            "content": self.content,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class LifecyclePlan:
    """Deterministic source-change plan."""

    schema_version: str
    plan_id: str
    action: str
    config_path: str
    changes: tuple[SourceChange, ...]
    notes: tuple[str, ...] = ()

    @classmethod
    def from_file(cls, path: Path) -> Self:
        """Load a lifecycle plan."""

        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            msg = "lifecycle plan file must contain a JSON object"
            raise PlanError(msg)
        return cls.from_dict(cast(dict[str, object], payload))

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Self:
        """Parse a lifecycle plan."""

        changes = payload.get("changes")
        notes = payload.get("notes", [])
        if not isinstance(changes, list) or not all(isinstance(item, dict) for item in changes):
            msg = "lifecycle plan changes must be objects"
            raise PlanError(msg)
        if not isinstance(notes, list) or not all(isinstance(item, str) for item in notes):
            msg = "lifecycle plan notes must be strings"
            raise PlanError(msg)
        return cls(
            schema_version=_required_string(payload, "schema_version"),
            plan_id=_required_string(payload, "plan_id"),
            action=_required_string(payload, "action"),
            config_path=_required_string(payload, "config_path"),
            changes=tuple(
                SourceChange.from_dict(cast(dict[str, object], item)) for item in changes
            ),
            notes=tuple(notes),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready plan."""

        return {
            "action": self.action,
            "changes": [change.to_dict() for change in self.changes],
            "config_path": self.config_path,
            "notes": list(self.notes),
            "plan_id": self.plan_id,
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        """Serialize the plan deterministically."""

        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def with_computed_id(self) -> LifecyclePlan:
        """Return the plan with a content-derived id."""

        payload = {**self.to_dict(), "plan_id": ""}
        plan_id = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return LifecyclePlan(
            schema_version=self.schema_version,
            plan_id=plan_id,
            action=self.action,
            config_path=self.config_path,
            changes=self.changes,
            notes=self.notes,
        )

    def write(self, output_dir: Path) -> tuple[Path, Path]:
        """Write JSON and Markdown summaries."""

        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "plan.json"
        markdown_path = output_dir / "plan.md"
        json_path.write_text(self.to_json(), encoding="utf-8")
        markdown_path.write_text(self.to_markdown(), encoding="utf-8")
        return json_path, markdown_path

    def to_markdown(self) -> str:
        """Return a short human review summary."""

        lines = [
            f"# Lifecycle plan `{self.plan_id}`",
            "",
            f"- Action: `{self.action}`",
            f"- Config path: `{self.config_path}`",
            f"- Planned source changes: `{len(self.changes)}`",
            "",
            "## Files",
            "",
        ]
        for change in self.changes:
            before = change.before_sha256 or "new-file"
            lines.append(f"- `{change.path}`: `{before}` -> `{change.after_sha256}`")
        if self.notes:
            lines.extend(["", "## Notes", ""])
            lines.extend(f"- {note}" for note in self.notes)
        return "\n".join(lines) + "\n"


def apply_lifecycle_plan(
    plan: LifecyclePlan,
    *,
    repo_root: Path,
    config_path: Path,
) -> dict[str, object]:
    """Apply source changes only when the reviewed hashes still match."""

    expected = plan.with_computed_id().plan_id
    if expected != plan.plan_id:
        msg = "lifecycle plan_id does not match the approved plan contents"
        raise SafetyError(msg)
    if str(config_path) != plan.config_path:
        msg = "lifecycle plan config path does not match the apply command"
        raise SafetyError(msg)

    written: list[str] = []
    for change in plan.changes:
        path = _safe_repo_path(repo_root, change.path)
        current = _sha256_text(path.read_text(encoding="utf-8")) if path.exists() else None
        if current != change.before_sha256:
            msg = f"refusing to write {change.path}: source changed after plan review"
            raise SafetyError(msg)
        if _sha256_text(change.content) != change.after_sha256:
            msg = f"refusing to write {change.path}: planned content hash mismatch"
            raise SafetyError(msg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(change.content, encoding="utf-8")
        written.append(change.path)

    return {
        "action": plan.action,
        "plan_id": plan.plan_id,
        "status": "applied",
        "written": written,
    }


def build_model_add_plan(
    *,
    config_path: Path,
    model: ModelConfig,
    grant_consumer_key: str | None,
) -> LifecyclePlan:
    """Build a source-change plan for adding one model."""

    if not config_path.is_dir():
        msg = "model add planning requires a configuration directory"
        raise PlanError(msg)
    config = load_config(config_path)
    if any(item.key == model.key for item in config.models):
        return _empty_plan(
            action="model add",
            config_path=config_path,
            note=f"model {model.key} already exists; no source changes planned",
        )
    candidate = GatewayConfig(
        platform=config.platform,
        models=(*config.models, model),
        consumers=config.consumers,
    )
    validate_config(candidate)

    changes: list[SourceChange] = []
    model_file = config_path / "models" / f"{model.key}.yaml"
    changes.append(
        SourceChange.from_content(model_file, _dump_yaml({"models": [_model_dict(model)]}))
    )
    changes.extend(_manifest_changes_for_model(model))
    if grant_consumer_key is not None:
        changes.append(
            _updated_consumers_change(
                config_path,
                model_key=model.key,
                consumer_key=grant_consumer_key,
            )
        )
        changes.append(
            _updated_ratelimit_change(
                model_keys=(model.key,),
                consumer_key=grant_consumer_key,
                config=config,
            )
        )

    return LifecyclePlan(
        schema_version=LIFECYCLE_PLAN_SCHEMA,
        plan_id="",
        action="model add",
        config_path=str(config_path),
        changes=tuple(_dedupe_changes(changes)),
        notes=(
            "Model access remains default-deny until a selected consumer is granted.",
            "Runtime credential values are not generated by this plan.",
        ),
    ).with_computed_id()


def build_consumer_plan(
    *,
    config_path: Path,
    action: str,
    consumer_key: str,
    display_name: str | None = None,
    allowed_models: tuple[str, ...] = (),
    requests_per_minute: int | None = None,
) -> LifecyclePlan:
    """Build a source-change plan for consumer add, rotate, or revoke."""

    if not config_path.is_dir():
        msg = "consumer lifecycle planning requires a configuration directory"
        raise PlanError(msg)
    config = load_config(config_path)
    existing = next((item for item in config.consumers if item.key == consumer_key), None)
    changes: list[SourceChange] = []

    if action == "consumer add":
        if existing is not None:
            return _empty_plan(
                action=action,
                config_path=config_path,
                note=f"consumer {consumer_key} already exists; no source changes planned",
            )
        if display_name is None:
            display_name = consumer_key.replace("-", " ").title()
        new_consumer = ConsumerConfig(
            key=consumer_key,
            display_name=display_name,
            allowed_models=allowed_models,
            credential_placeholder="REPLACE_AT_RUNTIME",
            rate_limits=ConsumerRateLimits(requests_per_minute or 60),
        )
        candidate = GatewayConfig(
            platform=config.platform,
            models=config.models,
            consumers=(*config.consumers, new_consumer),
        )
        validate_config(candidate)
        changes.append(_consumers_file_change(config_path, candidate.consumers))
        if allowed_models:
            changes.append(
                _updated_ratelimit_change(
                    model_keys=allowed_models,
                    consumer_key=consumer_key,
                    config=candidate,
                    requests_per_minute=requests_per_minute or 60,
                )
            )
    elif action == "consumer rotate":
        if existing is None:
            msg = f"consumer {consumer_key} does not exist"
            raise PlanError(msg)
        rotated = _replace_consumer(
            config.consumers,
            existing,
            credential_placeholder=FAKE_ROTATED_PLACEHOLDER,
        )
        validate_config(
            GatewayConfig(platform=config.platform, models=config.models, consumers=rotated)
        )
        changes.append(_consumers_file_change(config_path, rotated))
    elif action == "consumer revoke":
        if existing is None:
            msg = f"consumer {consumer_key} does not exist"
            raise PlanError(msg)
        revoked = _replace_consumer(
            config.consumers,
            existing,
            allowed_models=(),
            credential_placeholder=REVOKED_PLACEHOLDER,
        )
        validate_config(
            GatewayConfig(platform=config.platform, models=config.models, consumers=revoked)
        )
        changes.append(_consumers_file_change(config_path, revoked))
    else:
        msg = f"unsupported consumer lifecycle action: {action}"
        raise PlanError(msg)

    return LifecyclePlan(
        schema_version=LIFECYCLE_PLAN_SCHEMA,
        plan_id="",
        action=action,
        config_path=str(config_path),
        changes=tuple(_dedupe_changes(changes)),
        notes=("Runtime credential values stay outside Git.",),
    ).with_computed_id()


def model_from_request(
    *,
    key: str,
    display_name: str,
    kind: str,
    host: str,
    route_path: str,
    permission: str,
    backend: str,
    service_name: str,
    service_namespace: str,
    service_port: int,
    service_port_name: str = "http",
) -> ModelConfig:
    """Build a model config from CLI request values."""

    return ModelConfig(
        key=key,
        display_name=display_name,
        kind=ModelKind(kind),
        host=host,
        route_path=route_path,
        permission=permission,
        backend=RouteBackend(backend),
        service=ServiceRef(
            name=service_name,
            namespace=service_namespace,
            ports=(ServicePort(name=service_port_name, number=service_port),),
            target_port_name=service_port_name,
        ),
    )


def _manifest_changes_for_model(model: ModelConfig) -> list[SourceChange]:
    changes = [
        SourceChange.from_content(
            Path("manifests/baseline-v1.3.1/bases/routes") / f"{model.key}.yaml",
            _dump_yaml(_route_manifest(model)),
        ),
        SourceChange.from_content(
            Path("manifests/baseline-v1.3.1/bases/policies") / f"{model.key}-policy.yaml",
            _dump_yaml(_policy_manifest(model)),
        ),
        _updated_kustomization(
            Path("manifests/baseline-v1.3.1/bases/routes/kustomization.yaml"),
            f"{model.key}.yaml",
        ),
        _updated_kustomization(
            Path("manifests/baseline-v1.3.1/bases/policies/kustomization.yaml"),
            f"{model.key}-policy.yaml",
        ),
        _appended_multidoc(
            Path("manifests/baseline-v1.3.1/bases/backends/model-service-contracts.yaml"),
            _service_manifest(model),
        ),
    ]
    if model.kind is ModelKind.CHAT:
        changes.append(
            _appended_multidoc(
                Path("manifests/baseline-v1.3.1/bases/backends/chat-agentgateway-backends.yaml"),
                _agentgateway_backend(model),
            )
        )
    return changes


def _updated_consumers_change(
    config_path: Path, *, model_key: str, consumer_key: str
) -> SourceChange:
    config = load_config(config_path)
    consumer = next((item for item in config.consumers if item.key == consumer_key), None)
    if consumer is None:
        msg = f"consumer {consumer_key} does not exist"
        raise PlanError(msg)
    consumers = _replace_consumer(
        config.consumers,
        consumer,
        allowed_models=tuple(sorted({*consumer.allowed_models, model_key})),
    )
    return _consumers_file_change(config_path, consumers)


def _updated_ratelimit_change(
    *,
    model_keys: tuple[str, ...],
    consumer_key: str,
    config: GatewayConfig,
    requests_per_minute: int | None = None,
) -> SourceChange:
    path = Path("manifests/baseline-v1.3.1/bases/ratelimit/configmaps.yaml")
    documents = _load_docs(path)
    configmap = next(
        (
            item
            for item in documents
            if item.get("kind") == "ConfigMap"
            and _metadata(item).get("name") == "envoy-ratelimit-config"
        ),
        None,
    )
    if configmap is None:
        msg = "envoy-ratelimit-config ConfigMap was not found"
        raise PlanError(msg)
    data = _mapping(configmap.get("data"), "ratelimit data")
    ratelimit_config = yaml.safe_load(str(data.get("config.yaml", "")))
    if not isinstance(ratelimit_config, dict):
        msg = "envoy-ratelimit config.yaml must be a mapping"
        raise PlanError(msg)
    descriptors = ratelimit_config.setdefault("descriptors", [])
    if not isinstance(descriptors, list):
        msg = "envoy-ratelimit descriptors must be a list"
        raise PlanError(msg)
    consumer_limit = requests_per_minute or _requests_per_minute(config, consumer_key)
    consumer_descriptor = next(
        (
            item
            for item in descriptors
            if isinstance(item, dict)
            and item.get("key") == "consumer_id"
            and item.get("value") == consumer_key
        ),
        None,
    )
    if consumer_descriptor is None:
        consumer_descriptor = {"key": "consumer_id", "value": consumer_key, "descriptors": []}
        descriptors.append(consumer_descriptor)
    nested = consumer_descriptor.setdefault("descriptors", [])
    if not isinstance(nested, list):
        msg = f"consumer_id descriptor for {consumer_key} must contain nested descriptors"
        raise PlanError(msg)
    for model_key in model_keys:
        if not any(isinstance(item, dict) and item.get("value") == model_key for item in nested):
            nested.append(
                {
                    "key": "model",
                    "value": model_key,
                    "rate_limit": {
                        "unit": "minute",
                        "requests_per_unit": consumer_limit,
                    },
                }
            )
    data["config.yaml"] = _LiteralString(_dump_yaml(ratelimit_config).removeprefix("---\n"))
    return SourceChange.from_content(path, _dump_yaml_all(documents))


def _consumers_file_change(
    config_path: Path, consumers: tuple[ConsumerConfig, ...]
) -> SourceChange:
    return SourceChange.from_content(
        config_path / "consumers.yaml",
        _dump_yaml({"consumers": [_consumer_dict(consumer) for consumer in consumers]}),
    )


def _replace_consumer(
    consumers: tuple[ConsumerConfig, ...],
    target: ConsumerConfig,
    *,
    allowed_models: tuple[str, ...] | None = None,
    credential_placeholder: str | None = None,
) -> tuple[ConsumerConfig, ...]:
    updated = ConsumerConfig(
        key=target.key,
        display_name=target.display_name,
        allowed_models=allowed_models if allowed_models is not None else target.allowed_models,
        credential_placeholder=credential_placeholder or target.credential_placeholder,
        rate_limits=target.rate_limits,
    )
    return tuple(updated if item.key == target.key else item for item in consumers)


def _route_manifest(model: ModelConfig) -> dict[str, object]:
    backend_ref: dict[str, object]
    if model.kind is ModelKind.CHAT:
        backend_ref = {
            "group": "agentgateway.dev",
            "kind": "AgentgatewayBackend",
            "name": f"{model.key}-backend",
            "namespace": model.service.namespace,
        }
    else:
        backend_ref = {
            "name": model.service.name,
            "namespace": model.service.namespace,
            "port": model.service.ports[0].number,
        }
    return {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "HTTPRoute",
        "metadata": _model_metadata(f"route-{model.key}", "model-route", model),
        "spec": {
            "hostnames": [model.host],
            "parentRefs": [{"name": "ai-gateway", "namespace": "ai-gateway"}],
            "rules": [
                {
                    "matches": [{"path": {"type": "Exact", "value": model.route_path}}],
                    "backendRefs": [backend_ref],
                }
            ],
        },
    }


def _policy_manifest(model: ModelConfig) -> dict[str, object]:
    return {
        "apiVersion": "agentgateway.dev/v1alpha1",
        "kind": "AgentgatewayPolicy",
        "metadata": {
            **_model_metadata(f"policy-{model.key}", "model-authorization-policy", model),
            "annotations": {"ai.gateway/consumer-id-expression": "apiKey.consumer_id"},
        },
        "spec": {
            "targetRefs": [
                {
                    "group": "gateway.networking.k8s.io",
                    "kind": "HTTPRoute",
                    "name": f"route-{model.key}",
                }
            ],
            "traffic": {
                "apiKeyAuthentication": {
                    "mode": "Strict",
                    "location": {"header": {"name": "x-api-key"}},
                    "secretRef": {"name": "agentgateway-consumer-keys"},
                },
                "authorization": {
                    "action": "Require",
                    "policy": {
                        "matchExpressions": [
                            f"apiKey.permissions.exists(permission, permission == '{model.permission}')"
                        ]
                    },
                },
                "rateLimit": {
                    "global": {
                        "backendRef": {
                            "kind": "Service",
                            "name": "envoy-ratelimit",
                            "namespace": "ai-gateway",
                            "port": 8081,
                        },
                        "failureMode": "FailClosed",
                        "domain": "ai-gateway",
                        "descriptors": [
                            {
                                "entries": [
                                    {"name": "consumer_id", "expression": "apiKey.consumer_id"},
                                    {"name": "model", "expression": f"'{model.key}'"},
                                ],
                                "unit": "Requests",
                            }
                        ],
                    }
                },
            },
        },
    }


def _service_manifest(model: ModelConfig) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            **_model_metadata(model.service.name, "nim-service-contract", model),
            "annotations": {
                "airgap.ai.gateway/owner-boundary": (
                    "service-contract-only-nim-workload-owned-outside-gateway"
                )
            },
        },
        "spec": {
            "selector": {
                "app.kubernetes.io/name": model.service.name,
                "app.kubernetes.io/component": "nim-runtime",
            },
            "ports": [
                {
                    "name": model.service.ports[0].name or "http",
                    "port": model.service.ports[0].number,
                    "targetPort": model.service.target_port_name or model.service.ports[0].number,
                    "protocol": "TCP",
                }
            ],
        },
    }


def _agentgateway_backend(model: ModelConfig) -> dict[str, object]:
    return {
        "apiVersion": "agentgateway.dev/v1alpha1",
        "kind": "AgentgatewayBackend",
        "metadata": _model_metadata(f"{model.key}-backend", "chat-backend", model),
        "spec": {
            "ai": {
                "provider": {
                    "openai": {},
                    "host": f"{model.service.name}.{model.service.namespace}.svc.cluster.local",
                    "port": model.service.ports[0].number,
                }
            }
        },
    }


def _model_metadata(name: str, component: str, model: ModelConfig) -> dict[str, object]:
    return {
        "name": name,
        "namespace": "ai-gateway",
        "labels": {
            "app.kubernetes.io/name": name,
            "app.kubernetes.io/component": component,
            "app.kubernetes.io/part-of": "ai-gateway",
            "ai.gateway/model-key": model.key,
            "ai.gateway/api-shape": model.kind.value,
        },
    }


def _updated_kustomization(path: Path, resource: str) -> SourceChange:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    item = _mapping(raw, str(path))
    resources = item.setdefault("resources", [])
    if not isinstance(resources, list):
        msg = f"{path} resources must be a list"
        raise PlanError(msg)
    if resource not in resources:
        resources.append(resource)
    return SourceChange.from_content(path, _dump_yaml(item))


def _appended_multidoc(path: Path, manifest: dict[str, object]) -> SourceChange:
    documents = _load_docs(path)
    metadata = _metadata(manifest)
    if not any(_metadata(item).get("name") == metadata.get("name") for item in documents):
        documents.append(manifest)
    return SourceChange.from_content(path, _dump_yaml_all(documents))


def _load_docs(path: Path) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], item)
        for item in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(item, dict)
    ]


def _model_dict(model: ModelConfig) -> dict[str, object]:
    return {
        "backend": model.backend.value,
        "display_name": model.display_name,
        "host": model.host,
        "key": model.key,
        "kind": model.kind.value,
        "permission": model.permission,
        "route_path": model.route_path,
        "service": {
            "name": model.service.name,
            "namespace": model.service.namespace,
            "ports": [{"name": port.name, "number": port.number} for port in model.service.ports],
            "target_port_name": model.service.target_port_name,
        },
    }


def _consumer_dict(consumer: ConsumerConfig) -> dict[str, object]:
    payload: dict[str, object] = {
        "allowed_models": list(consumer.allowed_models),
        "credential_placeholder": consumer.credential_placeholder,
        "display_name": consumer.display_name,
        "key": consumer.key,
    }
    if consumer.rate_limits is not None:
        payload["rate_limits"] = {
            "requests_per_minute": consumer.rate_limits.requests_per_minute,
        }
        if consumer.rate_limits.tokens_per_minute is not None:
            cast(dict[str, object], payload["rate_limits"])["tokens_per_minute"] = (
                consumer.rate_limits.tokens_per_minute
            )
    return payload


def _requests_per_minute(config: GatewayConfig, consumer_key: str) -> int:
    consumer = next((item for item in config.consumers if item.key == consumer_key), None)
    if consumer is None or consumer.rate_limits is None:
        return 60
    return consumer.rate_limits.requests_per_minute


def _empty_plan(*, action: str, config_path: Path, note: str) -> LifecyclePlan:
    return LifecyclePlan(
        schema_version=LIFECYCLE_PLAN_SCHEMA,
        plan_id="",
        action=action,
        config_path=str(config_path),
        changes=(),
        notes=(note,),
    ).with_computed_id()


def _dedupe_changes(changes: list[SourceChange]) -> list[SourceChange]:
    deduped: dict[str, SourceChange] = {}
    for change in changes:
        deduped[change.path] = change
    return [deduped[key] for key in sorted(deduped)]


def _safe_repo_path(repo_root: Path, relative: str) -> Path:
    path = (repo_root / relative).resolve()
    root = repo_root.resolve()
    if root not in path.parents and path != root:
        msg = f"planned path escapes repository: {relative}"
        raise SafetyError(msg)
    return path


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{label} must be a mapping"
        raise PlanError(msg)
    return cast(dict[str, object], value)


def _metadata(value: dict[str, object]) -> dict[str, object]:
    return _mapping(value.get("metadata"), "metadata")


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        msg = f"{key} must be a non-empty string"
        raise PlanError(msg)
    return value


def _optional_string(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        msg = f"{key} must be a non-empty string"
        raise PlanError(msg)
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dump_yaml(value: object) -> str:
    return "---\n" + yaml.dump(value, Dumper=_LifecycleDumper, sort_keys=False)


def _dump_yaml_all(values: list[dict[str, object]]) -> str:
    return yaml.dump_all(values, Dumper=_LifecycleDumper, explicit_start=True, sort_keys=False)
