from __future__ import annotations

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
