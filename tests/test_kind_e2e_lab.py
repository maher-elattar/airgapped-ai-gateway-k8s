from __future__ import annotations

import importlib.util
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from airgap_ai_gateway.errors import AirgapGatewayError
from airgap_ai_gateway.kind_lab import (
    KubectlGuard,
    LabEvidence,
    LabResult,
    _has_conditions,
    _public_pull_events,
    build_parser,
    fake_credentials,
    find_free_port,
    junit_xml,
    markdown_report,
    new_lab_names,
    render_lab_documents,
    rendered_images,
    runtime_secret_manifest,
    validate_disposable_cluster_name,
    validate_lab_documents,
)

MOCK_PATH = Path("lab/mocks/openai_mock.py")


class FakeRunner:
    def __init__(self, context: str) -> None:
        self.context = context
        self.calls: list[list[str]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if list(args) == ["kubectl", "config", "current-context"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{self.context}\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")


def test_mock_embedding_is_deterministic_and_non_empty() -> None:
    spec = importlib.util.spec_from_file_location("openai_mock", MOCK_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    first = module.deterministic_embedding("airgap")
    second = module.deterministic_embedding("airgap")

    assert first == second
    assert len(first) > 0
    assert all(isinstance(value, float) for value in first)


def test_lab_overlay_routes_are_protected_and_images_are_local() -> None:
    documents = render_lab_documents(
        local_registry="registry.example.internal:5000",
        digest_overrides={
            "agentgateway": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "envoy-ratelimit": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "redis": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        },
    )

    assert validate_lab_documents(documents, registry="registry.example.internal:5000") == []
    assert any("openai-mock:e2e-lab" in image for image in rendered_images(documents))


def test_lab_overlay_can_include_retained_nginx_edge() -> None:
    documents = render_lab_documents(
        local_registry="registry.example.internal:5000",
        digest_overrides={
            "agentgateway": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "envoy-ratelimit": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "redis": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        },
        include_nginx=True,
    )
    images = rendered_images(documents)

    assert validate_lab_documents(documents, registry="registry.example.internal:5000") == []
    assert "registry.example.internal:5000/library/nginx:1.27.5-alpine" in images


def test_lab_validation_fails_when_route_is_unprotected() -> None:
    documents = render_lab_documents(
        local_registry="registry.example.internal:5000",
        digest_overrides={
            "agentgateway": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "envoy-ratelimit": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "redis": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        },
    )
    filtered = [
        document
        for document in documents
        if not (
            document.get("kind") == "AgentgatewayPolicy"
            and document.get("metadata", {}).get("name") == "policy-qwen-chat"
        )
    ]

    errors = validate_lab_documents(filtered, registry="registry.example.internal:5000")

    assert "HTTPRoute route-qwen-chat has no AgentgatewayPolicy" in errors


def test_lab_validation_fails_when_runtime_image_uses_public_registry() -> None:
    documents = render_lab_documents(
        local_registry="registry.example.internal:5000",
        digest_overrides={
            "agentgateway": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "envoy-ratelimit": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "redis": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        },
    )
    for document in documents:
        if document.get("kind") == "Deployment":
            containers = document["spec"]["template"]["spec"]["containers"]
            containers[0]["image"] = "docker.io/library/python:latest"
            break

    errors = validate_lab_documents(documents, registry="registry.example.internal:5000")

    assert any("public registry" in error for error in errors)


def test_kubectl_guard_verifies_context_before_target_command() -> None:
    runner = FakeRunner("kind-agw-e2e-sample")
    guard = KubectlGuard(runner, "kind-agw-e2e-sample", {})

    guard.run(["get", "pods"])

    assert runner.calls == [
        ["kubectl", "config", "current-context"],
        ["kubectl", "--context", "kind-agw-e2e-sample", "get", "pods"],
    ]


def test_kubectl_guard_rejects_context_mismatch() -> None:
    guard = KubectlGuard(FakeRunner("kind-other"), "kind-agw-e2e-sample", {})

    with pytest.raises(AirgapGatewayError, match="refusing kubectl operation"):
        guard.run(["get", "pods"])


def test_teardown_guard_refuses_unrelated_cluster_names() -> None:
    with pytest.raises(AirgapGatewayError):
        validate_disposable_cluster_name("production")

    validate_disposable_cluster_name("agw-e2e-sample1")


def test_runtime_secret_uses_fake_values_and_no_string_data() -> None:
    secret = runtime_secret_manifest(fake_credentials())

    assert secret["kind"] == "Secret"
    assert "stringData" not in secret
    assert "data" in secret
    assert "example-only-do-not-use" not in str(secret["data"].values())


def test_evidence_helpers_render_failed_and_passed_results() -> None:
    names = new_lab_names(run_id="sample-c", registry_port=5003)
    evidence = LabEvidence(names)
    evidence.image_audit = {"status": "passed", "publicImages": []}
    evidence.add("passes", True, "ok", duration=0.1234)
    evidence.add("fails", False, "bad")

    junit = junit_xml(evidence.results)
    markdown = markdown_report(evidence)

    assert evidence.failed
    assert 'failures="1"' in junit
    assert '<failure message="bad" />' in junit
    assert "FAIL: fails" in markdown
    assert '"publicImages": []' in markdown


def test_junit_xml_escapes_result_names_and_details() -> None:
    xml = junit_xml([LabResult('route <bad> & "quoted"', "failed", "x < y")])

    assert "&lt;bad&gt;" in xml
    assert "x &lt; y" in xml


def test_find_free_port_returns_connectable_port_number() -> None:
    port = find_free_port()

    assert 1024 < port < 65536


def test_lab_parser_exposes_run_and_plan_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["plan", "--run-id", "sample"]).command == "plan"
    assert parser.parse_args(["run", "--with-nginx", "--keep"]).with_nginx is True


def test_condition_helper_reads_agentgateway_ancestor_conditions() -> None:
    payload = {
        "status": {
            "ancestors": [
                {
                    "conditions": [
                        {"type": "Accepted", "status": "True"},
                        {"type": "Attached", "status": "True"},
                    ]
                }
            ]
        }
    }

    assert _has_conditions(payload, ("Accepted", "Attached"))


def test_public_pull_event_filter_is_scoped_to_lab_namespace() -> None:
    events = {
        "items": [
            {
                "reason": "Pulling",
                "message": 'Pulling image "registry.k8s.io/coredns/coredns:v1.11.1"',
                "involvedObject": {"namespace": "kube-system", "name": "coredns"},
            },
            {
                "reason": "Pulling",
                "message": 'Pulling image "docker.io/library/python:latest"',
                "involvedObject": {"namespace": "ai-gateway", "name": "bad"},
            },
        ]
    }

    assert _public_pull_events(events, target_namespace="ai-gateway") == [
        {
            "message": 'Pulling image "docker.io/library/python:latest"',
            "name": "bad",
            "namespace": "ai-gateway",
        }
    ]


def test_generated_lab_names_are_unique_and_disposable() -> None:
    first = new_lab_names(run_id="sample-a", registry_port=5001)
    second = new_lab_names(run_id="sample-b", registry_port=5002)

    assert first.cluster != second.cluster
    assert first.context == f"kind-{first.cluster}"
    validate_disposable_cluster_name(first.cluster)
