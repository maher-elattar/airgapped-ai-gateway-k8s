"""Private registry mapping helpers."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, cast

from airgap_ai_gateway.airgap_bundle import (
    DEFAULT_COMPATIBILITY_SET,
    DEFAULT_LOCK_PATH,
    DEFAULT_PROMOTION_TOOL,
    build_registry_promotion_plan,
    load_source_lock,
)
from airgap_ai_gateway.command import CommandResult
from airgap_ai_gateway.errors import BundleError, SafetyError
from airgap_ai_gateway.models import GatewayConfig
from airgap_ai_gateway.redaction import redact_mapping, redact_text


class PromotionRunner(Protocol):
    """Command runner used by registry promotion apply."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        sensitive_output: bool = False,
    ) -> CommandResult:
        """Run a command."""


def image_mapping(config: GatewayConfig) -> list[dict[str, str]]:
    """Return configured public-to-private image mapping."""

    return [asdict(image) for image in config.platform.registry.images]


def promotion_plan(
    config: GatewayConfig,
    *,
    lock_file: Path = DEFAULT_LOCK_PATH,
    compatibility_set: str = DEFAULT_COMPATIBILITY_SET,
    private_registry: str | None = None,
    check_existing: bool = True,
    tool: str = DEFAULT_PROMOTION_TOOL,
    output_file: Path | None = None,
) -> dict[str, object]:
    """Return a safe registry promotion plan without moving images."""

    registry = private_registry or config.platform.registry.private_registry
    if lock_file.exists():
        return build_registry_promotion_plan(
            load_source_lock(lock_file),
            compatibility_set=compatibility_set,
            private_registry=registry,
            check_existing=check_existing,
            tool=tool,
            output_file=output_file,
        )

    plan: dict[str, Any] = {
        "private_registry": config.platform.registry.private_registry,
        "strict_airgap": config.platform.registry.strict_airgap,
        "images": image_mapping(config),
        "status": "registry-promotion-skeleton",
    }
    return plan


def apply_promotion_plan(
    *,
    plan_file: Path,
    runner: PromotionRunner,
    confirmation: str,
    expected_confirmation: str,
    commands_log_path: Path | None = None,
) -> dict[str, object]:
    """Apply only image copy actions listed in an approved promotion plan."""

    if confirmation != expected_confirmation:
        msg = "registry promote apply refused: confirmation token does not match configuration"
        raise SafetyError(msg)
    plan = _load_plan(plan_file)
    actions = plan.get("actions")
    if not isinstance(actions, list):
        msg = "promotion plan must contain an actions list"
        raise BundleError(msg)

    results: list[dict[str, object]] = []
    command_log: list[dict[str, object]] = []
    for raw_action in actions:
        action = _mapping(raw_action, "promotion action")
        name = _string(action.get("name"), "promotion action name")
        check_existing = bool(action.get("checkExistingBeforePush", False))
        existence_check = _command(action.get("existenceCheck"), f"{name} existenceCheck")
        copy_command = _command(action.get("copyCommand"), f"{name} copyCommand")

        if check_existing:
            check = runner.run(existence_check)
            command_log.append(_command_log_entry(existence_check, check))
            if check.returncode == 0:
                results.append({"name": name, "status": "already-present"})
                continue

        copied = runner.run(copy_command)
        command_log.append(_command_log_entry(copy_command, copied))
        if copied.returncode != 0:
            msg = f"registry promotion failed for {name}: {copied.stderr.strip() or copied.returncode}"
            raise BundleError(msg)
        results.append({"name": name, "status": "promoted"})

    report = {
        "actionCount": len(actions),
        "planFile": str(plan_file),
        "privateRegistry": plan.get("privateRegistry", ""),
        "results": results,
        "status": "applied",
    }
    if commands_log_path is not None:
        commands_log_path.parent.mkdir(parents=True, exist_ok=True)
        commands_log_path.write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in command_log),
            encoding="utf-8",
        )
    return cast(dict[str, object], redact_mapping(report))


def _load_plan(path: Path) -> dict[str, object]:
    if not path.exists():
        msg = f"promotion plan does not exist: {path}"
        raise BundleError(msg)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        msg = "promotion plan must contain a JSON object"
        raise BundleError(msg)
    if loaded.get("status") != "planned":
        msg = "promotion plan status must be planned"
        raise BundleError(msg)
    return cast(dict[str, object], loaded)


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{label} must be a JSON object"
        raise BundleError(msg)
    return cast(dict[str, object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"{label} must be a non-empty string"
        raise BundleError(msg)
    return value


def _command(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = f"{label} must be a list of strings"
        raise BundleError(msg)
    return tuple(value)


def _command_log_entry(argv: tuple[str, ...], result: CommandResult) -> dict[str, object]:
    return {
        "argv": [redact_text(item) for item in argv],
        "returncode": result.returncode,
        "stderr": redact_text(result.stderr),
        "stdout": redact_text(result.stdout),
    }
