"""Argo CD GitOps source rendering, validation, and planning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from airgap_ai_gateway.errors import PlanError
from airgap_ai_gateway.ledger import ResourceRef
from airgap_ai_gateway.manifest import (
    Manifest,
    build_kustomization,
    dump_documents,
    load_image_map,
    validate_documents,
)
from airgap_ai_gateway.models import GatewayConfig
from airgap_ai_gateway.planning import (
    APPLY_MODES,
    PLAN_SCHEMA_VERSION,
    ExecutionPlan,
    PlanAction,
)

GITOPS_ENVIRONMENTS = ("kind-demo", "retained-nginx-edge", "production-reference")
GITOPS_ROOT = Path("gitops/argocd")
ARGOCD_NAMESPACE = "argocd"
GATEWAY_NAMESPACE = "ai-gateway"
REPOSITORY_URL = "https://github.com/ahmed658/airgapped-ai-gateway-k8s.git"
TARGET_REVISION = "main"
PROJECT_NAME = "airgap-ai-gateway"
REQUIRED_SYNC_OPTIONS = frozenset(
    {
        "CreateNamespace=true",
        "ServerSideApply=true",
        "ApplyOutOfSyncOnly=true",
        "PruneLast=true",
        "FailOnSharedResource=true",
        "RespectIgnoreDifferences=true",
    }
)


@dataclass(frozen=True, slots=True)
class GitOpsRenderResult:
    """Rendered Argo CD bootstrap and managed overlay documents."""

    environment: str
    bootstrap: tuple[Manifest, ...]
    managed_overlay: tuple[Manifest, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a compact JSON-friendly summary."""

        return {
            "bootstrap_resources": [_identity(document) for document in self.bootstrap],
            "environment": self.environment,
            "managed_resources": [_identity(document) for document in self.managed_overlay],
            "status": "rendered",
        }


def bootstrap_path(environment: str) -> Path:
    """Return the Argo CD bootstrap Kustomize path for an environment."""

    _validate_environment(environment)
    return GITOPS_ROOT / "bootstrap" / environment


def managed_overlay_path(environment: str) -> Path:
    """Return the Argo CD managed overlay path for an environment."""

    _validate_environment(environment)
    return GITOPS_ROOT / "managed-overlays" / environment


def render_gitops(environment: str) -> GitOpsRenderResult:
    """Render GitOps bootstrap and managed resources without cluster access."""

    return GitOpsRenderResult(
        environment=environment,
        bootstrap=tuple(build_kustomization(bootstrap_path(environment))),
        managed_overlay=tuple(build_kustomization(managed_overlay_path(environment))),
    )


def write_gitops_render(environment: str, output_dir: Path) -> tuple[Path, Path]:
    """Write rendered GitOps bootstrap and managed overlay YAML."""

    rendered = render_gitops(environment)
    output_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_file = output_dir / f"{environment}-argocd-bootstrap.yaml"
    managed_file = output_dir / f"{environment}-managed-overlay.yaml"
    bootstrap_file.write_text(dump_documents(list(rendered.bootstrap)), encoding="utf-8")
    managed_file.write_text(dump_documents(list(rendered.managed_overlay)), encoding="utf-8")
    return bootstrap_file, managed_file


def validate_gitops(environment: str) -> list[str]:
    """Return GitOps validation errors for one environment."""

    rendered = render_gitops(environment)
    errors: list[str] = []
    errors.extend(_validate_bootstrap(rendered.bootstrap, environment))
    errors.extend(
        f"managed overlay: {error}"
        for error in validate_documents(
            list(rendered.managed_overlay),
            overlay=environment,
            image_map=load_image_map(),
        )
    )
    errors.extend(_validate_prune_guards(rendered.managed_overlay))
    return errors


