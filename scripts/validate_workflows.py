#!/usr/bin/env python3
"""Validate GitHub Actions workflow safety rules."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def workflow_on(document: dict[Any, Any]) -> Any:
    """Return the workflow trigger mapping while tolerating YAML 1.1 loaders."""

    if "on" in document:
        return document["on"]
    return document.get(True, {})


def trigger_names(triggers: Any) -> set[str]:
    """Return normalized trigger names."""

    if isinstance(triggers, str):
        return {triggers}
    if isinstance(triggers, list):
        return {str(trigger) for trigger in triggers}
    if isinstance(triggers, dict):
        return {str(trigger) for trigger in triggers}
    return set()


def validate_workflow(path: Path) -> list[str]:
    """Return workflow policy violations for one workflow file."""

    errors: list[str] = []
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        return [f"{path}: workflow is not a mapping"]

    permissions = document.get("permissions")
    if permissions != {"contents": "read"}:
        errors.append(f"{path}: top-level permissions must be exactly contents: read")

    triggers = workflow_on(document)
    if "pull_request_target" in trigger_names(triggers):
        errors.append(f"{path}: pull_request_target is not allowed")

    concurrency = document.get("concurrency")
    if not isinstance(concurrency, dict):
        errors.append(f"{path}: top-level concurrency is required")
    elif concurrency.get("cancel-in-progress") is not True:
        errors.append(f"{path}: concurrency must cancel superseded runs")

    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return [*errors, f"{path}: workflow must define at least one job"]

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            errors.append(f"{path}: job {job_name} is not a mapping")
            continue
        if "timeout-minutes" not in job:
            errors.append(f"{path}: job {job_name} must set timeout-minutes")
        job_permissions = job.get("permissions")
        if job_permissions is not None and job_permissions != {"contents": "read"}:
            errors.append(f"{path}: job {job_name} may not broaden permissions")
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            errors.append(f"{path}: job {job_name} steps must be a list")
            continue
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                errors.append(f"{path}: job {job_name} step {index} is not a mapping")
                continue
            uses = step.get("uses")
            if uses is None:
                continue
            if not isinstance(uses, str):
                errors.append(f"{path}: job {job_name} step {index} uses must be a string")
                continue
            if uses.startswith("./"):
                continue
            if not FULL_SHA.fullmatch(uses):
                errors.append(
                    f"{path}: job {job_name} step {index} action must be pinned by commit SHA"
                )
            if uses.startswith("actions/cache@"):
                cache_input = step.get("with", {})
                key = cache_input.get("key") if isinstance(cache_input, dict) else None
                if not isinstance(key, str) or "hashFiles(" not in key:
                    errors.append(f"{path}: actions/cache key must be exact and content-derived")
                if isinstance(cache_input, dict) and "restore-keys" in cache_input:
                    errors.append(f"{path}: actions/cache restore-keys are not allowed")
            if uses.startswith("actions/upload-artifact@"):
                artifact_input = step.get("with", {})
                if not isinstance(artifact_input, dict):
                    errors.append(f"{path}: upload-artifact must define explicit inputs")
                    continue
                if artifact_input.get("if-no-files-found") != "error":
                    errors.append(f"{path}: upload-artifact must fail when artifacts are missing")
                if "retention-days" not in artifact_input:
                    errors.append(f"{path}: upload-artifact must set retention-days")

    return errors


def main() -> int:
    """Run the workflow validator."""

    if not WORKFLOW_DIR.exists():
        print("workflow validation failed: .github/workflows does not exist", file=sys.stderr)
        return 1

    workflow_paths = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))
    if not workflow_paths:
        print("workflow validation failed: no workflows found", file=sys.stderr)
        return 1

    errors = [error for path in workflow_paths for error in validate_workflow(path)]
    if errors:
        print("workflow validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"workflow validation passed: {len(workflow_paths)} workflow(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
