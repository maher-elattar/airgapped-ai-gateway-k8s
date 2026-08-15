"""Approved-plan execution boundary."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import yaml

from airgap_ai_gateway.command import CommandResult
from airgap_ai_gateway.conditions import (
    poll_deployment_ready,
    poll_gateway_programmed,
    poll_httproute_ready,
    poll_policy_ready,
)
from airgap_ai_gateway.errors import ExecutionError, SafetyError
from airgap_ai_gateway.ledger import PreChangeSnapshot, StateLedger, ledger_from_resources
from airgap_ai_gateway.models import GatewayConfig
from airgap_ai_gateway.planning import ExecutionPlan, PlanAction
from airgap_ai_gateway.redaction import REDACTED, redact_mapping, redact_text
from airgap_ai_gateway.safety import MUTATING_ACTIONS


class CommandRunner(Protocol):
    """Command runner interface used by the executor."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        sensitive_output: bool = False,
    ) -> CommandResult:
        """Run a command and return the captured result."""


@dataclass(frozen=True, slots=True)
class CommandLogEntry:
    """One command recorded without preserving secret output."""

    action_id: str
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    sensitive_output: bool

    def to_dict(self) -> dict[str, object]:
        """Return a redacted JSON object."""

        stdout = REDACTED if self.sensitive_output else redact_text(self.stdout)
        stderr = REDACTED if self.sensitive_output else redact_text(self.stderr)
        return {
            "action_id": self.action_id,
            "argv": [redact_text(item) for item in self.argv],
            "returncode": self.returncode,
            "stderr": stderr,
            "stdout": stdout,
        }


@dataclass(slots=True)
class CommandLog:
    """Append-only in-memory command log."""

    entries: list[CommandLogEntry]

    def __init__(self) -> None:
        self.entries = []

    def append(
        self,
        *,
        action_id: str,
        argv: tuple[str, ...],
        result: CommandResult,
        sensitive_output: bool,
    ) -> None:
        """Record one command result."""

        self.entries.append(
            CommandLogEntry(
                action_id=action_id,
                argv=argv,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                sensitive_output=sensitive_output,
            )
        )

    def to_jsonl(self) -> str:
        """Serialize the log as redacted JSON Lines."""

        return "".join(json.dumps(entry.to_dict(), sort_keys=True) + "\n" for entry in self.entries)

    def write(self, path: Path) -> None:
        """Write the redacted command log."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_jsonl(), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Execution outcome for one plan action."""

    action_id: str
    kind: str
    status: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    sensitive_output: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a redacted JSON object."""

        stdout = REDACTED if self.sensitive_output else redact_text(self.stdout)
        stderr = REDACTED if self.sensitive_output else redact_text(self.stderr)
        return {
            "action_id": self.action_id,
            "kind": self.kind,
            "returncode": self.returncode,
            "status": self.status,
            "stderr": stderr,
            "stdout": stdout,
        }


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Deterministic, redacted execution report."""

    status: str
    plan_id: str
    command: str
    apply_mode: str
    results: tuple[ActionResult, ...]
    ledger: StateLedger | None

    def to_dict(self) -> dict[str, object]:
        """Return a redacted report payload."""

        payload = {
            "apply_mode": self.apply_mode,
            "command": self.command,
            "ledger": self.ledger.to_dict() if self.ledger else None,
            "plan_id": self.plan_id,
            "results": [result.to_dict() for result in self.results],
            "status": self.status,
        }
        return cast(dict[str, object], redact_mapping(payload))


class FakeCommandRunner:
    """Deterministic command runner for unit tests."""

    def __init__(
        self,
        responses: Mapping[tuple[str, ...], CommandResult | Sequence[CommandResult]] | None = None,
        *,
        default: CommandResult | None = None,
        strict: bool = False,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.inputs: list[str | None] = []
        self.sensitive_flags: list[bool] = []
        self._responses: dict[tuple[str, ...], list[CommandResult]] = {}
        for command, response in (responses or {}).items():
            if isinstance(response, CommandResult):
                self._responses[command] = [response]
            else:
                self._responses[command] = list(response)
        self._default = default or CommandResult(0, "{}\n", "")
        self._strict = strict

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        sensitive_output: bool = False,
    ) -> CommandResult:
        """Return a configured fake response."""

        self.calls.append(argv)
        self.inputs.append(input_text)
        self.sensitive_flags.append(sensitive_output)
        responses = self._responses.get(argv)
        if responses:
            if len(responses) > 1:
                return responses.pop(0)
            return responses[0]
        if self._strict:
            msg = f"unexpected command: {' '.join(argv)}"
            raise ExecutionError(msg)
        return self._default


