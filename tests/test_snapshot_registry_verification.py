from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from urllib import request

import pytest

from airgap_ai_gateway.command import CommandResult
from airgap_ai_gateway.configuration import load_config
from airgap_ai_gateway.errors import BundleError, SafetyError, VerificationError
from airgap_ai_gateway.execution import FakeCommandRunner
from airgap_ai_gateway.ledger import PreChangeSnapshot, ResourceRef
from airgap_ai_gateway.models import ModelKind
from airgap_ai_gateway.planning import PLAN_SCHEMA_VERSION, ExecutionPlan
from airgap_ai_gateway.registry import apply_promotion_plan, promotion_plan
from airgap_ai_gateway.snapshot import capture_snapshot, redacted_snapshot_preview
from airgap_ai_gateway.verification import default_http_probe, run_runtime_verification

EXPECTED_CONTEXT = "kind-airgap-ai-gateway"
CONFIRMATION = "I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY"
EXAMPLE_CONFIG = Path("examples/config")
SECRET_VALUE = "example-secret-value-do-not-leak"


def test_snapshot_capture_records_absent_and_sensitive_resources(tmp_path: Path) -> None:
    secret = ResourceRef("v1", "Secret", "ai-gateway", "agentgateway-consumer-keys")
    configmap = ResourceRef("v1", "ConfigMap", "ai-gateway", "gateway-config")
    plan = _execution_plan((secret, configmap))
    runner = FakeCommandRunner(
        {
            ("kubectl", "config", "current-context"): CommandResult(0, EXPECTED_CONTEXT),
            ("kubectl", "-n", "ai-gateway", "get", "secret", secret.name, "-o", "json"): (
                CommandResult(
                    0,
                    json.dumps(
                        {
                            "apiVersion": "v1",
                            "kind": "Secret",
                            "metadata": {"name": secret.name, "namespace": secret.namespace},
                            "stringData": {"api-key": SECRET_VALUE},
                        }
                    ),
                )
            ),
            ("kubectl", "-n", "ai-gateway", "get", "configmap", configmap.name, "-o", "json"): (
                CommandResult(1, stderr="NotFound")
            ),
        },
        strict=True,
    )

    report = capture_snapshot(
        plan=plan,
        runner=runner,
        expected_context=EXPECTED_CONTEXT,
        output_file=tmp_path / "snapshot.json",
    )

    assert report.resource_count == 1
    assert report.absent_count == 1
    assert report.sensitive_count == 1
    snapshot = json.loads((tmp_path / "snapshot.json").read_text(encoding="utf-8"))
    assert SECRET_VALUE in json.dumps(snapshot)
    preview = redacted_snapshot_preview(PreChangeSnapshot.from_dict(snapshot))
    assert SECRET_VALUE not in json.dumps(preview)


def test_snapshot_refuses_context_mismatch(tmp_path: Path) -> None:
    plan = _execution_plan((ResourceRef("v1", "ConfigMap", "ai-gateway", "gateway-config"),))

    with pytest.raises(SafetyError, match="expected context"):
        capture_snapshot(
            plan=plan,
            runner=FakeCommandRunner(),
            expected_context="other-context",
            output_file=tmp_path / "snapshot.json",
        )


