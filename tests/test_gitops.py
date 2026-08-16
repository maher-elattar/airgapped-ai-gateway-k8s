from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from airgap_ai_gateway import gitops as gitops_module
from airgap_ai_gateway.cli import main
from airgap_ai_gateway.command import CommandResult
from airgap_ai_gateway.configuration import load_config
from airgap_ai_gateway.errors import PlanError
from airgap_ai_gateway.execution import FakeCommandRunner, execute_plan
from airgap_ai_gateway.gitops import (
    GITOPS_ENVIRONMENTS,
    REQUIRED_SYNC_OPTIONS,
    build_gitops_execution_plan,
    render_gitops,
    validate_gitops,
)
from airgap_ai_gateway.ledger import LedgerState, PreChangeSnapshot

EXAMPLE_CONFIG = Path("examples/config")
EXPECTED_CONTEXT = "kind-airgap-ai-gateway"
CONFIRMATION = "I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY"


@pytest.mark.parametrize("environment", GITOPS_ENVIRONMENTS)
def test_gitops_environment_validates(environment: str) -> None:
    assert validate_gitops(environment) == []


@pytest.mark.parametrize("environment", GITOPS_ENVIRONMENTS)
def test_argocd_application_points_to_managed_overlay(environment: str) -> None:
    rendered = render_gitops(environment)
    applications = [item for item in rendered.bootstrap if item["kind"] == "Application"]

    assert len(applications) == 1
    application = applications[0]
    spec = application["spec"]
    assert spec["source"]["repoURL"] == "https://github.com/ahmed658/airgap-ai-gateway-platform.git"
    assert spec["source"]["targetRevision"] == "main"
    assert spec["source"]["path"] == f"gitops/argocd/managed-overlays/{environment}"
    assert spec["destination"] == {
        "namespace": "ai-gateway",
        "server": "https://kubernetes.default.svc",
    }
    assert spec["syncPolicy"]["automated"] == {
        "allowEmpty": False,
        "prune": True,
        "selfHeal": True,
    }
    assert REQUIRED_SYNC_OPTIONS.issubset(set(spec["syncPolicy"]["syncOptions"]))


def test_argocd_project_is_least_privilege_and_secret_free() -> None:
    rendered = render_gitops("production-reference")
    project = next(item for item in rendered.bootstrap if item["kind"] == "AppProject")
    spec = project["spec"]

    assert spec["sourceRepos"] == ["https://github.com/ahmed658/airgap-ai-gateway-platform.git"]
    assert spec["destinations"] == [
        {"namespace": "ai-gateway", "server": "https://kubernetes.default.svc"}
    ]
    whitelisted_kinds = {item["kind"] for item in spec["namespaceResourceWhitelist"]}
    assert "Secret" not in whitelisted_kinds
    assert "*" not in whitelisted_kinds


@pytest.mark.parametrize("environment", GITOPS_ENVIRONMENTS)
def test_model_service_contracts_are_prune_guarded(environment: str) -> None:
    rendered = render_gitops(environment)
    service_contracts = [
        item
        for item in rendered.managed_overlay
        if item["kind"] == "Service"
        and item["metadata"]["labels"]["app.kubernetes.io/component"] == "nim-service-contract"
    ]

    assert {item["metadata"]["name"] for item in service_contracts} == {
        "embedding-nim",
        "gemma-nim",
        "qwen-nim",
    }
    assert all(
        item["metadata"]["annotations"]["argocd.argoproj.io/sync-options"] == "Prune=false"
        for item in service_contracts
    )


def test_gitops_plan_is_deterministic_and_offline() -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = build_gitops_execution_plan(config, environment="production-reference")
    second = build_gitops_execution_plan(config, environment="production-reference")

    assert plan.to_json() == second.to_json()
    assert plan.command == "gitops apply"
    assert plan.namespace == "argocd"
    assert "gitops/argocd/bootstrap/production-reference" in plan.to_json()
    assert "--dry-run=server" in plan.to_json()
    assert "kind: Secret" not in plan.to_json()

    live_plan = build_gitops_execution_plan(
        config,
        environment="production-reference",
        apply_mode="live",
    )
    assert "--dry-run=server" not in live_plan.to_json()


def test_gitops_refuses_unknown_environment_and_apply_mode() -> None:
    config = load_config(EXAMPLE_CONFIG)

    with pytest.raises(PlanError, match="unknown GitOps environment"):
        render_gitops("unknown")
    with pytest.raises(PlanError, match="unsupported apply mode"):
        build_gitops_execution_plan(
            config,
            environment="production-reference",
            apply_mode="client-side",
        )


def test_gitops_plan_refuses_failed_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(EXAMPLE_CONFIG)

    monkeypatch.setattr(gitops_module, "validate_gitops", lambda _environment: ["broken app"])

    with pytest.raises(PlanError, match="gitops plan refused"):
        build_gitops_execution_plan(config, environment="production-reference")


def test_gitops_apply_uses_existing_executor_safety_model() -> None:
    config = load_config(EXAMPLE_CONFIG)
    plan = build_gitops_execution_plan(config, environment="retained-nginx-edge")
    runner = FakeCommandRunner(
        {
            ("kubectl", "config", "current-context"): CommandResult(0, EXPECTED_CONTEXT),
            (
                "kubectl",
                "diff",
                "-k",
                "gitops/argocd/bootstrap/retained-nginx-edge",
            ): CommandResult(0, ""),
            (
                "kubectl",
                "apply",
                "--server-side",
                "--dry-run=server",
                "-k",
                "gitops/argocd/bootstrap/retained-nginx-edge",
            ): CommandResult(
                0, "application.argoproj.io/ai-gateway-retained-nginx-edge configured"
            ),
        },
        strict=True,
    )

    report = execute_plan(
        plan=plan,
        config=config,
        runner=runner,
        expected_context=EXPECTED_CONTEXT,
        apply_mode="server-side-dry-run",
        confirmation=CONFIRMATION,
        snapshot=PreChangeSnapshot.empty_ok(),
    )

    assert report.status == "succeeded"
    assert report.ledger is not None
    assert {entry.state for entry in report.ledger.entries} == {LedgerState.CREATED}


