from __future__ import annotations

from pathlib import Path

import yaml

from airgap_ai_gateway.manifest import (
    OVERLAYS,
    build_overlay,
    dump_documents,
    load_image_map,
    summarize_documents,
    validate_documents,
)

GOLDEN_DIR = Path("tests/golden/manifests")


def test_each_overlay_passes_semantic_validation() -> None:
    image_map = load_image_map()

    for overlay in OVERLAYS:
        documents = build_overlay(overlay)
        errors = validate_documents(documents, overlay=overlay, image_map=image_map)

        assert errors == []


def test_overlay_golden_summaries_are_stable() -> None:
    for overlay in OVERLAYS:
        documents = build_overlay(overlay)
        expected = yaml.safe_load((GOLDEN_DIR / f"{overlay}.yaml").read_text(encoding="utf-8"))

        assert summarize_documents(documents, overlay) == expected


def test_no_secret_or_public_registry_is_rendered() -> None:
    for overlay in OVERLAYS:
        rendered = dump_documents(build_overlay(overlay))

        assert "kind: Secret" not in rendered
        assert "stringData:" not in rendered
        assert "cr.agentgateway.dev/" not in rendered
        assert "docker.io/" not in rendered
        assert "registry.example.internal:5000/" in rendered


def test_production_reference_excludes_demo_redis_workload() -> None:
    rendered = dump_documents(build_overlay("production-reference"))

    assert "kind: Deployment\nmetadata:\n  name: redis" not in rendered
    assert "name: external-ha-redis" in rendered
    assert "external-ha-redis.ai-gateway.svc.cluster.local:6379" in rendered
