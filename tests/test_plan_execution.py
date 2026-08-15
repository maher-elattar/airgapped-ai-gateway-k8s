from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from airgap_ai_gateway.command import CommandResult
from airgap_ai_gateway.configuration import load_config, validate_config
from airgap_ai_gateway.discovery import DiscoveryCandidate, select_unique_candidate
from airgap_ai_gateway.errors import (
    ConfigError,
    DiscoveryError,
    ExecutionError,
    PlanError,
    SafetyError,
    VerificationError,
)
from airgap_ai_gateway.execution import (
    FakeCommandRunner,
    execute_plan,
    interpret_kubectl_diff,
)
from airgap_ai_gateway.ledger import (
    LedgerEntry,
    LedgerState,
    PreChangeSnapshot,
    ResourceRef,
    StateLedger,
)
from airgap_ai_gateway.models import RegistryImage
from airgap_ai_gateway.onboarding import render_chat_model_onboarding
from airgap_ai_gateway.planning import (
    ExecutionPlan,
    build_execution_plan,
    build_rollback_execution_plan,
    write_plan_files,
)
from airgap_ai_gateway.reporting import to_json
from airgap_ai_gateway.verification import HttpProbeSpec, verify_embedding_response

EXAMPLE_CONFIG = Path("examples/config")
EXPECTED_CONTEXT = "kind-airgap-ai-gateway"
CONFIRMATION = "I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY"
SECRET_VALUE = "example-secret-value-do-not-leak"


def test_plan_files_are_deterministic_and_offline(tmp_path: Path) -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = build_execution_plan(config, command="deploy apply")
    second = build_execution_plan(config, command="deploy apply")

    assert plan.to_json() == second.to_json()
    assert "sk-" not in plan.to_json()
    assert "credential_placeholder" not in plan.to_json()

    json_path, markdown_path = write_plan_files(plan, tmp_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["plan_id"] == plan.plan_id
    markdown = markdown_path.read_text(encoding="utf-8")
    assert plan.plan_id in markdown
    assert "Public TLS verification: `enabled`" in markdown


def test_chat_model_onboarding_does_not_duplicate_api_version() -> None:
    config = load_config(EXAMPLE_CONFIG)
    model = next(item for item in config.models if item.key == "qwen-chat")
    rendered = render_chat_model_onboarding(model, namespace=config.platform.gateway.namespace)

    documents = [section for section in rendered.split("---") if section.strip()]
    assert len(documents) == 3
    assert all(section.count("apiVersion:") == 1 for section in documents)
    assert [item["kind"] for item in yaml.safe_load_all(rendered) if item] == [
        "AgentgatewayBackend",
        "HTTPRoute",
        "AgentgatewayPolicy",
    ]


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [
        (0, "no_changes"),
        (1, "changes_present"),
    ],
)
def test_kubectl_diff_return_code_semantics(returncode: int, expected: str) -> None:
    assert interpret_kubectl_diff(CommandResult(returncode)) == expected


def test_kubectl_diff_return_code_two_blocks_execution() -> None:
    with pytest.raises(ExecutionError, match="kubectl diff failed"):
        interpret_kubectl_diff(CommandResult(2, stderr="schema failure"))


def test_tied_discovery_candidates_fail_with_required_override() -> None:
    candidates = (
        DiscoveryCandidate("edge-a", "ingress", 10, "hostname match"),
        DiscoveryCandidate("edge-b", "ingress", 10, "hostname match"),
    )

    with pytest.raises(DiscoveryError) as error:
        select_unique_candidate(candidates, field_name="Ingress")

    message = str(error.value)
    assert "ingress/edge-a" in message
    assert "ingress/edge-b" in message
    assert "provide an explicit Ingress override" in message


def test_backup_failure_blocks_apply_before_any_command() -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = build_execution_plan(config, command="deploy apply")
    runner = FakeCommandRunner()

    with pytest.raises(ExecutionError, match="pre-change snapshot"):
        execute_plan(
            plan=plan,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot(status="failed", error="backup unavailable"),
        )

    assert runner.calls == []


def test_deploy_apply_creates_state_ledger_from_snapshot() -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = build_execution_plan(config, command="deploy apply")
    pre_existing = plan.resources[0]
    snapshot = PreChangeSnapshot(
        status="ok",
        resources={
            pre_existing.identity: {
                "apiVersion": pre_existing.api_version,
                "kind": pre_existing.kind,
                "metadata": {"name": pre_existing.name, "namespace": pre_existing.namespace},
            }
        },
    )
    runner = FakeCommandRunner(_responses_for_plan(plan))

    report = execute_plan(
        plan=plan,
        config=config,
        runner=runner,
        expected_context=EXPECTED_CONTEXT,
        apply_mode="server-side-dry-run",
        confirmation=CONFIRMATION,
        snapshot=snapshot,
    )

    assert report.ledger is not None
    states = {entry.ref.identity: entry.state for entry in report.ledger.entries}
    assert states[pre_existing.identity] == LedgerState.UPDATED
    assert LedgerState.CREATED in states.values()