def test_registry_promotion_apply_uses_approved_plan_and_redacted_log(tmp_path: Path) -> None:
    plan_file = tmp_path / "promotion-plan.json"
    plan_file.write_text(
        json.dumps(
            {
                "actions": [
                    {
                        "checkExistingBeforePush": True,
                        "copyCommand": ["skopeo", "copy", "docker://src-a", "docker://dst-a"],
                        "existenceCheck": ["skopeo", "inspect", "docker://dst-a"],
                        "name": "already-present",
                    },
                    {
                        "checkExistingBeforePush": True,
                        "copyCommand": [
                            "skopeo",
                            "copy",
                            "docker://src-b",
                            "docker://dst-b?token=example-secret-value-do-not-leak",
                        ],
                        "existenceCheck": ["skopeo", "inspect", "docker://dst-b"],
                        "name": "promote-me",
                    },
                ],
                "privateRegistry": "registry.example.internal:5000",
                "status": "planned",
            }
        ),
        encoding="utf-8",
    )
    runner = FakeCommandRunner(
        {
            ("skopeo", "inspect", "docker://dst-a"): CommandResult(0, "exists"),
            ("skopeo", "inspect", "docker://dst-b"): CommandResult(1, stderr="missing"),
            (
                "skopeo",
                "copy",
                "docker://src-b",
                "docker://dst-b?token=example-secret-value-do-not-leak",
            ): CommandResult(0, "copied"),
        },
        strict=True,
    )

    report = apply_promotion_plan(
        plan_file=plan_file,
        runner=runner,
        confirmation=CONFIRMATION,
        expected_confirmation=CONFIRMATION,
        commands_log_path=tmp_path / "commands.log",
    )

    assert report["status"] == "applied"
    results = cast(list[dict[str, object]], report["results"])
    assert [item["status"] for item in results] == ["already-present", "promoted"]
    command_log = (tmp_path / "commands.log").read_text(encoding="utf-8")
    assert SECRET_VALUE not in command_log


def test_registry_promotion_refuses_bad_confirmation(tmp_path: Path) -> None:
    plan_file = tmp_path / "promotion-plan.json"
    plan_file.write_text('{"actions": [], "status": "planned"}\n', encoding="utf-8")

    with pytest.raises(SafetyError, match="confirmation"):
        apply_promotion_plan(
            plan_file=plan_file,
            runner=FakeCommandRunner(),
            confirmation="wrong",
            expected_confirmation=CONFIRMATION,
        )


def test_registry_promotion_plan_falls_back_to_config_when_lock_is_absent(tmp_path: Path) -> None:
    config = load_config(EXAMPLE_CONFIG)

    plan = promotion_plan(config, lock_file=tmp_path / "missing-lock.yaml")

    assert plan["status"] == "registry-promotion-skeleton"
    assert plan["private_registry"] == "registry.example.internal:5000"
    assert plan["images"]


def test_registry_promotion_apply_reports_copy_failure(tmp_path: Path) -> None:
    plan_file = tmp_path / "promotion-plan.json"
    plan_file.write_text(
        json.dumps(
            {
                "actions": [
                    {
                        "checkExistingBeforePush": False,
                        "copyCommand": ["skopeo", "copy", "docker://src", "docker://dst"],
                        "existenceCheck": ["skopeo", "inspect", "docker://dst"],
                        "name": "broken",
                    }
                ],
                "status": "planned",
            }
        ),
        encoding="utf-8",
    )
    runner = FakeCommandRunner(
        {("skopeo", "copy", "docker://src", "docker://dst"): CommandResult(2, stderr="boom")},
        strict=True,
    )

    with pytest.raises(BundleError, match="registry promotion failed"):
        apply_promotion_plan(
            plan_file=plan_file,
            runner=runner,
            confirmation=CONFIRMATION,
            expected_confirmation=CONFIRMATION,
        )


