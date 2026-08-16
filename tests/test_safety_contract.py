from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from airgap_ai_gateway.command import CommandResult, describe_intent
from airgap_ai_gateway.configuration import load_config
from airgap_ai_gateway.discovery import (
    DiscoveryCandidate,
    discover,
    select_unique_candidate,
)
from airgap_ai_gateway.errors import (
    DiscoveryError,
    ExecutionError,
    PlanError,
    SafetyError,
    VerificationError,
)
from airgap_ai_gateway.execution import FakeCommandRunner, execute_plan
from airgap_ai_gateway.ledger import (
    LedgerEntry,
    LedgerState,
    PreChangeSnapshot,
    ResourceRef,
    StateLedger,
    ledger_from_resources,
)
from airgap_ai_gateway.manifest import build_overlay
from airgap_ai_gateway.planning import (
    ExecutionPlan,
    PlanAction,
    build_execution_plan,
    build_plan,
    build_rollback_execution_plan,
)
from airgap_ai_gateway.safety import ensure_mutation_is_confirmed, mutation_state
from airgap_ai_gateway.verification import verification_plan, verify_embedding_response

EXAMPLE_CONFIG = Path("examples/config")
EXPECTED_CONTEXT = "kind-airgap-ai-gateway"
CONFIRMATION = "I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY"


def test_safety_gate_allows_non_mutating_and_exact_mutating_command() -> None:
    config = load_config(EXAMPLE_CONFIG)

    ensure_mutation_is_confirmed(
        action="deploy plan",
        config=config,
        expected_context=None,
        confirmation=None,
    )
    ensure_mutation_is_confirmed(
        action="deploy apply",
        config=config,
        expected_context=EXPECTED_CONTEXT,
        confirmation=CONFIRMATION,
    )

    assert mutation_state("deploy apply").startswith("requires approved plan")
    assert mutation_state("deploy plan") == "offline plan"

    intent = describe_intent("render", ("airgap-ai-gateway", "render"), mutating=False)
    assert intent.label == "render"
    assert intent.argv == ("airgap-ai-gateway", "render")
    assert intent.mutating is False


def test_safety_gate_rejects_wrong_context_and_confirmation() -> None:
    config = load_config(EXAMPLE_CONFIG)

    with pytest.raises(SafetyError, match="expected-context"):
        ensure_mutation_is_confirmed(
            action="deploy apply",
            config=config,
            expected_context="wrong",
            confirmation=CONFIRMATION,
        )
    with pytest.raises(SafetyError, match="confirm"):
        ensure_mutation_is_confirmed(
            action="deploy apply",
            config=config,
            expected_context=EXPECTED_CONTEXT,
            confirmation="wrong",
        )


def test_discovery_report_and_candidate_selection_paths() -> None:
    config = load_config(EXAMPLE_CONFIG)
    report = discover(config)
    candidates = (
        DiscoveryCandidate("edge-a", "ingress", 10, "hostname match"),
        DiscoveryCandidate("edge-b", "ingress", 5, "label match"),
    )

    assert report.model_count == 3
    assert select_unique_candidate(candidates).name == "edge-a"
    assert select_unique_candidate(candidates, override="ingress/edge-b").name == "edge-b"

    with pytest.raises(DiscoveryError, match="no Ingress candidates"):
        select_unique_candidate((), field_name="Ingress")
    with pytest.raises(DiscoveryError, match="did not match"):
        select_unique_candidate(candidates, override="missing")


def test_verification_plan_and_embedding_response_edges() -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = verification_plan(config)

    assert plan["expected_context"] == EXPECTED_CONTEXT
    verify_embedding_response(200, {"data": [{"embedding": [0.1, 2]}]})

    for status, payload in (
        (500, {"error": "upstream"}),
        (200, {}),
        (200, {"data": ["bad"]}),
        (200, {"data": [{"embedding": ["bad"]}]}),
    ):
        with pytest.raises(VerificationError):
            verify_embedding_response(status, payload)