def test_secret_content_is_redacted_from_reports_markdown_and_commands_log(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = load_config(EXAMPLE_CONFIG)
    ledger = _rollback_ledger()
    plan = build_rollback_execution_plan(
        config,
        ledger=ledger,
        run_id="run-1",
        apply_mode="server-side-dry-run",
    )
    commands_log = tmp_path / "commands.log"
    runner = FakeCommandRunner(
        {
            ("kubectl", "config", "current-context"): CommandResult(0, EXPECTED_CONTEXT),
            ("kubectl", "apply", "-f", "-"): CommandResult(
                0,
                stdout=f"restored {SECRET_VALUE}",
                stderr=f"warning {SECRET_VALUE}",
            ),
            (
                "kubectl",
                "-n",
                "ai-gateway",
                "delete",
                "ConfigMap.v1/created-by-run",
                "--ignore-not-found=false",
            ): CommandResult(0, stdout=f"deleted {SECRET_VALUE}"),
        }
    )

    report = execute_plan(
        plan=plan,
        config=config,
        runner=runner,
        expected_context=EXPECTED_CONTEXT,
        apply_mode="server-side-dry-run",
        confirmation=CONFIRMATION,
        snapshot=PreChangeSnapshot.empty_ok(),
        ledger=ledger,
        commands_log_path=commands_log,
    )

    json_report = to_json(report.to_dict())
    print(json_report)
    console = capsys.readouterr().out
    assert SECRET_VALUE not in plan.to_json()
    assert SECRET_VALUE not in plan.to_markdown()
    assert SECRET_VALUE not in json_report
    assert SECRET_VALUE not in console
    assert SECRET_VALUE not in commands_log.read_text(encoding="utf-8")


def test_dry_run_plan_does_not_generate_credentials() -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = build_execution_plan(config, command="deploy apply", apply_mode="server-side-dry-run")

    rendered = plan.to_json()
    assert "REPLACE_AT_RUNTIME" not in rendered
    assert "example-only-do-not-use" not in rendered
    assert "sk-" not in rendered
    assert "--dry-run=server" in rendered


def test_public_tls_verification_is_enabled_by_default() -> None:
    assert HttpProbeSpec(url="https://qwen.ai.example.internal/v1/chat/completions").verify_tls


def test_skip_ratelimit_conflicts_with_enabled_policy() -> None:
    config = load_config(EXAMPLE_CONFIG)

    with pytest.raises(PlanError, match="skip-ratelimit conflicts"):
        build_execution_plan(config, command="deploy apply", skip_ratelimit=True)


def test_embedding_200_without_vector_fails_verification() -> None:
    with pytest.raises(VerificationError, match="vector"):
        verify_embedding_response(200, {"data": [{"object": "embedding"}]})


def test_rollback_restores_pre_existing_secret_and_does_not_delete_gateway_or_policy() -> None:
    config = load_config(EXAMPLE_CONFIG)
    ledger = _rollback_ledger()
    plan = build_rollback_execution_plan(config, ledger=ledger, run_id="run-1")

    restore_resources = {
        action.resource.kind
        for action in plan.actions
        if action.kind == "kubectl-restore" and action.resource is not None
    }
    deleted_resources = {
        action.resource.kind
        for action in plan.actions
        if action.kind == "kubectl-delete-resource" and action.resource is not None
    }

    assert {"Secret", "Gateway", "AgentgatewayPolicy"} <= restore_resources
    assert "Gateway" not in deleted_resources
    assert "AgentgatewayPolicy" not in deleted_resources
    assert deleted_resources == {"ConfigMap"}


def test_cleanup_aborts_when_ingress_state_cannot_be_read() -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = build_execution_plan(config, command="destroy apply")
    delete_commands = [action.command for action in plan.actions if action.kind == "kubectl-delete"]
    assert delete_commands
    runner = FakeCommandRunner(
        {
            ("kubectl", "config", "current-context"): CommandResult(0, EXPECTED_CONTEXT),
            ("kubectl", "-n", "ai-gateway", "get", "ingress", "-o", "json"): CommandResult(
                1,
                stderr="forbidden",
            ),
        }
    )

    with pytest.raises(ExecutionError, match="Ingress state could not be read"):
        execute_plan(
            plan=plan,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
        )

    assert delete_commands[0] not in runner.calls


def test_exact_context_mismatch_blocks_before_mutation() -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = build_execution_plan(config, command="destroy apply")
    runner = FakeCommandRunner(
        {("kubectl", "config", "current-context"): CommandResult(0, "other-context\n")}
    )

    with pytest.raises(SafetyError, match="context mismatch"):
        execute_plan(
            plan=plan,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
        )

    assert len(runner.calls) == 1


def test_public_image_or_mutable_tag_is_rejected() -> None:
    config = load_config(EXAMPLE_CONFIG)
    invalid_registry = replace(
        config.platform.registry,
        images=(
            RegistryImage(
                name="bad",
                source="registry.example.invalid/bad:latest",
                target="docker.io/library/bad:latest",
            ),
        ),
    )
    invalid = replace(config, platform=replace(config.platform, registry=invalid_registry))

    with pytest.raises(ConfigError) as error:
        validate_config(invalid)

    message = str(error.value)
    assert "private registry" in message
    assert "mutable latest tag" in message


def _responses_for_plan(execution_plan: ExecutionPlan) -> dict[tuple[str, ...], CommandResult]:
    responses: dict[tuple[str, ...], CommandResult] = {
        ("kubectl", "config", "current-context"): CommandResult(0, EXPECTED_CONTEXT)
    }
    for action in execution_plan.actions:
        if action.kind == "kubectl-diff":
            responses[action.command] = CommandResult(0)
        elif action.kind == "kubectl-apply":
            responses[action.command] = CommandResult(0, stdout="applied")
        elif action.kind == "poll-gateway":
            payload = action.payload or {}
            responses[
                (
                    "kubectl",
                    "-n",
                    str(payload["namespace"]),
                    "get",
                    "gateway",
                    str(payload["name"]),
                    "-o",
                    "json",
                )
            ] = CommandResult(0, stdout=json.dumps(_conditions("Programmed")))
        elif action.kind == "poll-httproute":
            payload = action.payload or {}
            responses[
                (
                    "kubectl",
                    "-n",
                    str(payload["namespace"]),
                    "get",
                    "httproute",
                    str(payload["name"]),
                    "-o",
                    "json",
                )
            ] = CommandResult(
                0,
                stdout=json.dumps(
                    {
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
                ),
            )
        elif action.kind == "poll-policy":
            payload = action.payload or {}
            responses[
                (
                    "kubectl",
                    "-n",
                    str(payload["namespace"]),
                    "get",
                    "agentgatewaypolicy",
                    str(payload["name"]),
                    "-o",
                    "json",
                )
            ] = CommandResult(0, stdout=json.dumps(_conditions("Accepted", "Attached")))
        elif action.kind == "poll-deployment":
            payload = action.payload or {}
            responses[
                (
                    "kubectl",
                    "-n",
                    str(payload["namespace"]),
                    "get",
                    "deployment",
                    str(payload["name"]),
                    "-o",
                    "json",
                )
            ] = CommandResult(
                0,
                stdout=json.dumps(
                    {
                        "metadata": {"generation": 2},
                        "spec": {"replicas": 1},
                        "status": {"availableReplicas": 1, "observedGeneration": 2},
                    }
                ),
            )
    return responses


def _conditions(*types: str) -> dict[str, object]:
    return {
        "status": {
            "conditions": [{"type": condition_type, "status": "True"} for condition_type in types]
        }
    }


def _rollback_ledger() -> StateLedger:
    secret_ref = ResourceRef("v1", "Secret", "ai-gateway", "agentgateway-consumer-keys")
    gateway_ref = ResourceRef(
        "gateway.networking.k8s.io/v1",
        "Gateway",
        "ai-gateway",
        "ai-gateway",
    )
    policy_ref = ResourceRef(
        "agentgateway.dev/v1alpha1",
        "AgentgatewayPolicy",
        "ai-gateway",
        "policy-qwen-chat",
    )
    created_ref = ResourceRef("v1", "ConfigMap", "ai-gateway", "created-by-run")
    other_ref = ResourceRef("v1", "ConfigMap", "ai-gateway", "other-run")
    return StateLedger(
        (
            LedgerEntry(
                ref=secret_ref,
                state=LedgerState.UPDATED,
                run_id="run-1",
                before={
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": secret_ref.name, "namespace": secret_ref.namespace},
                    "stringData": {"api-key": SECRET_VALUE},
                },
            ),
            LedgerEntry(
                ref=gateway_ref,
                state=LedgerState.UPDATED,
                run_id="run-1",
                before={
                    "apiVersion": gateway_ref.api_version,
                    "kind": gateway_ref.kind,
                    "metadata": {"name": gateway_ref.name, "namespace": gateway_ref.namespace},
                },
            ),
            LedgerEntry(
                ref=policy_ref,
                state=LedgerState.PRE_EXISTING,
                run_id="run-1",
                before={
                    "apiVersion": policy_ref.api_version,
                    "kind": policy_ref.kind,
                    "metadata": {"name": policy_ref.name, "namespace": policy_ref.namespace},
                },
            ),
            LedgerEntry(ref=created_ref, state=LedgerState.CREATED, run_id="run-1"),
            LedgerEntry(ref=other_ref, state=LedgerState.CREATED, run_id="run-2"),
        )
    )
