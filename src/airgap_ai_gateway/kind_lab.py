"""Disposable kind end-to-end lab orchestration."""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast
from xml.sax.saxutils import escape

import yaml

from airgap_ai_gateway.airgap_bundle import DEFAULT_LOCK_PATH, load_source_lock
from airgap_ai_gateway.errors import AirgapGatewayError
from airgap_ai_gateway.manifest import build_kustomization, dump_documents
from airgap_ai_gateway.verification import verify_embedding_response

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_OVERLAY = REPO_ROOT / "manifests/baseline-v1.3.1/overlays/kind-e2e-lab"
BROKEN_BACKEND_MANIFEST = REPO_ROOT / "lab/fixtures/kubernetes/broken-backend.yaml"
NGINX_EDGE_MANIFEST = REPO_ROOT / "lab/fixtures/kubernetes/retained-nginx-edge.yaml"
MOCK_IMAGE_CONTEXT = REPO_ROOT / "lab/mocks"
RUNS_DIR = REPO_ROOT / "runs"
NAMESPACE = "ai-gateway"
PLACEHOLDER_REGISTRY = "registry.example.internal:5000"
CLUSTER_PREFIX = "agw-e2e-"
REGISTRY_PREFIX = "agw-e2e-registry-"
KIND_NODE_IMAGE = (
    "kindest/node@sha256:7fbc5644a803286a69ff9c5695f03bb01b512896835e15df7df17f756f7245ac"
)
REGISTRY_IMAGE = "registry@sha256:46faa9a1ae6813194b53921a370f2f4f8c5e1aae228a89bceafef5847a6a3278"
MOCK_IMAGE_REPOSITORY = "airgap-ai-gateway/openai-mock"
MOCK_IMAGE_TAG = "e2e-lab"
PUBLIC_REGISTRY_MARKERS = (
    "cr.agentgateway.dev/",
    "docker.io/",
    "ghcr.io/",
    "quay.io/",
    "registry.k8s.io/",
)
LAB_IMAGE_TAGS = {
    "agentgateway": "agentgateway/agentgateway:v1.3.1",
    "agentgateway-controller": "agentgateway/controller:v1.3.1",
    "envoy-ratelimit": "envoyproxy/ratelimit:837de552",
    "redis": "library/redis:7.2.5-alpine",
    "fixture-nginx-edge": "library/nginx:1.27.5-alpine",
}