def test_resource_refs_snapshots_and_ledgers_round_trip(tmp_path: Path) -> None:
    namespace_ref = ResourceRef.from_manifest(
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "app"}}
    )
    cluster_ref = ResourceRef.from_manifest(
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "ai-gateway"}}
    )
    snapshot = PreChangeSnapshot(
        status="ok",
        resources={
            namespace_ref.identity: {
                "apiVersion": namespace_ref.api_version,
                "kind": namespace_ref.kind,
                "metadata": {"name": namespace_ref.name, "namespace": namespace_ref.namespace},
            }
        },
    )
    ledger = ledger_from_resources(
        resources=(namespace_ref, cluster_ref),
        snapshot=snapshot,
        run_id="run-1",
    )
    ledger_path = tmp_path / "ledger.json"
    snapshot_path = tmp_path / "snapshot.json"

    ledger.write(ledger_path)
    snapshot_path.write_text(json.dumps(snapshot.to_dict()), encoding="utf-8")

    loaded_ledger = StateLedger.from_file(ledger_path)
    loaded_snapshot = PreChangeSnapshot.from_file(snapshot_path)
    states = {entry.ref.identity: entry.state for entry in loaded_ledger.entries}

    assert namespace_ref.namespace == "default"
    assert cluster_ref.namespace == ""
    assert loaded_snapshot.resources == snapshot.resources
    assert states[namespace_ref.identity] == LedgerState.UPDATED
    assert states[cluster_ref.identity] == LedgerState.CREATED
    assert loaded_ledger.for_run("missing").entries == ()


def test_invalid_ledger_and_snapshot_inputs_fail_closed(tmp_path: Path) -> None:
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("[]", encoding="utf-8")

    for manifest in (
        {},
        {"apiVersion": "v1", "metadata": {"name": "bad"}},
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": ""}},
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "bad", "namespace": ""}},
    ):
        with pytest.raises(ExecutionError):
            ResourceRef.from_manifest(manifest)

    with pytest.raises(ExecutionError, match="snapshot resources"):
        PreChangeSnapshot.from_dict({"status": "ok", "resources": []})
    with pytest.raises(ExecutionError, match="snapshot resource entries"):
        PreChangeSnapshot.from_dict({"status": "ok", "resources": {"bad": []}})
    with pytest.raises(ExecutionError, match="pre-change snapshot must be a JSON object"):
        PreChangeSnapshot.from_file(invalid_json)
    with pytest.raises(ExecutionError, match="state ledger must be a JSON object"):
        StateLedger.from_file(invalid_json)
    with pytest.raises(ExecutionError, match="ledger entries must be a list"):
        StateLedger.from_dict({"entries": {}})
    with pytest.raises(ExecutionError, match="ledger entries must be objects"):
        StateLedger.from_dict({"entries": ["bad"]})
    with pytest.raises(ExecutionError, match="ledger entry ref"):
        LedgerEntry.from_dict({"state": "created", "run_id": "run-1"})
    with pytest.raises(ExecutionError, match="expected string"):
        ResourceRef.from_dict(
            {"apiVersion": "v1", "kind": "ConfigMap", "namespace": 7, "name": "x"}
        )


def test_execution_plan_round_trip_and_validation_errors(tmp_path: Path) -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = build_execution_plan(config, command="cutover apply", apply_mode="live")
    path = tmp_path / "plan.json"
    path.write_text(plan.to_json(), encoding="utf-8")
    loaded = ExecutionPlan.from_file(path)

    assert loaded.to_json() == plan.to_json()
    assert any(action.kind == "read-ingress-state" for action in plan.actions)
    assert any(
        action.kind == "kubectl-apply" and "--dry-run=server" not in action.command
        for action in plan.actions
    )

    with pytest.raises(ExecutionError, match="plan file does not exist"):
        ExecutionPlan.from_file(tmp_path / "missing.json")
    bad_path = tmp_path / "bad-plan.json"
    bad_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ExecutionError, match="plan file must contain"):
        ExecutionPlan.from_file(bad_path)
    with pytest.raises(ExecutionError, match="plan actions must be a list"):
        ExecutionPlan.from_dict({**plan.to_dict(), "actions": {}})
    with pytest.raises(ExecutionError, match="plan resources must be a list"):
        ExecutionPlan.from_dict({**plan.to_dict(), "resources": {}})
    with pytest.raises(ExecutionError, match="plan notes must be a list"):
        ExecutionPlan.from_dict({**plan.to_dict(), "notes": [7]})
    with pytest.raises(ExecutionError, match="plan actions must be objects"):
        ExecutionPlan.from_dict({**plan.to_dict(), "actions": ["bad"]})
    with pytest.raises(ExecutionError, match="plan resources must be objects"):
        ExecutionPlan.from_dict({**plan.to_dict(), "resources": ["bad"]})