def test_cli_gitops_commands_work_without_cluster_access(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--config", str(EXAMPLE_CONFIG), "gitops", "validate"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"

    assert (
        main(
            [
                "--config",
                str(EXAMPLE_CONFIG),
                "gitops",
                "render",
                "--environment",
                "kind-demo",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "rendered"

    assert (
        main(
            [
                "--config",
                str(EXAMPLE_CONFIG),
                "gitops",
                "render",
                "--environment",
                "kind-demo",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    render_payload = json.loads(capsys.readouterr().out)
    assert Path(render_payload["bootstrap"]).exists()
    assert Path(render_payload["managed_overlay"]).exists()

    assert (
        main(
            [
                "--config",
                str(EXAMPLE_CONFIG),
                "gitops",
                "plan",
                "--environment",
                "kind-demo",
                "--output-dir",
                str(tmp_path / "plan"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "planned"


def test_cli_gitops_apply_is_safety_gated(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--config", str(EXAMPLE_CONFIG), "gitops", "apply"])

    assert code == 2
    assert "expected-context" in capsys.readouterr().err


def test_gitops_validator_reports_bootstrap_shape_errors() -> None:
    errors = gitops_module._validate_bootstrap(
        (
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "forbidden", "namespace": "argocd"},
            },
        ),
        "kind-demo",
    )

    assert any("expected exactly one AppProject" in error for error in errors)
    assert any("expected exactly one Application" in error for error in errors)
    assert any("must not render Secret" in error for error in errors)


def test_gitops_validator_reports_project_boundary_errors() -> None:
    project = _project()
    project["metadata"]["name"] = "wrong"
    project["metadata"]["namespace"] = "default"
    project["spec"]["sourceRepos"] = ["*"]
    project["spec"]["destinations"] = [{"server": "*", "namespace": "*"}]
    project["spec"]["namespaceResourceWhitelist"] = [
        {"group": "", "kind": "Secret"},
        {"group": "*", "kind": "*"},
    ]
    project["spec"]["clusterResourceWhitelist"] = [{"group": "*", "kind": "*"}]

    errors = gitops_module._validate_project(project)

    assert any("name must be" in error for error in errors)
    assert any("argocd namespace" in error for error in errors)
    assert any("sourceRepos" in error for error in errors)
    assert any("destination" in error for error in errors)
    assert any("Secret" in error for error in errors)
    assert any("namespace whitelist" in error for error in errors)
    assert any("cluster whitelist" in error for error in errors)


def test_gitops_validator_reports_application_boundary_errors() -> None:
    application = _application()
    application["metadata"]["namespace"] = "default"
    application["metadata"]["finalizers"] = ["resources-finalizer.argocd.argoproj.io"]
    application["spec"]["project"] = "wrong"
    application["spec"]["source"] = {
        "repoURL": "https://example.invalid/repo.git",
        "targetRevision": "develop",
        "path": "wrong",
    }
    application["spec"]["destination"] = {
        "server": "https://example.invalid",
        "namespace": "default",
    }
    application["spec"]["syncPolicy"] = {
        "automated": {"allowEmpty": True, "prune": False, "selfHeal": False},
        "syncOptions": [],
    }

    errors = gitops_module._validate_application(application, "kind-demo")

    assert any("argocd namespace" in error for error in errors)
    assert any("cascading" in error for error in errors)
    assert any("airgap-ai-gateway project" in error for error in errors)
    assert any("repoURL" in error for error in errors)
    assert any("targetRevision" in error for error in errors)
    assert any("source path" in error for error in errors)
    assert any("destination server" in error for error in errors)
    assert any("destination namespace" in error for error in errors)
    assert any("prune" in error for error in errors)
    assert any("selfHeal" in error for error in errors)
    assert any("allowEmpty" in error for error in errors)
    assert any("missing sync options" in error for error in errors)


def test_gitops_validator_reports_missing_prune_guards_and_bad_shapes() -> None:
    errors = gitops_module._validate_prune_guards(
        (
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "ignored", "namespace": "ai-gateway"},
            },
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": "qwen-nim",
                    "namespace": "ai-gateway",
                    "labels": {"app.kubernetes.io/component": "nim-service-contract"},
                    "annotations": {},
                },
            },
        )
    )

    assert any("qwen-nim" in error for error in errors)
    assert any("expected prune guards" in error for error in errors)

    with pytest.raises(PlanError, match="must be a mapping"):
        gitops_module._mapping(None, "bad")
    with pytest.raises(PlanError, match="must be a list"):
        gitops_module._sequence(None, "bad")


def _project() -> dict[str, Any]:
    rendered = render_gitops("production-reference")
    project = next(item for item in rendered.bootstrap if item["kind"] == "AppProject")
    return deepcopy(project)


def _application() -> dict[str, Any]:
    rendered = render_gitops("kind-demo")
    application = next(item for item in rendered.bootstrap if item["kind"] == "Application")
    return deepcopy(application)
