"""Private registry mapping helpers."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from airgap_ai_gateway.airgap_bundle import (
    DEFAULT_COMPATIBILITY_SET,
    DEFAULT_LOCK_PATH,
    DEFAULT_PROMOTION_TOOL,
    build_registry_promotion_plan,
    load_source_lock,
)
from airgap_ai_gateway.models import GatewayConfig


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