def test_registry_promotion_apply_rejects_malformed_plan(tmp_path: Path) -> None:
    bad_status = tmp_path / "bad-status.json"
    bad_status.write_text('{"actions": [], "status": "draft"}\n', encoding="utf-8")
    with pytest.raises(BundleError, match="status must be planned"):
        apply_promotion_plan(
            plan_file=bad_status,
            runner=FakeCommandRunner(),
            confirmation=CONFIRMATION,
            expected_confirmation=CONFIRMATION,
        )

    missing_actions = tmp_path / "missing-actions.json"
    missing_actions.write_text('{"status": "planned"}\n', encoding="utf-8")
    with pytest.raises(BundleError, match="actions list"):
        apply_promotion_plan(
            plan_file=missing_actions,
            runner=FakeCommandRunner(),
            confirmation=CONFIRMATION,
            expected_confirmation=CONFIRMATION,
        )

    invalid_command = tmp_path / "invalid-command.json"
    invalid_command.write_text(
        json.dumps(
            {
                "actions": [
                    {
                        "checkExistingBeforePush": False,
                        "copyCommand": "skopeo copy",
                        "existenceCheck": [],
                        "name": "bad",
                    }
                ],
                "status": "planned",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BundleError, match="copyCommand"):
        apply_promotion_plan(
            plan_file=invalid_command,
            runner=FakeCommandRunner(),
            confirmation=CONFIRMATION,
            expected_confirmation=CONFIRMATION,
        )


def test_runtime_verification_matrix_passes_with_fake_runner_and_probe() -> None:
    config = load_config(EXAMPLE_CONFIG)
    runner = FakeCommandRunner(_runtime_responses(config), strict=True)
    probe = _MatrixProbe()

    report = run_runtime_verification(
        config,
        runner=runner,
        expected_context=EXPECTED_CONTEXT,
        gateway_url="https://gateway.example.internal",
        credentials={
            "internal-chat": "internal-key",
            "rag-indexer": "rag-key",
            "testing-client": "low-key",
            "unknown": "unknown-key",
        },
        http_probe=probe,
        rate_limit_attempts=5,
    )

    assert report["status"] == "passed"
    assert report["tls_verify"] is True
    assert any(call == ("kubectl", "config", "current-context") for call in runner.calls)


def test_runtime_verification_records_embedding_vector_failure() -> None:
    config = load_config(EXAMPLE_CONFIG)
    runner = FakeCommandRunner(_runtime_responses(config), strict=True)
    probe = _MatrixProbe(embedding_vector=False)

    report = run_runtime_verification(
        config,
        runner=runner,
        expected_context=EXPECTED_CONTEXT,
        gateway_url="https://gateway.example.internal",
        credentials={
            "internal-chat": "internal-key",
            "rag-indexer": "rag-key",
            "testing-client": "low-key",
            "unknown": "unknown-key",
        },
        http_probe=probe,
        rate_limit_attempts=5,
    )

    assert report["status"] == "failed"
    assert "vector" in json.dumps(report)


def test_runtime_verification_refuses_wrong_expected_context() -> None:
    config = load_config(EXAMPLE_CONFIG)

    with pytest.raises(VerificationError, match="expected context"):
        run_runtime_verification(
            config,
            runner=FakeCommandRunner(),
            expected_context="other-context",
            gateway_url="https://gateway.example.internal",
            credentials={},
            http_probe=_MatrixProbe(),
        )


def test_default_http_probe_posts_openai_shape_and_parses_http_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class _Response:
        status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(
        req: request.Request,
        *,
        timeout: int,
        context: object | None = None,
    ) -> _Response:
        calls.append({"context": context, "headers": dict(req.header_items()), "timeout": timeout})
        return _Response()

    monkeypatch.setattr("airgap_ai_gateway.verification.request.urlopen", fake_urlopen)

    status, payload = default_http_probe(
        "https://gateway.example.internal/v1/chat/completions",
        host="qwen.ai.example.internal",
        api_key="example-only-do-not-use",
        kind=ModelKind.CHAT,
        verify_tls=False,
        timeout_seconds=3,
    )

    assert status == 200
    assert payload == {"ok": True}
    assert calls[0]["context"] is not None

    class _HttpError(Exception):
        code = 401

        def read(self) -> bytes:
            return b'{"error": "unauthorized"}'

    def fake_error_urlopen(
        req: request.Request,
        *,
        timeout: int,
        context: object | None = None,
    ) -> _Response:
        del req, timeout, context
        raise _HttpError

    monkeypatch.setattr("airgap_ai_gateway.verification.request.urlopen", fake_error_urlopen)

    status, payload = default_http_probe(
        "https://gateway.example.internal/v1/chat/completions",
        host="qwen.ai.example.internal",
        api_key=None,
        kind=ModelKind.CHAT,
        verify_tls=True,
        timeout_seconds=3,
    )

    assert status == 401
    assert payload == {"error": "unauthorized"}


def _execution_plan(resources: tuple[ResourceRef, ...]) -> ExecutionPlan:
    return ExecutionPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        plan_id="",
        command="deploy apply",
        platform="test",
        baseline="v1.3.1",
        overlay="test",
        namespace="ai-gateway",
        apply_mode="server-side-dry-run",
        expected_context=EXPECTED_CONTEXT,
        tls_verify=True,
        actions=(),
        resources=resources,
    ).with_computed_id()


def _runtime_responses(config: Any) -> dict[tuple[str, ...], CommandResult]:
    responses: dict[tuple[str, ...], CommandResult] = {
        ("kubectl", "config", "current-context"): CommandResult(0, EXPECTED_CONTEXT),
        (
            "kubectl",
            "-n",
            "ai-gateway",
            "get",
            "gateway",
            config.platform.gateway.name,
            "-o",
            "json",
        ): CommandResult(0, json.dumps(_conditions("Programmed"))),
        (
            "kubectl",
            "-n",
            "ai-gateway",
            "get",
            "deployment",
            "envoy-ratelimit",
            "-o",
            "json",
        ): CommandResult(0, json.dumps(_ready_deployment())),
        (
            "kubectl",
            "-n",
            "ai-gateway",
            "get",
            "deployment",
            "redis",
            "-o",
            "json",
        ): CommandResult(0, json.dumps(_ready_deployment())),
    }
    for model in config.models:
        responses[
            (
                "kubectl",
                "-n",
                "ai-gateway",
                "get",
                "httproute",
                f"route-{model.key}",
                "-o",
                "json",
            )
        ] = CommandResult(0, json.dumps(_route_conditions()))
        responses[
            (
                "kubectl",
                "-n",
                "ai-gateway",
                "get",
                "agentgatewaypolicy",
                f"policy-{model.key}",
                "-o",
                "json",
            )
        ] = CommandResult(0, json.dumps(_conditions("Accepted", "Attached")))
    return responses


def _conditions(*types: str) -> dict[str, object]:
    return {
        "status": {
            "conditions": [{"type": condition_type, "status": "True"} for condition_type in types]
        }
    }


def _route_conditions() -> dict[str, object]:
    return {
        "status": {
            "parents": [
                {
                    "conditions": [
                        {"type": "Accepted", "status": "True"},
                        {"type": "ResolvedRefs", "status": "True"},
                    ]
                }
            ]
        }
    }


def _ready_deployment() -> dict[str, object]:
    return {
        "metadata": {"generation": 1},
        "spec": {"replicas": 1},
        "status": {"availableReplicas": 1, "observedGeneration": 1},
    }


class _MatrixProbe:
    def __init__(self, *, embedding_vector: bool = True) -> None:
        self.embedding_vector = embedding_vector
        self.low_key_count = 0

    def __call__(
        self,
        url: str,
        *,
        host: str,
        api_key: str | None,
        kind: ModelKind,
        verify_tls: bool,
        timeout_seconds: int,
    ) -> tuple[int, dict[str, Any]]:
        del url, timeout_seconds
        assert verify_tls is True
        if host == "wrong.ai.example.internal":
            return 404, {}
        if api_key in {None, "unknown-key"}:
            return 401, {}
        if api_key == "low-key":
            self.low_key_count += 1
            return (429, {}) if self.low_key_count >= 3 else (200, {})
        if kind is ModelKind.EMBEDDING:
            if api_key != "rag-key":
                return 403, {}
            payload = (
                {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
                if self.embedding_vector
                else {"data": [{"object": "embedding"}]}
            )
            return 200, payload
        if "qwen" in host or "gemma" in host:
            return (200, {}) if api_key == "internal-key" else (403, {})
        return 404, {}
