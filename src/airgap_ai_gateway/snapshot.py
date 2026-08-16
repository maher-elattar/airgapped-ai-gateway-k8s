"""Pre-change snapshot capture for approved plans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from airgap_ai_gateway.command import CommandResult
from airgap_ai_gateway.errors import ExecutionError, SafetyError
from airgap_ai_gateway.ledger import PreChangeSnapshot, ResourceRef
from airgap_ai_gateway.planning import ExecutionPlan
from airgap_ai_gateway.redaction import REDACTED


class SnapshotRunner(Protocol):
    """Command runner used by snapshot capture."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        sensitive_output: bool = False,
    ) -> CommandResult:
        """Run a command."""


@dataclass(frozen=True, slots=True)
class SnapshotReport:
    """Redacted snapshot capture report."""

    status: str
    output_file: str
    resource_count: int
    absent_count: int
    sensitive_count: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready report."""

        return {
            "absent_count": self.absent_count,
            "output_file": self.output_file,
            "resource_count": self.resource_count,
            "sensitive_count": self.sensitive_count,
            "status": self.status,
        }


def capture_snapshot(
    *,
    plan: ExecutionPlan,
    runner: SnapshotRunner,
    expected_context: str,
    output_file: Path,
) -> SnapshotReport:
    """Capture current state for every resource in an approved plan."""

    if expected_context != plan.expected_context:
        msg = "snapshot create refused: expected context must match the saved plan exactly"
        raise SafetyError(msg)

    resources: dict[str, dict[str, object]] = {}
    absent = 0
    sensitive = 0
    for ref in plan.resources:
        _verify_context(runner, expected_context)
        result = runner.run(_get_command(ref), sensitive_output=ref.kind == "Secret")
        if result.returncode == 0:
            resources[ref.identity] = _parse_manifest(ref, result.stdout)
            if ref.kind == "Secret":
                sensitive += 1
            continue
        if _is_not_found(result):
            absent += 1
            continue
        msg = f"snapshot capture failed for {ref.identity}: {result.stderr.strip() or result.returncode}"
        raise ExecutionError(msg)

    snapshot = PreChangeSnapshot(status="ok", resources=resources)
    snapshot.write(output_file)
    return SnapshotReport(
        status="captured",
        output_file=str(output_file),
        resource_count=len(resources),
        absent_count=absent,
        sensitive_count=sensitive,
    )


def _verify_context(runner: SnapshotRunner, expected_context: str) -> None:
    result = runner.run(("kubectl", "config", "current-context"))
    actual = result.stdout.strip()
    print(f"verifying kubectl context before snapshot: expected {expected_context}")
    if result.returncode != 0 or actual != expected_context:
        msg = f"context mismatch: expected {expected_context!r}, got {actual!r}"
        raise SafetyError(msg)


def _get_command(ref: ResourceRef) -> tuple[str, ...]:
    resource = ref.kind.lower()
    if ref.namespace:
        return ("kubectl", "-n", ref.namespace, "get", resource, ref.name, "-o", "json")
    return ("kubectl", "get", resource, ref.name, "-o", "json")


def _parse_manifest(ref: ResourceRef, stdout: str) -> dict[str, object]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        msg = f"snapshot capture returned invalid JSON for {ref.identity}"
        raise ExecutionError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"snapshot capture returned non-object JSON for {ref.identity}"
        raise ExecutionError(msg)
    return cast(dict[str, object], payload)


def _is_not_found(result: CommandResult) -> bool:
    combined = f"{result.stdout}\n{result.stderr}".lower()
    return result.returncode != 0 and ("notfound" in combined or "not found" in combined)


def redacted_snapshot_preview(snapshot: PreChangeSnapshot) -> dict[str, object]:
    """Return resource identities without exposing captured Secret material."""

    items: dict[str, object] = {}
    for identity, manifest in snapshot.resources.items():
        kind = manifest.get("kind")
        items[identity] = REDACTED if kind == "Secret" else kind
    return {"resources": dict(sorted(items.items())), "status": snapshot.status}
