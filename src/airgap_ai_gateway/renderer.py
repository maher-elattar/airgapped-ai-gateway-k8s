"""Offline manifest rendering skeleton."""

from __future__ import annotations

from pathlib import Path

from airgap_ai_gateway.models import GatewayConfig
from airgap_ai_gateway.yaml_io import dump_yaml

RUNTIME_SECRET_PLACEHOLDER = "REPLACE_AT_RUNTIME"


def render_manifests(config: GatewayConfig) -> dict[str, str]:
    """Render safe, fake-only manifests for this scaffold phase."""

    platform_contract = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "airgap-ai-gateway-platform-contract",
            "namespace": config.platform.gateway.namespace,
            "labels": {
                "app.kubernetes.io/name": "airgap-ai-gateway-platform",
                "app.kubernetes.io/part-of": "ai-gateway",
            },
        },
        "data": {
            "agentgatewayVersion": config.platform.baseline.agentgateway_version,
            "gatewayApiVersion": config.platform.baseline.gateway_api_version,
            "runtimeCredentialPlaceholder": RUNTIME_SECRET_PLACEHOLDER,
            "secretPolicy": "runtime-secret-material-stays-outside-git",
        },
    }
    model_contract = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "airgap-ai-gateway-model-contract",
            "namespace": config.platform.gateway.namespace,
        },
        "data": {
            model.key: (
                f"kind={model.kind}; host={model.host}; path={model.route_path}; "
                f"backend={model.backend}; permission={model.permission}"
            )
            for model in config.models
        },
    }
    consumer_contract = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "airgap-ai-gateway-consumer-contract",
            "namespace": config.platform.gateway.namespace,
        },
        "data": {
            consumer.key: (
                f"models={','.join(consumer.allowed_models)}; "
                f"credential={consumer.credential_placeholder}"
            )
            for consumer in config.consumers
        },
    }

    return {
        "00-platform-contract.yaml": dump_yaml(platform_contract),
        "10-model-contract.yaml": dump_yaml(model_contract),
        "20-consumer-contract.yaml": dump_yaml(consumer_contract),
    }


def write_rendered_manifests(config: GatewayConfig, output_dir: Path) -> list[Path]:
    """Write rendered scaffold manifests to an output directory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in render_manifests(config).items():
        target = output_dir / name
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written
