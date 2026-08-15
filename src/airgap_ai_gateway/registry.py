"""Private registry mapping helpers."""

from __future__ import annotations

from dataclasses import asdict

from airgap_ai_gateway.models import GatewayConfig


def image_mapping(config: GatewayConfig) -> list[dict[str, str]]:
    """Return configured public-to-private image mapping."""

    return [asdict(image) for image in config.platform.registry.images]


def promotion_plan(config: GatewayConfig) -> dict[str, object]:
    """Return a safe registry promotion plan without moving images."""

    return {
        "private_registry": config.platform.registry.private_registry,
        "strict_airgap": config.platform.registry.strict_airgap,
        "images": image_mapping(config),
        "status": "registry-promotion-skeleton",
    }