class CommandRunner(Protocol):
    """Small command runner protocol for testable lab orchestration."""

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command."""


class SubprocessRunner:  # pragma: no cover
    """Subprocess-backed command runner."""

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a subprocess with text capture."""

        completed = subprocess.run(
            list(args),
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        if check and completed.returncode != 0:
            msg = (
                f"command failed ({completed.returncode}): {' '.join(args)}\n"
                f"{completed.stderr.strip()}"
            )
            raise AirgapGatewayError(msg)
        return completed


@dataclass(frozen=True, slots=True)
class LabNames:
    """Unique names for one disposable lab run."""

    run_id: str
    cluster: str
    context: str
    registry_container: str
    registry: str
    registry_port: int
    run_dir: Path


@dataclass(slots=True)
class LabResult:
    """One behavioral assertion result."""

    name: str
    status: str
    detail: str = ""
    duration_seconds: float = 0.0


@dataclass(slots=True)
class LabEvidence:
    """Evidence collected during one lab run."""

    names: LabNames
    results: list[LabResult] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    image_audit: dict[str, object] = field(default_factory=dict)

    def add(self, name: str, passed: bool, detail: str = "", duration: float = 0.0) -> None:
        """Append one assertion result."""

        self.results.append(
            LabResult(
                name=name,
                status="passed" if passed else "failed",
                detail=detail,
                duration_seconds=round(duration, 3),
            )
        )

    @property
    def failed(self) -> bool:
        """Return whether any assertion failed."""

        return any(result.status != "passed" for result in self.results)


def new_lab_names(*, run_id: str | None = None, registry_port: int | None = None) -> LabNames:
    """Create unique disposable names for a kind lab run."""

    token = run_id or f"{int(time.time())}-{secrets.token_hex(3)}"
    safe_token = re.sub(r"[^a-z0-9-]", "-", token.lower()).strip("-")
    cluster = f"{CLUSTER_PREFIX}{safe_token}"
    validate_disposable_cluster_name(cluster)
    port = registry_port or find_free_port()
    return LabNames(
        run_id=safe_token,
        cluster=cluster,
        context=f"kind-{cluster}",
        registry_container=f"{REGISTRY_PREFIX}{safe_token}",
        registry=f"localhost:{port}",
        registry_port=port,
        run_dir=RUNS_DIR / f"kind-e2e-{safe_token}",
    )


def validate_disposable_cluster_name(name: str) -> None:
    """Refuse teardown or mutation targets that are not lab-owned."""

    if not re.fullmatch(r"agw-e2e-[a-z0-9][a-z0-9-]{5,60}", name):
        msg = f"refusing non-disposable kind cluster name: {name}"
        raise AirgapGatewayError(msg)


def find_free_port() -> int:
    """Find a loopback TCP port for the local registry."""

    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class KubectlGuard:
    """Context-verifying kubectl wrapper."""

    def __init__(self, runner: CommandRunner, expected_context: str, env: dict[str, str]) -> None:
        self.runner = runner
        self.expected_context = expected_context
        self.env = env

    def run(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Verify context, then run kubectl with an exact context."""

        self.verify_context()
        command = ["kubectl", "--context", self.expected_context, *args]
        print(f"kubectl context verified: {self.expected_context}", file=sys.stderr)
        print(f"kubectl command: {' '.join(command)}", file=sys.stderr)
        return self.runner.run(
            command,
            input_text=input_text,
            check=check,
            timeout_seconds=timeout_seconds,
        )

    def verify_context(self) -> None:
        """Read and verify the current context before a kubectl operation."""

        print(
            f"verifying kubectl context before operation: expected {self.expected_context}",
            file=sys.stderr,
        )
        completed = self.runner.run(
            ["kubectl", "config", "current-context"],
            check=True,
            timeout_seconds=30,
        )
        current = completed.stdout.strip()
        if current != self.expected_context:
            msg = f"refusing kubectl operation: current context {current!r} != {self.expected_context!r}"
            raise AirgapGatewayError(msg)


def validate_lab_documents(documents: list[dict[str, Any]], *, registry: str) -> list[str]:
    """Validate the static e2e lab contract."""

    errors: list[str] = []
    routes = {
        _metadata(document)["name"] for document in documents if document.get("kind") == "HTTPRoute"
    }
    protected_routes: set[str] = set()
    for document in documents:
        if document.get("kind") != "AgentgatewayPolicy":
            continue
        for target_ref in _as_list(document.get("spec", {}).get("targetRefs", [])):
            if target_ref.get("kind") == "HTTPRoute":
                protected_routes.add(str(target_ref.get("name")))
    for route in sorted(routes - protected_routes):
        errors.append(f"HTTPRoute {route} has no AgentgatewayPolicy")

    for image in rendered_images(documents):
        if not image.startswith(f"{registry}/"):
            errors.append(f"image does not use the lab registry {registry}: {image}")
        for marker in PUBLIC_REGISTRY_MARKERS:
            if marker in image:
                errors.append(f"image points at public registry marker {marker}: {image}")
    return errors


def rendered_images(documents: list[dict[str, Any]]) -> list[str]:
    """Collect runtime image references from rendered documents."""

    images: list[str] = []
    for document in documents:
        if document.get("kind") == "AgentgatewayParameters":
            image = document.get("spec", {}).get("image", {})
            if isinstance(image, dict):
                registry = image.get("registry")
                repository = image.get("repository")
                digest = image.get("digest")
                if all(isinstance(value, str) for value in (registry, repository, digest)):
                    images.append(f"{registry}/{repository}@{digest}")
        if document.get("kind") != "Deployment":
            continue
        pod_spec = document.get("spec", {}).get("template", {}).get("spec", {})
        for container in _as_list(pod_spec.get("containers", [])):
            image = container.get("image")
            if isinstance(image, str):
                images.append(image)
    return sorted(images)


def render_lab_documents(
    *,
    local_registry: str,
    digest_overrides: dict[str, str] | None = None,
    include_nginx: bool = False,
) -> list[dict[str, Any]]:
    """Render the lab overlay and rewrite runtime images to the local registry."""

    documents = build_kustomization(LAB_OVERLAY)
    if include_nginx:
        documents.extend(_load_yaml_documents(NGINX_EDGE_MANIFEST))
    return rewrite_documents_for_local_registry(
        documents,
        local_registry=local_registry,
        digest_overrides=digest_overrides or {},
    )


def rewrite_documents_for_local_registry(
    documents: list[dict[str, Any]],
    *,
    local_registry: str,
    digest_overrides: dict[str, str],
) -> list[dict[str, Any]]:
    """Rewrite placeholder image references for one unique local registry."""

    rendered = yaml.safe_load_all(dump_documents(documents))
    rewritten: list[dict[str, Any]] = []
    for item in rendered:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "AgentgatewayParameters":
            image = item.setdefault("spec", {}).setdefault("image", {})
            image["registry"] = local_registry
            image["digest"] = digest_overrides.get("agentgateway", image.get("digest"))
        if item.get("kind") == "Deployment":
            pod_spec = item.get("spec", {}).get("template", {}).get("spec", {})
            for container in _as_list(pod_spec.get("containers", [])):
                image = container.get("image")
                if isinstance(image, str):
                    container["image"] = _rewrite_image(image, local_registry, digest_overrides)
        rewritten.append(cast(dict[str, Any], item))
    return rewritten


def runtime_secret_manifest(credentials: dict[str, str]) -> dict[str, Any]:
    """Return runtime-only fake test credentials as a Secret manifest."""

    payloads = {
        "internal-chat": {
            "key": credentials["internal-chat"],
            "metadata": {
                "consumer_id": "internal-chat",
                "environment": "lab",
                "tier": "standard",
                "permissions": ["allow_qwen_chat", "allow_gemma_chat"],
            },
        },
        "rag-indexer": {
            "key": credentials["rag-indexer"],
            "metadata": {
                "consumer_id": "rag-indexer",
                "environment": "lab",
                "tier": "standard",
                "permissions": ["allow_embedding_index"],
            },
        },
        "low-limit": {
            "key": credentials["low-limit"],
            "metadata": {
                "consumer_id": "low-limit",
                "environment": "lab",
                "tier": "low-limit",
                "permissions": ["allow_gemma_chat"],
            },
        },
    }
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": "agentgateway-consumer-keys",
            "namespace": NAMESPACE,
            "labels": {"ai.gateway.runtime-secret": "consumer-api-keys"},
        },
        "type": "Opaque",
        "data": {
            name: base64.b64encode(json.dumps(value, sort_keys=True).encode()).decode()
            for name, value in payloads.items()
        },
    }


def fake_credentials() -> dict[str, str]:
    """Return unmistakably fake runtime test keys."""

    return {
        "internal-chat": "example-only-do-not-use-internal-chat",
        "rag-indexer": "example-only-do-not-use-rag-indexer",
        "low-limit": "example-only-do-not-use-low-limit",
        "unknown": "example-only-do-not-use-unknown",
    }


class KindE2ELab:  # pragma: no cover
    """Disposable kind lab workflow."""

    def __init__(
        self,
        *,
        names: LabNames,
        with_nginx: bool = False,
        keep: bool = False,
        runner: CommandRunner | None = None,
    ) -> None:
        self.names = names
        self.with_nginx = with_nginx
        self.keep = keep
        self.runner = runner or SubprocessRunner()
        self.env = os.environ.copy()
        self.kubectl = KubectlGuard(self.runner, names.context, self.env)
        self.evidence = LabEvidence(names)

    def run(self) -> LabEvidence:
        """Run the complete disposable lab."""

        self.names.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.preflight()
            self.start_registry()
            self.create_cluster()
            image_digests = self.prepare_images()
            self.prepare_artifacts()
            self.airgap_preinstall_gate(image_digests)
            self.install_gateway_stack()
            self.deploy_lab(image_digests)
            self.apply_runtime_credentials()
            self.wait_for_readiness()
            self.airgap_runtime_gate()
            self.run_behavior_matrix(target="internal-gateway")
            if self.evidence.failed:
                self.collect_failure_diagnostics()
            self.run_broken_backend_test()
            if self.with_nginx:
                self.deploy_retained_nginx(image_digests)
                self.run_behavior_matrix(target="retained-nginx-edge")
            self.assert_model_services_survive_gateway_cleanup()
            if self.evidence.failed:
                self.collect_failure_diagnostics()
        except Exception as exc:
            self.collect_failure_diagnostics()
            self.evidence.add("lab workflow failed", False, detail=str(exc)[:1000])
            raise
        finally:
            self.write_evidence()
            if not self.keep:
                self.teardown()
        return self.evidence

    def preflight(self) -> None:
        """Verify local commands are present."""

        for binary in ("docker", "helm", "kind", "kubectl"):
            if shutil.which(binary) is None:
                msg = f"required command not found: {binary}"
                raise AirgapGatewayError(msg)
        validate_disposable_cluster_name(self.names.cluster)

    def start_registry(self) -> None:
        """Start a unique local registry container."""

        self.runner.run(["docker", "pull", REGISTRY_IMAGE], timeout_seconds=180)
        self.runner.run(
            [
                "docker",
                "run",
                "-d",
                "-p",
                f"127.0.0.1:{self.names.registry_port}:5000",
                "--restart=always",
                "--name",
                self.names.registry_container,
                REGISTRY_IMAGE,
            ],
            timeout_seconds=60,
        )

    def create_cluster(self) -> None:
        """Create a unique kind cluster with a local registry mirror."""

        config = {
            "kind": "Cluster",
            "apiVersion": "kind.x-k8s.io/v1alpha4",
            "containerdConfigPatches": [
                (
                    '[plugins."io.containerd.grpc.v1.cri".registry.mirrors.'
                    f'"{self.names.registry}"]\n'
                    f'  endpoint = ["http://{self.names.registry_container}:5000"]\n'
                )
            ],
            "nodes": [{"role": "control-plane"}],
        }
        config_path = self.names.run_dir / "kind-config.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        self.runner.run(
            [
                "kind",
                "create",
                "cluster",
                "--name",
                self.names.cluster,
                "--image",
                KIND_NODE_IMAGE,
                "--config",
                str(config_path),
            ],
            timeout_seconds=300,
        )
        self.runner.run(
            ["docker", "network", "connect", "kind", self.names.registry_container],
            check=False,
            timeout_seconds=30,
        )

    def prepare_images(self) -> dict[str, str]:
        """Build and promote every runtime image into the local registry."""

        lock = load_source_lock(DEFAULT_LOCK_PATH)
        entries = {entry.name: entry for entry in lock.entries_for("baseline-v1.3.1")}
        digests: dict[str, str] = {}
        for name, local_path in LAB_IMAGE_TAGS.items():
            if name == "fixture-nginx-edge" and not self.with_nginx:
                continue
            entry = entries[name]
            local_ref = f"{self.names.registry}/{local_path}"
            self.runner.run(["docker", "pull", entry.canonical_source], timeout_seconds=300)
            self.runner.run(["docker", "tag", entry.canonical_source, local_ref])
            self.runner.run(["docker", "push", local_ref], timeout_seconds=300)
            digests[name] = (
                registry_digest(self.names.registry, local_path) or entry.oci_digest or ""
            )

        mock_ref = f"{self.names.registry}/{MOCK_IMAGE_REPOSITORY}:{MOCK_IMAGE_TAG}"
        self.runner.run(
            [
                "docker",
                "build",
                "--pull",
                "-t",
                mock_ref,
                str(MOCK_IMAGE_CONTEXT),
            ],
            timeout_seconds=300,
        )
        self.runner.run(["docker", "push", mock_ref], timeout_seconds=300)
        digests["openai-mock"] = (
            registry_digest(self.names.registry, f"{MOCK_IMAGE_REPOSITORY}:{MOCK_IMAGE_TAG}") or ""
        )
        return digests

    def prepare_artifacts(self) -> None:
        """Fetch chart and CRD artifacts into the ignored run directory."""

        artifacts = self.names.run_dir / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        lock = load_source_lock(DEFAULT_LOCK_PATH)
        entries = {entry.name: entry for entry in lock.entries_for("baseline-v1.3.1")}
        gateway_api = entries["gateway-api-crds"]
        gateway_api_path = artifacts / "gateway-api-experimental-install.yaml"
        urllib.request.urlretrieve(gateway_api.canonical_source, gateway_api_path)
        if sha256_file(gateway_api_path) != gateway_api.sha256:
            msg = "Gateway API CRD checksum mismatch"
            raise AirgapGatewayError(msg)
        for name in ("agentgateway-crds-chart", "agentgateway-controller-chart"):
            entry = entries[name]
            chart = entry.canonical_source.rsplit(":", 1)[0]
            self.runner.run(
                [
                    "helm",
                    "pull",
                    chart,
                    "--version",
                    entry.version,
                    "--destination",
                    str(artifacts),
                ],
                timeout_seconds=180,
            )
            chart_path = artifacts / entry.destination_name.split("/")[-1]
            if sha256_file(chart_path) != entry.sha256:
                msg = f"chart checksum mismatch: {name}"
                raise AirgapGatewayError(msg)

    def airgap_preinstall_gate(self, image_digests: dict[str, str]) -> None:
        """Prove all rendered runtime images point at the local registry."""

        documents = render_lab_documents(
            local_registry=self.names.registry,
            digest_overrides=_digest_overrides(image_digests),
            include_nginx=self.with_nginx,
        )
        errors = validate_lab_documents(documents, registry=self.names.registry)
        if errors:
            raise AirgapGatewayError("\n".join(errors))
        gate = {
            "registry": self.names.registry,
            "renderedImages": rendered_images(documents),
            "status": "passed",
        }
        gate_path = self.names.run_dir / "evidence" / "airgap-preinstall-gate.json"
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        gate_path.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.evidence.artifacts["airgap_preinstall_gate"] = str(gate_path)

    def install_gateway_stack(self) -> None:
        """Install Gateway API and agentgateway from prepared artifacts."""

        artifacts = self.names.run_dir / "artifacts"
        self.kubectl.run(
            ["create", "-f", str(artifacts / "gateway-api-experimental-install.yaml")],
            timeout_seconds=120,
        )
        self.runner.run(
            [
                "helm",
                "--kube-context",
                self.names.context,
                "upgrade",
                "--install",
                "agentgateway-crds",
                str(artifacts / "agentgateway-crds-v1.3.1.tgz"),
                "--namespace",
                "agentgateway-system",
                "--create-namespace",
            ],
            timeout_seconds=180,
        )
        self.runner.run(
            [
                "helm",
                "--kube-context",
                self.names.context,
                "upgrade",
                "--install",
                "agentgateway",
                str(artifacts / "agentgateway-v1.3.1.tgz"),
                "--namespace",
                "agentgateway-system",
                "--create-namespace",
                "--set",
                f"image.registry={self.names.registry}",
                "--set",
                "image.pullPolicy=IfNotPresent",
                "--set",
                f"controller.image.registry={self.names.registry}",
                "--set",
                "controller.image.repository=agentgateway/controller",
                "--set",
                "controller.image.tag=v1.3.1",
                "--set",
                f"proxy.image.registry={self.names.registry}",
                "--set",
                "proxy.image.repository=agentgateway/agentgateway",
                "--set",
                "proxy.image.tag=v1.3.1",
            ],
            timeout_seconds=240,
        )

    def deploy_lab(self, image_digests: dict[str, str]) -> None:
        """Render and apply the lab overlay."""

        documents = render_lab_documents(
            local_registry=self.names.registry,
            digest_overrides=_digest_overrides(image_digests),
        )
        rendered = dump_documents(documents)
        path = self.names.run_dir / "rendered" / "kind-e2e-lab.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        self.kubectl.run(["apply", "-f", str(path)], timeout_seconds=180)

    def apply_runtime_credentials(self) -> None:
        """Create runtime-only fake credentials under the ignored run directory."""

        secret = runtime_secret_manifest(fake_credentials())
        path = self.names.run_dir / "runtime" / "consumer-keys.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(secret, sort_keys=False), encoding="utf-8")
        self.kubectl.run(["apply", "-f", str(path)], timeout_seconds=60)

    def wait_for_readiness(self) -> None:
        """Wait for controller-owned and repository-owned lab workloads."""

        for deployment in ("redis", "qwen-nim", "gemma-nim", "embedding-nim", "envoy-ratelimit"):
            self.kubectl.run(
                [
                    "-n",
                    NAMESPACE,
                    "rollout",
                    "status",
                    f"deployment/{deployment}",
                    "--timeout=180s",
                ],
                timeout_seconds=210,
            )
        self.wait_resource_conditions("gateway", "ai-gateway", ("Programmed",))
        for route in ("route-qwen-chat", "route-gemma-chat", "route-embedding-index"):
            self.wait_resource_conditions("httproute", route, ("Accepted", "ResolvedRefs"))
        for policy in ("policy-qwen-chat", "policy-gemma-chat", "policy-embedding-index"):
            self.wait_resource_conditions("agentgatewaypolicy", policy, ("Accepted", "Attached"))

    def wait_resource_conditions(
        self,
        kind: str,
        name: str,
        conditions: Sequence[str],
        *,
        timeout_seconds: int = 180,
    ) -> None:
        """Poll Gateway API-style nested status conditions with a fixed deadline."""

        deadline = time.monotonic() + timeout_seconds
        last_status = "not read yet"
        while time.monotonic() < deadline:
            result = self.kubectl.run(
                ["-n", NAMESPACE, "get", kind, name, "-o", "json"],
                check=False,
                timeout_seconds=60,
            )
            if result.returncode == 0:
                payload = json.loads(result.stdout)
                if isinstance(payload, dict):
                    if _has_conditions(cast(dict[str, Any], payload), conditions):
                        return
                    last_status = json.dumps(
                        _condition_statuses(cast(dict[str, Any], payload)),
                        sort_keys=True,
                    )
            else:
                last_status = result.stderr.strip() or f"kubectl exited {result.returncode}"
            time.sleep(2)
        msg = (
            f"timed out waiting for {kind}/{name} conditions "
            f"{', '.join(conditions)}; last status: {last_status[:500]}"
        )
        raise AirgapGatewayError(msg)

    def airgap_runtime_gate(self) -> None:
        """Audit runtime image references and warning events after install."""

        pods = self.kubectl.run(["get", "pods", "-A", "-o", "json"], timeout_seconds=60)
        pod_data = json.loads(pods.stdout)
        events = self.kubectl.run(["get", "events", "-A", "-o", "json"], timeout_seconds=60)
        event_data = json.loads(events.stdout)
        namespace_images: list[str] = []
        system_images: list[str] = []
        for item in _as_list(pod_data.get("items", [])):
            namespace = _metadata(item).get("namespace")
            images = _pod_images(item)
            if namespace == NAMESPACE:
                namespace_images.extend(images)
            else:
                system_images.extend(images)
        public_namespace_images = [
            image
            for image in namespace_images
            if _is_public_image(image) and not image.startswith(f"{self.names.registry}/")
        ]
        public_pull_events = _public_pull_events(event_data, target_namespace=NAMESPACE)
        audit: dict[str, object] = {
            "kindNodeFixturePublicImages": sorted(set(system_images)),
            "namespace": NAMESPACE,
            "namespaceImages": sorted(namespace_images),
            "publicPullEvents": public_pull_events,
            "publicRuntimeImages": sorted(public_namespace_images),
            "status": "passed"
            if not public_namespace_images and not public_pull_events
            else "failed",
        }
        self.evidence.image_audit = audit
        path = self.names.run_dir / "evidence" / "airgap-runtime-gate.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"audit": audit, "events": event_data}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.evidence.artifacts["airgap_runtime_gate"] = str(path)
        if public_namespace_images:
            msg = f"namespace runtime public image references found: {', '.join(public_namespace_images)}"
            raise AirgapGatewayError(msg)
        if public_pull_events:
            msg = "public image pull events found after local image preparation"
            raise AirgapGatewayError(msg)

    def collect_failure_diagnostics(self) -> None:
        """Write bounded Kubernetes diagnostics before disposable teardown."""

        evidence_dir = self.names.run_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        diagnostics: dict[str, object] = {}
        commands = {
            "pods": ["-n", NAMESPACE, "get", "pods", "-o", "wide"],
            "deployments": ["-n", NAMESPACE, "get", "deployments", "-o", "wide"],
            "services": ["-n", NAMESPACE, "get", "services", "-o", "wide"],
            "events": ["-n", NAMESPACE, "get", "events", "--sort-by=.lastTimestamp"],
            "gateway_logs": ["-n", NAMESPACE, "logs", "deployment/ai-gateway", "--tail=200"],
            "ratelimit_logs": [
                "-n",
                NAMESPACE,
                "logs",
                "deployment/envoy-ratelimit",
                "--tail=200",
            ],
        }
        for name, command in commands.items():
            with contextlib.suppress(Exception):
                result = self.kubectl.run(command, check=False, timeout_seconds=30)
                diagnostics[name] = {
                    "returncode": result.returncode,
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                }
        path = evidence_dir / "failure-diagnostics.json"
        if path.exists():
            index = 2
            while (evidence_dir / f"failure-diagnostics-{index}.json").exists():
                index += 1
            path = evidence_dir / f"failure-diagnostics-{index}.json"
        path.write_text(json.dumps(diagnostics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.evidence.artifacts["failure_diagnostics"] = str(path)

    def run_behavior_matrix(self, *, target: str) -> None:
        """Run the required status-code matrix through a gateway target."""

        service = (
            "retained-nginx-edge" if target == "retained-nginx-edge" else self.gateway_service()
        )
        with self.port_forward(service) as endpoint:
            cases = [
                ("missing key returns 401", "qwen.ai.example.internal", None, "chat", 401),
                (
                    "unknown key returns 401",
                    "qwen.ai.example.internal",
                    fake_credentials()["unknown"],
                    "chat",
                    401,
                ),
                (
                    "allowed Qwen consumer returns 200",
                    "qwen.ai.example.internal",
                    fake_credentials()["internal-chat"],
                    "chat",
                    200,
                ),
                (
                    "denied Qwen consumer returns 403",
                    "qwen.ai.example.internal",
                    fake_credentials()["rag-indexer"],
                    "chat",
                    403,
                ),
                (
                    "allowed Gemma consumer returns 200",
                    "gemma.ai.example.internal",
                    fake_credentials()["internal-chat"],
                    "chat",
                    200,
                ),
                (
                    "allowed embedding consumer returns 200 with vector",
                    "embed.ai.example.internal",
                    fake_credentials()["rag-indexer"],
                    "embedding",
                    200,
                ),
                (
                    "denied embedding consumer returns 403",
                    "embed.ai.example.internal",
                    fake_credentials()["internal-chat"],
                    "embedding",
                    403,
                ),
                (
                    "wrong Host returns 404",
                    "wrong.ai.example.internal",
                    fake_credentials()["internal-chat"],
                    "chat",
                    404,
                ),
            ]
            for name, host, key, kind, expected in cases:
                started = time.monotonic()
                status, payload = gateway_request(endpoint, host=host, api_key=key, kind=kind)
                passed = status == expected
                if name.endswith("with vector") and status == 200:
                    try:
                        verify_embedding_response(status, payload)
                    except Exception as exc:  # pragma: no cover - detail captured in evidence
                        passed = False
                        payload = {"error": str(exc)}
                self.evidence.add(
                    f"{target}: {name}",
                    passed,
                    detail=f"expected={expected} actual={status}",
                    duration=time.monotonic() - started,
                )
            self.run_rate_limit_case(endpoint, target=target)

    def run_rate_limit_case(self, endpoint: str, *, target: str) -> None:
        """Generate bounded repeated traffic for the low-limit 429 case."""

        statuses: list[int] = []
        for _ in range(6):
            status, _payload = gateway_request(
                endpoint,
                host="gemma.ai.example.internal",
                api_key=fake_credentials()["low-limit"],
                kind="chat",
            )
            statuses.append(status)
            if status == 429:
                break
        self.evidence.add(
            f"{target}: repeated traffic reaches 429",
            429 in statuses,
            detail=f"statuses={statuses}",
        )

    def run_broken_backend_test(self) -> None:
        """Apply a retained broken route and require useful diagnostics."""

        self.kubectl.run(["apply", "-f", str(BROKEN_BACKEND_MANIFEST)], timeout_seconds=60)
        time.sleep(5)
        route = self.kubectl.run(
            ["-n", NAMESPACE, "get", "httproute", "route-broken-backend", "-o", "json"],
            timeout_seconds=60,
        )
        route_data = json.loads(route.stdout)
        conditions = json.dumps(route_data.get("status", {}), sort_keys=True)
        resolved_false = "ResolvedRefs" in conditions and "False" in conditions
        useful = resolved_false
        if not useful:
            with self.port_forward(self.gateway_service()) as endpoint:
                status, payload = gateway_request(
                    endpoint,
                    host="broken.ai.example.internal",
                    api_key=fake_credentials()["internal-chat"],
                    kind="chat",
                )
                useful = status >= 500
                conditions = f"status={status} payload={payload}"
        self.evidence.add(
            "broken backend reports failed refs or upstream failure",
            useful,
            detail=conditions[:500],
        )

    def deploy_retained_nginx(self, image_digests: dict[str, str]) -> None:
        """Apply the optional retained NGINX edge path."""

        documents = _load_yaml_documents(NGINX_EDGE_MANIFEST)
        documents = rewrite_documents_for_local_registry(
            documents,
            local_registry=self.names.registry,
            digest_overrides=_digest_overrides(image_digests),
        )
        path = self.names.run_dir / "rendered" / "retained-nginx-edge.yaml"
        path.write_text(dump_documents(documents), encoding="utf-8")
        self.kubectl.run(["apply", "-f", str(path)], timeout_seconds=60)
        self.kubectl.run(
            [
                "-n",
                NAMESPACE,
                "rollout",
                "status",
                "deployment/retained-nginx-edge",
                "--timeout=120s",
            ],
            timeout_seconds=150,
        )

    def assert_model_services_survive_gateway_cleanup(self) -> None:
        """Delete only the Gateway and prove model Services remain."""

        before = self.model_services()
        self.kubectl.run(["-n", NAMESPACE, "delete", "gateway", "ai-gateway"], timeout_seconds=60)
        after = self.model_services()
        self.evidence.add(
            "model Services survive gateway cleanup",
            before == after == {"qwen-nim", "gemma-nim", "embedding-nim"},
            detail=f"before={sorted(before)} after={sorted(after)}",
        )

    def model_services(self) -> set[str]:
        """Return the model Service names currently present."""

        services = self.kubectl.run(
            ["-n", NAMESPACE, "get", "svc", "-o", "json"], timeout_seconds=60
        )
        data = json.loads(services.stdout)
        return {
            item["metadata"]["name"]
            for item in data.get("items", [])
            if item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component")
            == "nim-service-contract"
        }

    def gateway_service(self) -> str:
        """Discover the generated gateway Service."""

        result = self.kubectl.run(
            [
                "-n",
                NAMESPACE,
                "get",
                "svc",
                "-o",
                "json",
            ],
            timeout_seconds=60,
        )
        items = json.loads(result.stdout).get("items", [])
        candidates: list[dict[str, Any]] = []
        for item in _as_list(items):
            metadata = _metadata(item)
            labels = metadata.get("labels", {})
            if not isinstance(labels, dict):
                labels = {}
            if (
                metadata.get("name") == "ai-gateway"
                or labels.get("app.kubernetes.io/component") == "generated-data-plane-service"
                or labels.get("gateway.networking.k8s.io/gateway-name") == "ai-gateway"
            ):
                candidates.append(item)
        if not candidates:
            return "ai-gateway"
        candidates.sort(key=lambda item: 0 if _metadata(item).get("name") == "ai-gateway" else 1)
        return str(_metadata(candidates[0])["name"])

    def service_port(self, service: str) -> int:
        """Return the Service port to use for a local port-forward."""

        result = self.kubectl.run(
            ["-n", NAMESPACE, "get", "svc", service, "-o", "json"],
            timeout_seconds=60,
        )
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            msg = f"service/{service} returned non-object JSON"
            raise AirgapGatewayError(msg)
        spec = payload.get("spec", {})
        if not isinstance(spec, dict):
            msg = f"service/{service} returned no spec"
            raise AirgapGatewayError(msg)
        ports = _as_list(spec.get("ports", []))
        for service_port in ports:
            if service_port.get("port") == 80:
                return 80
        for service_port in ports:
            value = service_port.get("port")
            if isinstance(value, int):
                return value
        msg = f"service/{service} has no usable port"
        raise AirgapGatewayError(msg)

    @contextlib.contextmanager
    def port_forward(self, service: str) -> Any:
        """Port-forward a Service and yield the local endpoint."""

        port = find_free_port()
        remote_port = self.service_port(service)
        self.kubectl.verify_context()
        print(f"kubectl context verified: {self.names.context}", file=sys.stderr)
        command = [
            "kubectl",
            "--context",
            self.names.context,
            "-n",
            NAMESPACE,
            "port-forward",
            "--address",
            "127.0.0.1",
            f"svc/{service}",
            f"{port}:{remote_port}",
        ]
        print(f"kubectl command: {' '.join(command)}", file=sys.stderr)
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            try:
                wait_for_port(port)
            except AirgapGatewayError as exc:
                process.terminate()
                stdout, stderr = process.communicate(timeout=5)
                detail = stderr.strip() or stdout.strip()
                msg = f"{exc}; kubectl port-forward detail: {detail[:1000]}"
                raise AirgapGatewayError(msg) from exc
            yield f"http://127.0.0.1:{port}"
        finally:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)

    def write_evidence(self) -> None:
        """Write JSON, JUnit, and Markdown evidence."""

        evidence_dir = self.names.run_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        json_path = evidence_dir / "results.json"
        junit_path = evidence_dir / "junit.xml"
        markdown_path = evidence_dir / "report.md"
        json_path.write_text(
            json.dumps(self.evidence_to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        junit_path.write_text(junit_xml(self.evidence.results), encoding="utf-8")
        markdown_path.write_text(markdown_report(self.evidence), encoding="utf-8")
        self.evidence.artifacts.update(
            {"json": str(json_path), "junit": str(junit_path), "markdown": str(markdown_path)}
        )

    def evidence_to_dict(self) -> dict[str, Any]:
        """Return evidence as a redacted JSON-safe dictionary."""

        return {
            "cluster": self.names.cluster,
            "context": self.names.context,
            "registry": self.names.registry,
            "imageAudit": self.evidence.image_audit,
            "results": [
                {
                    "name": result.name,
                    "status": result.status,
                    "detail": result.detail,
                    "durationSeconds": result.duration_seconds,
                }
                for result in self.evidence.results
            ],
            "status": "failed" if self.evidence.failed else "passed",
        }

    def teardown(self) -> None:
        """Tear down only the generated disposable cluster and registry."""

        validate_disposable_cluster_name(self.names.cluster)
        try:
            self.runner.run(
                ["kind", "delete", "cluster", "--name", self.names.cluster],
                check=False,
                timeout_seconds=240,
            )
        finally:
            if self.names.registry_container.startswith(REGISTRY_PREFIX):
                self.runner.run(
                    ["docker", "rm", "-f", self.names.registry_container],
                    check=False,
                    timeout_seconds=60,
                )


def registry_digest(registry: str, repository_tag: str) -> str | None:  # pragma: no cover
    """Read Docker-Content-Digest for a local registry tag."""

    if ":" not in repository_tag:
        return None
    repository, tag = repository_tag.rsplit(":", 1)
    url = f"http://{registry}/v2/{repository}/manifests/{tag}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": (
                "application/vnd.oci.image.manifest.v1+json,"
                "application/vnd.docker.distribution.manifest.v2+json,"
                "application/vnd.docker.distribution.manifest.list.v2+json"
            )
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            digest = response.headers.get("Docker-Content-Digest")
            return str(digest) if digest is not None else None
    except urllib.error.URLError:
        return None


def gateway_request(
    endpoint: str,
    *,
    host: str,
    api_key: str | None,
    kind: str,
) -> tuple[int, dict[str, Any]]:  # pragma: no cover
    """Send one request through a forwarded gateway endpoint."""

    path = "/v1/embeddings" if kind == "embedding" else "/v1/chat/completions"
    body: dict[str, Any]
    if kind == "embedding":
        body = {"model": "embedding-index", "input": "airgap e2e"}
    else:
        body = {"model": "chat", "messages": [{"role": "user", "content": "hello"}]}
    headers = {"Host": host, "Content-Type": "application/json"}
    if api_key is not None:
        headers["x-api-key"] = api_key
    request = urllib.request.Request(
        f"{endpoint}{path}",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        with contextlib.suppress(json.JSONDecodeError):
            return exc.code, json.loads(raw)
        return exc.code, {"error": raw}


def junit_xml(results: Sequence[LabResult]) -> str:
    """Write a compact JUnit document."""

    failures = sum(1 for result in results if result.status != "passed")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuite name="kind-e2e-lab" tests="{len(results)}" failures="{failures}">',
    ]
    for result in results:
        lines.append(
            f'  <testcase name="{escape(result.name)}" time="{result.duration_seconds:.3f}">'
        )
        if result.status != "passed":
            lines.append(f'    <failure message="{escape(result.detail)}" />')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines) + "\n"


def markdown_report(evidence: LabEvidence) -> str:
    """Write a human-readable lab report."""

    lines = [
        "# Kind End-to-End Lab Report",
        "",
        f"- Cluster: `{evidence.names.cluster}`",
        f"- Context: `{evidence.names.context}`",
        f"- Registry: `{evidence.names.registry}`",
        f"- Status: `{'failed' if evidence.failed else 'passed'}`",
        "",
        "## Results",
        "",
    ]
    for result in evidence.results:
        icon = "PASS" if result.status == "passed" else "FAIL"
        lines.append(f"- {icon}: {result.name} — {result.detail}")
    lines.extend(["", "## Image audit", "", "```json"])
    lines.append(json.dumps(evidence.image_audit, indent=2, sort_keys=True))
    lines.extend(["```", ""])
    return "\n".join(lines)


def wait_for_port(port: int, *, timeout_seconds: int = 30) -> None:  # pragma: no cover
    """Wait until a local port accepts TCP connections."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
            return
        time.sleep(0.2)
    msg = f"port-forward did not become ready on 127.0.0.1:{port}"
    raise AirgapGatewayError(msg)


def sha256_file(path: Path) -> str:  # pragma: no cover
    """Return a file SHA-256."""

    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rewrite_image(image: str, local_registry: str, digest_overrides: dict[str, str]) -> str:
    if image.startswith(f"{PLACEHOLDER_REGISTRY}/"):
        remainder = image.removeprefix(f"{PLACEHOLDER_REGISTRY}/")
        if remainder.startswith("airgap-ai-gateway/openai-mock"):
            return f"{local_registry}/{MOCK_IMAGE_REPOSITORY}:{MOCK_IMAGE_TAG}"
        if remainder.startswith("envoyproxy/ratelimit@"):
            return f"{local_registry}/envoyproxy/ratelimit@{digest_overrides.get('envoy-ratelimit', remainder.split('@', 1)[1])}"
        if remainder.startswith("library/redis@"):
            return f"{local_registry}/library/redis@{digest_overrides.get('redis', remainder.split('@', 1)[1])}"
        if remainder.startswith("library/nginx:"):
            return f"{local_registry}/library/nginx:1.27.5-alpine"
        return f"{local_registry}/{remainder}"
    return image


def _digest_overrides(image_digests: dict[str, str]) -> dict[str, str]:
    return {
        "agentgateway": image_digests.get("agentgateway", ""),
        "envoy-ratelimit": image_digests.get("envoy-ratelimit", ""),
        "redis": image_digests.get("redis", ""),
    }


def _has_conditions(payload: dict[str, Any], condition_types: Sequence[str]) -> bool:
    statuses = _condition_statuses(payload)
    return all(statuses.get(condition_type) == "True" for condition_type in condition_types)


def _condition_statuses(payload: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for condition in _status_condition_items(payload):
        condition_type = condition.get("type")
        status = condition.get("status")
        if isinstance(condition_type, str) and isinstance(status, str):
            statuses[condition_type] = status
    return statuses


def _status_condition_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    status = payload.get("status", {})
    if not isinstance(status, dict):
        return []
    direct = _as_list(status.get("conditions", []))
    nested: list[dict[str, Any]] = []
    for key in ("parents", "ancestors"):
        for owner_status in _as_list(status.get(key, [])):
            nested.extend(_as_list(owner_status.get("conditions", [])))
    return [*direct, *nested]


def _pod_images(pod: dict[str, Any]) -> list[str]:
    spec = pod.get("spec", {})
    if not isinstance(spec, dict):
        return []
    images: list[str] = []
    for container_field in ("initContainers", "containers"):
        for container in _as_list(spec.get(container_field, [])):
            image = container.get("image")
            if isinstance(image, str) and "pause:" not in image:
                images.append(image)
    return images


def _public_pull_events(event_data: object, *, target_namespace: str) -> list[dict[str, str]]:
    if not isinstance(event_data, dict):
        return []
    results: list[dict[str, str]] = []
    for event in _as_list(event_data.get("items", [])):
        reason = str(event.get("reason", ""))
        message = str(event.get("message", ""))
        if reason != "Pulling" or not _is_public_image(message):
            continue
        involved = event.get("involvedObject", {})
        name = ""
        event_namespace = ""
        if isinstance(involved, dict):
            name = str(involved.get("name", ""))
            event_namespace = str(involved.get("namespace", ""))
        if event_namespace != target_namespace:
            continue
        results.append({"name": name, "namespace": event_namespace, "message": message[:300]})
    return results


def _is_public_image(value: str) -> bool:
    return any(marker in value for marker in PUBLIC_REGISTRY_MARKERS)


def _load_yaml_documents(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [item for item in yaml.safe_load_all(handle) if isinstance(item, dict)]


def _metadata(document: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], document.get("metadata", {}))


def _as_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]


def build_parser() -> argparse.ArgumentParser:
    """Build the lab CLI parser."""

    parser = argparse.ArgumentParser(description="Disposable kind e2e lab")
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="Create, test, report, and remove the lab.")
    run.add_argument("--run-id", default=None)
    run.add_argument("--with-nginx", action="store_true")
    run.add_argument("--keep", action="store_true")
    run.set_defaults(handler=_handle_run)
    plan = subcommands.add_parser("plan", help="Show the generated disposable names.")
    plan.add_argument("--run-id", default=None)
    plan.set_defaults(handler=_handle_plan)
    return parser


def _handle_run(args: argparse.Namespace) -> int:  # pragma: no cover
    names = new_lab_names(run_id=args.run_id)
    evidence = KindE2ELab(names=names, with_nginx=args.with_nginx, keep=args.keep).run()
    print(json.dumps({"runDir": str(names.run_dir), "failed": evidence.failed}, indent=2))
    return 1 if evidence.failed else 0


def _handle_plan(args: argparse.Namespace) -> int:  # pragma: no cover
    names = new_lab_names(run_id=args.run_id, registry_port=5001)
    print(
        json.dumps(
            {
                "cluster": names.cluster,
                "context": names.context,
                "registry": names.registry,
                "registryContainer": names.registry_container,
                "runDir": str(names.run_dir),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover
    """Run the lab CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except AirgapGatewayError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