class SubprocessCommandRunner:
    """Real command runner used by the CLI when an apply command is invoked."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        sensitive_output: bool = False,
    ) -> CommandResult:
        """Run a command with captured output."""

        del sensitive_output
        completed = subprocess.run(
            argv,
            input=input_text,
            capture_output=True,
            check=False,
            text=True,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def interpret_kubectl_diff(result: CommandResult) -> str:
    """Interpret Kubernetes diff return code semantics."""

    if result.returncode == 0:
        return "no_changes"
    if result.returncode == 1:
        return "changes_present"
    msg = f"kubectl diff failed with return code {result.returncode}"
    raise ExecutionError(msg)


def execute_plan(
    *,
    plan: ExecutionPlan,
    config: GatewayConfig,
    runner: CommandRunner,
    expected_context: str,
    apply_mode: str,
    confirmation: str,
    snapshot: PreChangeSnapshot | None,
    ledger: StateLedger | None = None,
    commands_log_path: Path | None = None,
) -> ExecutionReport:
    """Execute only the actions listed in an approved plan."""

    _validate_plan_integrity(plan)
    _validate_execution_inputs(
        plan=plan,
        config=config,
        expected_context=expected_context,
        apply_mode=apply_mode,
        confirmation=confirmation,
        snapshot=snapshot,
        ledger=ledger,
    )
    if snapshot is None:
        msg = f"{plan.command} requires a saved pre-change snapshot"
        raise ExecutionError(msg)

    command_log = CommandLog()
    logging_runner = _LoggingRunner(runner=runner, command_log=command_log)
    results: list[ActionResult] = []
    context_verified = False

    for action in plan.actions:
        if action.mutating and not context_verified:
            msg = f"{action.id} refused before context verification"
            raise SafetyError(msg)
        result = _execute_action(
            action=action,
            runner=logging_runner,
            expected_context=expected_context,
            ledger=ledger,
        )
        if action.kind == "verify-context":
            context_verified = True
        results.append(result)

    if commands_log_path is not None:
        command_log.write(commands_log_path)

    report_ledger = None
    if plan.command in {"deploy apply", "cutover apply"}:
        report_ledger = ledger_from_resources(
            resources=plan.resources,
            snapshot=snapshot,
            run_id=plan.plan_id,
        )
    if plan.command == "rollback apply":
        report_ledger = ledger

    return ExecutionReport(
        status="succeeded",
        plan_id=plan.plan_id,
        command=plan.command,
        apply_mode=plan.apply_mode,
        results=tuple(results),
        ledger=report_ledger,
    )


class _LoggingRunner:
    def __init__(self, *, runner: CommandRunner, command_log: CommandLog) -> None:
        self._runner = runner
        self._command_log = command_log
        self.action_id = "unknown"

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        sensitive_output: bool = False,
    ) -> CommandResult:
        result = self._runner.run(
            argv,
            input_text=input_text,
            sensitive_output=sensitive_output,
        )
        self._command_log.append(
            action_id=self.action_id,
            argv=argv,
            result=result,
            sensitive_output=sensitive_output,
        )
        return result


def _execute_action(
    *,
    action: PlanAction,
    runner: _LoggingRunner,
    expected_context: str,
    ledger: StateLedger | None,
) -> ActionResult:
    runner.action_id = action.id
    if action.kind == "verify-context":
        result = _run_required(action, runner)
        actual_context = result.stdout.strip()
        if actual_context != expected_context:
            msg = f"context mismatch: expected {expected_context!r}, got {actual_context!r}"
            raise SafetyError(msg)
        return _action_result(action, result, "verified")

    if action.kind == "kubectl-diff":
        result = runner.run(action.command, sensitive_output=action.sensitive_output)
        diff_status = interpret_kubectl_diff(result)
        return _action_result(action, result, diff_status)

    if action.kind == "read-ingress-state":
        result = runner.run(action.command, sensitive_output=action.sensitive_output)
        if result.returncode != 0:
            msg = "Ingress state could not be read; aborting before changing traffic"
            raise ExecutionError(msg)
        return _action_result(action, result, "read")

    if action.kind in {"kubectl-apply", "kubectl-delete", "kubectl-delete-resource"}:
        result = _run_required(action, runner)
        return _action_result(action, result, "applied" if "apply" in action.kind else "deleted")

    if action.kind == "kubectl-restore":
        manifest = _restore_manifest(action, ledger)
        result = runner.run(
            action.command,
            input_text=yaml.safe_dump(manifest, sort_keys=False),
            sensitive_output=True,
        )
        if result.returncode != 0:
            msg = f"{action.id} failed with return code {result.returncode}"
            raise ExecutionError(msg)
        return _action_result(action, result, "restored")

    if action.kind == "poll-gateway":
        namespace, name = _poll_payload(action)
        poll_gateway_programmed(runner, namespace=namespace, name=name)
        return ActionResult(action_id=action.id, kind=action.kind, status="ready")

    if action.kind == "poll-httproute":
        namespace, name = _poll_payload(action)
        poll_httproute_ready(runner, namespace=namespace, name=name)
        return ActionResult(action_id=action.id, kind=action.kind, status="ready")

    if action.kind == "poll-policy":
        namespace, name = _poll_payload(action)
        poll_policy_ready(runner, namespace=namespace, name=name)
        return ActionResult(action_id=action.id, kind=action.kind, status="ready")

    if action.kind == "poll-deployment":
        namespace, name = _poll_payload(action)
        poll_deployment_ready(runner, namespace=namespace, name=name)
        return ActionResult(action_id=action.id, kind=action.kind, status="ready")

    msg = f"unsupported plan action kind: {action.kind}"
    raise ExecutionError(msg)


def _restore_manifest(action: PlanAction, ledger: StateLedger | None) -> dict[str, object]:
    if ledger is None:
        msg = f"{action.id} requires the saved state ledger"
        raise ExecutionError(msg)
    identity = (action.payload or {}).get("restore_identity")
    if not isinstance(identity, str):
        msg = f"{action.id} restore action has no ledger identity"
        raise ExecutionError(msg)
    for entry in ledger.entries:
        if entry.ref.identity == identity and entry.before is not None:
            return entry.before
    msg = f"{action.id} could not find pre-change resource {identity}"
    raise ExecutionError(msg)


def _run_required(action: PlanAction, runner: _LoggingRunner) -> CommandResult:
    result = runner.run(action.command, sensitive_output=action.sensitive_output)
    if result.returncode != 0:
        msg = f"{action.id} failed with return code {result.returncode}"
        raise ExecutionError(msg)
    return result


def _action_result(action: PlanAction, result: CommandResult, status: str) -> ActionResult:
    return ActionResult(
        action_id=action.id,
        kind=action.kind,
        status=status,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        sensitive_output=action.sensitive_output,
    )


def _poll_payload(action: PlanAction) -> tuple[str, str]:
    payload = action.payload or {}
    namespace = payload.get("namespace")
    name = payload.get("name")
    if not isinstance(namespace, str) or not isinstance(name, str):
        msg = f"{action.id} poll action requires namespace and name"
        raise ExecutionError(msg)
    return namespace, name


def _validate_execution_inputs(
    *,
    plan: ExecutionPlan,
    config: GatewayConfig,
    expected_context: str,
    apply_mode: str,
    confirmation: str,
    snapshot: PreChangeSnapshot | None,
    ledger: StateLedger | None,
) -> None:
    if plan.command not in MUTATING_ACTIONS:
        msg = f"executor only accepts state-changing plans, got {plan.command}"
        raise ExecutionError(msg)
    configured_context = config.platform.cluster.expected_context
    if expected_context != configured_context or expected_context != plan.expected_context:
        msg = f"{plan.command} refused: expected context must match the saved plan exactly"
        raise SafetyError(msg)
    if apply_mode != plan.apply_mode:
        msg = f"{plan.command} refused: apply mode must match the saved plan exactly"
        raise SafetyError(msg)
    if confirmation != config.platform.confirmation_token:
        msg = f"{plan.command} refused: confirmation token does not match configuration"
        raise SafetyError(msg)
    if snapshot is None:
        msg = f"{plan.command} requires a saved pre-change snapshot"
        raise ExecutionError(msg)
    snapshot.require_ok()
    if plan.command == "rollback apply" and ledger is None:
        msg = "rollback apply requires the saved state ledger"
        raise ExecutionError(msg)


def _validate_plan_integrity(plan: ExecutionPlan) -> None:
    expected = plan.with_computed_id().plan_id
    if expected != plan.plan_id:
        msg = "plan_id does not match the approved plan contents"
        raise ExecutionError(msg)