def build_gitops_execution_plan(
    config: GatewayConfig,
    *,
    environment: str,
    apply_mode: str = "server-side-dry-run",
) -> ExecutionPlan:
    """Build a deterministic Argo CD bootstrap plan without contacting Kubernetes."""

    _validate_environment(environment)
    if apply_mode not in APPLY_MODES:
        msg = (
            f"unsupported apply mode {apply_mode}; expected one of {', '.join(sorted(APPLY_MODES))}"
        )
        raise PlanError(msg)
    errors = validate_gitops(environment)
    if errors:
        msg = "gitops plan refused: " + "; ".join(errors)
        raise PlanError(msg)

    resources = tuple(
        sorted(ResourceRef.from_manifest(item) for item in render_gitops(environment).bootstrap)
    )
    actions = (
        PlanAction(
            id="verify-context",
            kind="verify-context",
            description="Verify the exact Kubernetes context before bootstrapping Argo CD.",
            command=("kubectl", "config", "current-context"),
        ),
        PlanAction(
            id="kubectl-diff-gitops-bootstrap",
            kind="kubectl-diff",
            description=f"Compare Argo CD bootstrap resources for {environment}.",
            command=("kubectl", "diff", "-k", str(bootstrap_path(environment))),
        ),
        PlanAction(
            id="kubectl-apply-gitops-bootstrap",
            kind="kubectl-apply",
            description=f"Apply Argo CD AppProject and Application for {environment}.",
            command=_apply_command(environment, apply_mode),
            mutating=True,
        ),
    )

    return ExecutionPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        plan_id="",
        command="gitops apply",
        platform=config.platform.name,
        baseline=config.platform.baseline.agentgateway_version,
        overlay=f"gitops/{environment}",
        namespace=ARGOCD_NAMESPACE,
        apply_mode=apply_mode,
        expected_context=config.platform.cluster.expected_context,
        tls_verify=True,
        actions=actions,
        resources=resources,
        notes=(
            "Argo CD is expected to exist before this bootstrap is applied.",
            "Runtime credential values are not part of the GitOps source.",
            "The Application reconciles only the selected managed overlay path.",
        ),
    ).with_computed_id()


def _apply_command(environment: str, apply_mode: str) -> tuple[str, ...]:
    base = ("kubectl", "apply", "--server-side")
    if apply_mode == "server-side-dry-run":
        return (*base, "--dry-run=server", "-k", str(bootstrap_path(environment)))
    return (*base, "-k", str(bootstrap_path(environment)))


def _validate_environment(environment: str) -> None:
    if environment not in GITOPS_ENVIRONMENTS:
        msg = (
            f"unknown GitOps environment {environment}; "
            f"expected one of {', '.join(GITOPS_ENVIRONMENTS)}"
        )
        raise PlanError(msg)


def _validate_bootstrap(documents: tuple[Manifest, ...], environment: str) -> list[str]:
    errors: list[str] = []
    projects = [item for item in documents if item.get("kind") == "AppProject"]
    applications = [item for item in documents if item.get("kind") == "Application"]
    if len(projects) != 1:
        errors.append(f"{environment}: expected exactly one AppProject, got {len(projects)}")
    else:
        errors.extend(_validate_project(projects[0]))
    if len(applications) != 1:
        errors.append(f"{environment}: expected exactly one Application, got {len(applications)}")
    else:
        errors.extend(_validate_application(applications[0], environment))
    for document in documents:
        if document.get("kind") == "Secret":
            errors.append(f"{environment}: GitOps bootstrap must not render Secret resources")
    return errors


def _validate_project(project: Manifest) -> list[str]:
    errors: list[str] = []
    metadata = _mapping(project.get("metadata"), "AppProject.metadata")
    if metadata.get("name") != PROJECT_NAME:
        errors.append("AppProject name must be airgap-ai-gateway")
    if metadata.get("namespace") != ARGOCD_NAMESPACE:
        errors.append("AppProject must live in the argocd namespace")
    spec = _mapping(project.get("spec"), "AppProject.spec")
    if spec.get("sourceRepos") != [REPOSITORY_URL]:
        errors.append("AppProject sourceRepos must be pinned to the project repository")
    if spec.get("destinations") != [
        {"server": "https://kubernetes.default.svc", "namespace": GATEWAY_NAMESPACE}
    ]:
        errors.append("AppProject destination must be limited to the ai-gateway namespace")
    for item in _sequence(spec.get("namespaceResourceWhitelist"), "namespaceResourceWhitelist"):
        entry = _mapping(item, "namespaceResourceWhitelist[]")
        if entry.get("kind") == "Secret":
            errors.append("AppProject must not whitelist Secret resources")
        if entry.get("group") == "*" or entry.get("kind") == "*":
            errors.append("AppProject namespace whitelist must not use wildcards")
    for item in _sequence(spec.get("clusterResourceWhitelist"), "clusterResourceWhitelist"):
        entry = _mapping(item, "clusterResourceWhitelist[]")
        if entry.get("group") == "*" or entry.get("kind") == "*":
            errors.append("AppProject cluster whitelist must not use wildcards")
    return errors