def test_plan_action_parsing_and_planner_error_paths() -> None:
    config = load_config(EXAMPLE_CONFIG)
    resource = ResourceRef("v1", "ConfigMap", "ai-gateway", "example")
    action = PlanAction.from_dict(
        {
            "id": "a",
            "kind": "kubectl-delete-resource",
            "description": "delete",
            "command": ["kubectl", "delete", "configmap", "example"],
            "mutating": True,
            "resource": resource.to_dict(),
            "payload": {"x": "y"},
            "sensitive_output": False,
        }
    )

    assert action.resource == resource
    assert build_plan(config, "consumer add").to_dict()["state"] == "offline plan"

    with pytest.raises(ExecutionError, match="plan action command"):
        PlanAction.from_dict(
            {"id": "bad", "kind": "x", "description": "bad", "command": ["kubectl", 7]}
        )
    with pytest.raises(PlanError, match="unsupported apply mode"):
        build_execution_plan(config, command="deploy apply", apply_mode="client")
    with pytest.raises(PlanError, match="unknown overlay"):
        build_execution_plan(config, command="deploy apply", overlay="missing")
    with pytest.raises(PlanError, match="not supported"):
        build_execution_plan(config, command="verify")


def test_destroy_plan_and_cluster_scoped_rollback_delete() -> None:
    config = load_config(EXAMPLE_CONFIG)
    destroy_plan = build_execution_plan(config, command="destroy apply")
    namespace_ref = ResourceRef("v1", "Namespace", "", "temporary")
    ledger = StateLedger((LedgerEntry(namespace_ref, LedgerState.CREATED, "run-1"),))
    rollback_plan = build_rollback_execution_plan(config, ledger=ledger, run_id="run-1")
    delete_action = next(
        action for action in rollback_plan.actions if action.kind.endswith("resource")
    )

    assert any(action.kind == "kubectl-delete" for action in destroy_plan.actions)
    assert delete_action.command == (
        "kubectl",
        "delete",
        "Namespace.v1/temporary",
        "--ignore-not-found=false",
    )
    with pytest.raises(PlanError, match="no ledger entries"):
        build_rollback_execution_plan(config, ledger=ledger, run_id="missing")
    with pytest.raises(PlanError, match="has no pre-change resource"):
        build_rollback_execution_plan(
            config,
            ledger=StateLedger((LedgerEntry(namespace_ref, LedgerState.UPDATED, "run-1"),)),
            run_id="run-1",
        )


def test_executor_rejects_tampered_inputs_and_unapproved_action_sequences() -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = build_execution_plan(config, command="destroy apply")
    runner = FakeCommandRunner(
        {("kubectl", "config", "current-context"): CommandResult(0, EXPECTED_CONTEXT)}
    )

    with pytest.raises(ExecutionError, match="plan_id"):
        execute_plan(
            plan=replace(plan, plan_id="tampered"),
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
        )
    with pytest.raises(SafetyError, match="apply mode"):
        execute_plan(
            plan=plan,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="live",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
        )
    with pytest.raises(SafetyError, match="confirmation"):
        execute_plan(
            plan=plan,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation="wrong",
            snapshot=PreChangeSnapshot.empty_ok(),
        )

    mutating_first = replace(
        plan, actions=tuple(action for action in plan.actions if action.mutating)
    ).with_computed_id()
    with pytest.raises(SafetyError, match="before context verification"):
        execute_plan(
            plan=mutating_first,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
        )


