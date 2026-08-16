from __future__ import annotations

import json
from pathlib import Path

import pytest

from airgap_ai_gateway.cli import main

EXAMPLE_CONFIG = Path("examples/config")
EXPECTED_CONTEXT = "kind-airgap-ai-gateway"
CONFIRMATION = "I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY"


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["discover", "--help"],
        ["render", "--help"],
        ["bundle", "build", "--help"],
        ["bundle", "verify", "--help"],
        ["registry", "promote", "--help"],
        ["deploy", "plan", "--help"],
        ["deploy", "apply", "--help"],
        ["verify", "--help"],
        ["cutover", "plan", "--help"],
        ["cutover", "apply", "--help"],
        ["rollback", "plan", "--help"],
        ["rollback", "apply", "--help"],
        ["model", "add", "--help"],
        ["consumer", "add", "--help"],
        ["consumer", "rotate", "--help"],
        ["consumer", "revoke", "--help"],
        ["destroy", "plan", "--help"],
        ["destroy", "apply", "--help"],
        ["gitops", "--help"],
        ["gitops", "render", "--help"],
        ["gitops", "validate", "--help"],
        ["gitops", "plan", "--help"],
        ["gitops", "apply", "--help"],
    ],
)
def test_cli_help_works_for_every_planned_command(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(args)

    assert error.value.code == 0


def test_deploy_apply_refuses_missing_expected_context(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--config", str(EXAMPLE_CONFIG), "deploy", "apply"])

    assert code == 2
    assert "expected-context" in capsys.readouterr().err


def test_deploy_apply_refuses_missing_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--config",
            str(EXAMPLE_CONFIG),
            "deploy",
            "apply",
            "--expected-context",
            EXPECTED_CONTEXT,
        ]
    )

    assert code == 2
    assert "confirm" in capsys.readouterr().err


def test_apply_command_refuses_without_saved_plan_after_safety_inputs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "--config",
            str(EXAMPLE_CONFIG),
            "deploy",
            "apply",
            "--expected-context",
            EXPECTED_CONTEXT,
            "--confirm",
            CONFIRMATION,
            "--apply-mode",
            "server-side-dry-run",
        ]
    )

    error = capsys.readouterr().err
    assert code == 2
    assert "plan-file" in error


def test_cutover_rollback_and_destroy_are_safety_gated(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for command in ("cutover", "rollback", "destroy"):
        code = main(["--config", str(EXAMPLE_CONFIG), command, "apply"])
        assert code == 2
        assert "refused" in capsys.readouterr().err


def test_cli_model_add_plan_outputs_lifecycle_plan(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--config",
            str(EXAMPLE_CONFIG),
            "model",
            "add",
            "plan",
            "--model-key",
            "cli-chat",
            "--display-name",
            "CLI Chat",
            "--host",
            "cli-chat.ai.example.internal",
            "--route-path",
            "/v1/cli-chat/completions",
            "--permission",
            "model:cli-chat:invoke",
            "--service-name",
            "cli-chat-nim",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["action"] == "model add"
    assert payload["plan_id"]
    assert any("cli-chat.yaml" in change["path"] for change in payload["changes"])


def test_cli_consumer_add_plan_outputs_lifecycle_plan(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--config",
            str(EXAMPLE_CONFIG),
            "consumer",
            "add",
            "plan",
            "--consumer-key",
            "cli-consumer",
            "--display-name",
            "CLI Consumer",
            "--allowed-model",
            "qwen-chat",
            "--requests-per-minute",
            "9",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["action"] == "consumer add"
    assert payload["plan_id"]
    assert "REPLACE_AT_RUNTIME" in json.dumps(payload)


def test_cli_static_commands_emit_offline_reports(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--config", str(EXAMPLE_CONFIG), "discover"]) == 0
    discover_payload = json.loads(capsys.readouterr().out)
    assert discover_payload["status"].startswith("offline-discovery")

    assert main(["--config", str(EXAMPLE_CONFIG), "render"]) == 0
    render_payload = json.loads(capsys.readouterr().out)
    assert render_payload["status"] == "rendered-fake-only"

    assert (
        main(
            [
                "--config",
                str(EXAMPLE_CONFIG),
                "deploy",
                "plan",
                "--output-dir",
                str(tmp_path / "deploy-plan"),
            ]
        )
        == 0
    )
    deploy_payload = json.loads(capsys.readouterr().out)
    assert deploy_payload["status"] == "planned"

    assert main(["--config", str(EXAMPLE_CONFIG), "rollback", "plan"]) == 0
    rollback_payload = json.loads(capsys.readouterr().out)
    assert rollback_payload["status"] == "rollback-plan-skeleton"


def test_cli_runtime_verify_requires_context_and_gateway_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--config", str(EXAMPLE_CONFIG), "verify", "runtime"])

    assert code == 2
    assert "expected-context" in capsys.readouterr().err

    code = main(
        [
            "--config",
            str(EXAMPLE_CONFIG),
            "verify",
            "runtime",
            "--expected-context",
            EXPECTED_CONTEXT,
        ]
    )

    assert code == 2
    assert "gateway-url" in capsys.readouterr().err