def _validate_application(application: Manifest, environment: str) -> list[str]:
    errors: list[str] = []
    metadata = _mapping(application.get("metadata"), "Application.metadata")
    if metadata.get("namespace") != ARGOCD_NAMESPACE:
        errors.append(f"{environment}: Application must live in the argocd namespace")
    if metadata.get("finalizers"):
        errors.append(f"{environment}: Application must not enable cascading resource deletion")
    spec = _mapping(application.get("spec"), "Application.spec")
    if spec.get("project") != PROJECT_NAME:
        errors.append(f"{environment}: Application must use the airgap-ai-gateway project")
    source = _mapping(spec.get("source"), "Application.spec.source")
    if source.get("repoURL") != REPOSITORY_URL:
        errors.append(f"{environment}: Application repoURL must be the project repository")
    if source.get("targetRevision") != TARGET_REVISION:
        errors.append(f"{environment}: Application targetRevision must be main")
    expected_path = f"gitops/argocd/managed-overlays/{environment}"
    if source.get("path") != expected_path:
        errors.append(f"{environment}: Application source path must be {expected_path}")
    destination = _mapping(spec.get("destination"), "Application.spec.destination")
    if destination.get("server") != "https://kubernetes.default.svc":
        errors.append(f"{environment}: Application destination server must be in-cluster")
    if destination.get("namespace") != GATEWAY_NAMESPACE:
        errors.append(f"{environment}: Application destination namespace must be ai-gateway")
    sync_policy = _mapping(spec.get("syncPolicy"), "Application.spec.syncPolicy")
    automated = _mapping(sync_policy.get("automated"), "Application.spec.syncPolicy.automated")
    if automated.get("prune") is not True:
        errors.append(f"{environment}: automated prune must be enabled")
    if automated.get("selfHeal") is not True:
        errors.append(f"{environment}: automated selfHeal must be enabled")
    if automated.get("allowEmpty") is not False:
        errors.append(f"{environment}: automated allowEmpty must be false")
    sync_options = set(_sequence(sync_policy.get("syncOptions"), "syncOptions"))
    missing_options = sorted(REQUIRED_SYNC_OPTIONS - sync_options)
    if missing_options:
        errors.append(f"{environment}: missing sync options: {', '.join(missing_options)}")
    return errors


def _validate_prune_guards(documents: tuple[Manifest, ...]) -> list[str]:
    errors: list[str] = []
    guarded = 0
    for document in documents:
        if document.get("kind") != "Service":
            continue
        metadata = _mapping(document.get("metadata"), "Service.metadata")
        labels = _mapping(metadata.get("labels"), "Service.metadata.labels")
        if labels.get("app.kubernetes.io/component") != "nim-service-contract":
            continue
        guarded += 1
        annotations = _mapping(metadata.get("annotations"), "Service.metadata.annotations")
        if "Prune=false" not in str(annotations.get("argocd.argoproj.io/sync-options", "")):
            errors.append(f"Service {metadata.get('name')} must disable Argo CD pruning")
    if guarded != 3:
        errors.append(f"expected prune guards on 3 model Service contracts, got {guarded}")
    return errors


def _identity(document: Manifest) -> str:
    metadata = _mapping(document.get("metadata"), "metadata")
    namespace = metadata.get("namespace") or "_cluster"
    return f"{document.get('apiVersion')}/{document.get('kind')}/{namespace}/{metadata.get('name')}"


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"{field} must be a mapping"
        raise PlanError(msg)
    return value


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        msg = f"{field} must be a list"
        raise PlanError(msg)
    return value