def test_executor_action_error_paths() -> None:
    config = load_config(EXAMPLE_CONFIG)
    verify = PlanAction(
        id="verify-context",
        kind="verify-context",
        description="verify",
        command=("kubectl", "config", "current-context"),
    )
    unsupported = PlanAction(id="unsupported", kind="unsupported", description="unsupported")
    bad_poll = PlanAction(id="bad-poll", kind="poll-gateway", description="bad")
    failing_apply = PlanAction(
        id="apply",
        kind="kubectl-apply",
        description="apply",
        command=("kubectl", "apply", "-f", "x"),
        mutating=True,
    )
    base = build_execution_plan(config, command="destroy apply")

    unsupported_plan = replace(base, actions=(verify, unsupported)).with_computed_id()
    bad_poll_plan = replace(base, actions=(verify, bad_poll)).with_computed_id()
    failing_plan = replace(base, actions=(verify, failing_apply)).with_computed_id()
    runner = FakeCommandRunner(
        {("kubectl", "config", "current-context"): CommandResult(0, EXPECTED_CONTEXT)}
    )

    with pytest.raises(ExecutionError, match="unsupported plan action"):
        execute_plan(
            plan=unsupported_plan,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
        )
    with pytest.raises(ExecutionError, match="requires namespace and name"):
        execute_plan(
            plan=bad_poll_plan,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
        )
    with pytest.raises(ExecutionError, match="failed with return code"):
        execute_plan(
            plan=failing_plan,
            config=config,
            runner=FakeCommandRunner(
                {
                    ("kubectl", "config", "current-context"): CommandResult(0, EXPECTED_CONTEXT),
                    ("kubectl", "apply", "-f", "x"): CommandResult(1, stderr="denied"),
                }
            ),
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
        )


def test_rollback_restore_requires_matching_ledger_entry() -> None:
    config = load_config(EXAMPLE_CONFIG)
    ref = ResourceRef("v1", "ConfigMap", "ai-gateway", "restore-me")
    ledger = StateLedger(
        (
            LedgerEntry(
                ref=ref,
                state=LedgerState.UPDATED,
                run_id="run-1",
                before={
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": ref.name, "namespace": ref.namespace},
                },
            ),
        )
    )
    plan = build_rollback_execution_plan(config, ledger=ledger, run_id="run-1")
    bad_identity_action = replace(
        plan.actions[1],
        payload={"restore_identity": "v1/ConfigMap/ai-gateway/missing"},
    )
    bad_identity_plan = replace(
        plan, actions=(plan.actions[0], bad_identity_action)
    ).with_computed_id()
    no_identity_action = replace(plan.actions[1], payload={})
    no_identity_plan = replace(
        plan, actions=(plan.actions[0], no_identity_action)
    ).with_computed_id()
    runner = FakeCommandRunner(
        {("kubectl", "config", "current-context"): CommandResult(0, EXPECTED_CONTEXT)}
    )

    with pytest.raises(ExecutionError, match="requires the saved state ledger"):
        execute_plan(
            plan=plan,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
        )
    with pytest.raises(ExecutionError, match="no ledger identity"):
        execute_plan(
            plan=no_identity_plan,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
            ledger=ledger,
        )
    with pytest.raises(ExecutionError, match="could not find pre-change resource"):
        execute_plan(
            plan=bad_identity_plan,
            config=config,
            runner=runner,
            expected_context=EXPECTED_CONTEXT,
            apply_mode="server-side-dry-run",
            confirmation=CONFIRMATION,
            snapshot=PreChangeSnapshot.empty_ok(),
            ledger=ledger,
        )


def test_fake_runner_strict_mode_and_manifest_resource_coverage() -> None:
    runner = FakeCommandRunner(strict=True)

    with pytest.raises(ExecutionError, match="unexpected command"):
        runner.run(("kubectl", "version"))

    refs = tuple(ResourceRef.from_manifest(item) for item in build_overlay("kind-demo"))
    assert any(ref.kind == "HTTPRoute" for ref in refs)
    assert any(ref.kind == "Namespace" and ref.namespace == "" for ref in refs)
