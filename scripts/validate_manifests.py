#!/usr/bin/env python
"""Validate Kustomize overlays and Kubernetes manifest semantics."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from airgap_ai_gateway.manifest import (  # noqa: E402
    OVERLAYS,
    build_overlay,
    load_image_map,
    overlay_path,
    summarize_documents,
    validate_documents,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlay", choices=OVERLAYS, action="append")
    parser.add_argument("--kustomize", default="kustomize")
    parser.add_argument("--require-kustomize", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    overlays = tuple(args.overlay or OVERLAYS)
    image_map = load_image_map()
    kustomize_path = shutil.which(args.kustomize)
    if args.require_kustomize and kustomize_path is None:
        print(f"required kustomize binary not found: {args.kustomize}", file=sys.stderr)
        return 2

    failures: list[str] = []
    for overlay in overlays:
        documents = build_overlay(overlay)
        failures.extend(
            f"{overlay}: {error}"
            for error in validate_documents(documents, overlay=overlay, image_map=image_map)
        )
        if kustomize_path is not None:
            failures.extend(
                _compare_external_kustomize(kustomize_path, overlay, documents, image_map)
            )
        if args.summary:
            print(yaml.safe_dump(summarize_documents(documents, overlay), sort_keys=False))
        else:
            print(f"{overlay}: {len(documents)} resources validated")

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    if kustomize_path is None:
        print("kustomize build: skipped because standalone kustomize is not installed")
    return 0


def _compare_external_kustomize(
    kustomize_path: str,
    overlay: str,
    internal_documents: list[dict[str, Any]],
    image_map: dict[str, str],
) -> list[str]:
    completed = subprocess.run(
        [kustomize_path, "build", str(overlay_path(overlay))],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return [f"kustomize build failed for {overlay}: {completed.stderr.strip()}"]
    external_documents: list[dict[str, Any]] = [
        item for item in yaml.safe_load_all(completed.stdout) if isinstance(item, dict)
    ]
    internal_ids = sorted(_identity(item) for item in internal_documents)
    external_ids = sorted(_identity(item) for item in external_documents)
    if internal_ids != external_ids:
        return [f"kustomize build resource set differs for {overlay}"]
    return [
        f"kustomize build semantic check failed for {overlay}: {error}"
        for error in validate_documents(external_documents, overlay=overlay, image_map=image_map)
    ]


def _identity(item: dict[str, object]) -> tuple[str, str, str]:
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        return (str(item.get("kind")), "", "")
    return (
        str(item.get("kind")),
        str(metadata.get("namespace", "")),
        str(metadata.get("name", "")),
    )


if __name__ == "__main__":
    raise SystemExit(main())
