"""Configuration loading and validation."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from airgap_ai_gateway.errors import ConfigError
from airgap_ai_gateway.models import (
    Baseline,
    ClusterSettings,
    ConsumerConfig,
    ConsumerRateLimits,
    ExposureMode,
    GatewayConfig,
    GatewaySettings,
    ModelConfig,
    ModelKind,
    PlatformConfig,
    RateLimitSettings,
    ReferenceGrantMode,
    RegistryImage,
    RegistrySettings,
    RouteBackend,
    ServicePort,
    ServiceRef,
)
from airgap_ai_gateway.yaml_io import load_yaml_file


def load_config(path: Path) -> GatewayConfig:
    """Load a complete config from a file or a directory of example-style files."""

    raw = _load_raw_config(path)
    config = _parse_config(raw)
    validate_config(config)
    return config


def _load_raw_config(path: Path) -> dict[str, Any]:
    if path.is_file():
        return load_yaml_file(path)
    if not path.is_dir():
        msg = f"configuration path does not exist: {path}"
        raise ConfigError(msg)

    raw: dict[str, Any] = {}
    platform_file = path / "platform.yaml"
    consumers_file = path / "consumers.yaml"
    if not platform_file.exists():
        msg = f"missing platform configuration: {platform_file}"
        raise ConfigError(msg)
    raw.update(load_yaml_file(platform_file))

    models: list[dict[str, Any]] = []
    models_dir = path / "models"
    for model_file in sorted(models_dir.glob("*.yaml")):
        data = load_yaml_file(model_file)
        model_items = data.get("models", [])
        if not isinstance(model_items, list):
            msg = f"{model_file} field models must be a list"
            raise ConfigError(msg)
        models.extend(_expect_mapping(item, f"{model_file}:models[]") for item in model_items)
    raw["models"] = models

    if not consumers_file.exists():
        msg = f"missing consumer configuration: {consumers_file}"
        raise ConfigError(msg)
    raw.update(load_yaml_file(consumers_file))
    return raw


def _parse_config(raw: dict[str, Any]) -> GatewayConfig:
    platform_raw = _expect_mapping(raw.get("platform"), "platform")
    cluster_raw = _expect_mapping(raw.get("cluster"), "cluster")
    gateway_raw = _expect_mapping(raw.get("gateway"), "gateway")
    rate_limit_raw = _expect_mapping(raw.get("rate_limit"), "rate_limit")
    registry_raw = _expect_mapping(raw.get("registry"), "registry")

    platform = PlatformConfig(
        name=_expect_string(platform_raw.get("name"), "platform.name"),
        baseline=Baseline(
            agentgateway_version=_expect_string(
                platform_raw.get("agentgateway_version"),
                "platform.agentgateway_version",
            ),
            gateway_api_version=_expect_string(
                platform_raw.get("gateway_api_version"),
                "platform.gateway_api_version",
            ),
        ),
        cluster=ClusterSettings(
            expected_context=_expect_string(
                cluster_raw.get("expected_context"), "cluster.expected_context"
            )
        ),
        gateway=GatewaySettings(
            namespace=_expect_string(gateway_raw.get("namespace"), "gateway.namespace"),
            name=_expect_string(gateway_raw.get("name"), "gateway.name"),
            hostname_wildcard=_expect_string(
                gateway_raw.get("hostname_wildcard"),
                "gateway.hostname_wildcard",
            ),
            reference_grant_mode=ReferenceGrantMode(
                _expect_string(
                    gateway_raw.get("reference_grant_mode"), "gateway.reference_grant_mode"
                )
            ),
            exposure_mode=ExposureMode(
                _expect_string(gateway_raw.get("exposure_mode"), "gateway.exposure_mode")
            ),
        ),
        rate_limit=RateLimitSettings(
            enabled=_expect_bool(rate_limit_raw.get("enabled"), "rate_limit.enabled"),
            backend_enabled=_expect_bool(
                rate_limit_raw.get("backend_enabled"),
                "rate_limit.backend_enabled",
            ),
            namespace=_expect_string(rate_limit_raw.get("namespace"), "rate_limit.namespace"),
            service_name=_expect_string(
                rate_limit_raw.get("service_name"), "rate_limit.service_name"
            ),
        ),
        registry=RegistrySettings(
            private_registry=_expect_string(
                registry_raw.get("private_registry"), "registry.private_registry"
            ),
            strict_airgap=_expect_bool(registry_raw.get("strict_airgap"), "registry.strict_airgap"),
            images=tuple(
                _parse_image(item)
                for item in _expect_list(registry_raw.get("images"), "registry.images")
            ),
        ),
        confirmation_token=_expect_string(
            platform_raw.get("confirmation_token"), "platform.confirmation_token"
        ),
    )

    models = tuple(_parse_model(item) for item in _expect_list(raw.get("models"), "models"))
    consumers = tuple(
        _parse_consumer(item) for item in _expect_list(raw.get("consumers"), "consumers")
    )
    return GatewayConfig(platform=platform, models=models, consumers=consumers)


def _parse_image(raw: object) -> RegistryImage:
    item = _expect_mapping(raw, "registry.images[]")
    return RegistryImage(
        name=_expect_string(item.get("name"), "registry.images[].name"),
        source=_expect_string(item.get("source"), "registry.images[].source"),
        target=_expect_string(item.get("target"), "registry.images[].target"),
    )


def _parse_model(raw: object) -> ModelConfig:
    item = _expect_mapping(raw, "models[]")
    service_raw = _expect_mapping(item.get("service"), "models[].service")
    return ModelConfig(
        key=_expect_string(item.get("key"), "models[].key"),
        display_name=_expect_string(item.get("display_name"), "models[].display_name"),
        kind=ModelKind(_expect_string(item.get("kind"), "models[].kind")),
        host=_expect_string(item.get("host"), "models[].host"),
        route_path=_expect_string(item.get("route_path"), "models[].route_path"),
        permission=_expect_string(item.get("permission"), "models[].permission"),
        backend=RouteBackend(_expect_string(item.get("backend"), "models[].backend")),
        service=ServiceRef(
            name=_expect_string(service_raw.get("name"), "models[].service.name"),
            namespace=_expect_string(service_raw.get("namespace"), "models[].service.namespace"),
            ports=tuple(
                _parse_service_port(port)
                for port in _expect_list(service_raw.get("ports"), "models[].service.ports")
            ),
            target_port_name=_optional_string(
                service_raw.get("target_port_name"),
                "models[].service.target_port_name",
            ),
            target_port_number=_optional_int(
                service_raw.get("target_port_number"),
                "models[].service.target_port_number",
            ),
        ),
    )


def _parse_service_port(raw: object) -> ServicePort:
    item = _expect_mapping(raw, "models[].service.ports[]")
    return ServicePort(
        name=_optional_string(item.get("name"), "models[].service.ports[].name"),
        number=_expect_int(item.get("number"), "models[].service.ports[].number"),
    )


def _parse_consumer(raw: object) -> ConsumerConfig:
    item = _expect_mapping(raw, "consumers[]")
    rate_limit_raw = item.get("rate_limits")
    rate_limits = None
    if rate_limit_raw is not None:
        rate_limit = _expect_mapping(rate_limit_raw, "consumers[].rate_limits")
        rate_limits = ConsumerRateLimits(
            requests_per_minute=_expect_int(
                rate_limit.get("requests_per_minute"),
                "consumers[].rate_limits.requests_per_minute",
            ),
            tokens_per_minute=_optional_int(
                rate_limit.get("tokens_per_minute"),
                "consumers[].rate_limits.tokens_per_minute",
            ),
        )

    return ConsumerConfig(
        key=_expect_string(item.get("key"), "consumers[].key"),
        display_name=_expect_string(item.get("display_name"), "consumers[].display_name"),
        allowed_models=tuple(
            _expect_string(value, "consumers[].allowed_models[]")
            for value in _expect_list(item.get("allowed_models"), "consumers[].allowed_models")
        ),
        credential_placeholder=_expect_string(
            item.get("credential_placeholder"),
            "consumers[].credential_placeholder",
        ),
        rate_limits=rate_limits,
    )


def validate_config(config: GatewayConfig) -> None:
    """Reject configuration that would make the gateway ambiguous or unsafe."""

    errors: list[str] = []
    errors.extend(_duplicates("model keys", (model.key for model in config.models)))
    errors.extend(_duplicates("model hosts", (model.host for model in config.models)))
    errors.extend(
        _duplicates("model routes", (f"{model.host}{model.route_path}" for model in config.models))
    )
    errors.extend(
        _duplicates("model permission fields", (model.permission for model in config.models))
    )
    errors.extend(_duplicates("consumer keys", (consumer.key for consumer in config.consumers)))

    model_keys = {model.key for model in config.models}
    for consumer in config.consumers:
        unknown = sorted(set(consumer.allowed_models) - model_keys)
        if unknown:
            errors.append(
                f"consumer {consumer.key} references unknown models: {', '.join(unknown)}"
            )

    for model in config.models:
        errors.extend(_validate_service_port(model))
        if (
            model.service.namespace != config.platform.gateway.namespace
            and config.platform.gateway.reference_grant_mode is not ReferenceGrantMode.EXPLICIT
        ):
            errors.append(
                f"model {model.key} references Service namespace {model.service.namespace}; "
                "set gateway.reference_grant_mode to explicit before using cross-namespace backends"
            )
        if model.kind is ModelKind.CHAT and model.backend is not RouteBackend.AGENTGATEWAY_BACKEND:
            errors.append(f"chat model {model.key} must use agentgateway-backend in this baseline")
        if (
            model.kind is ModelKind.EMBEDDING
            and model.backend is not RouteBackend.KUBERNETES_SERVICE
        ):
            errors.append(
                f"embedding model {model.key} must use kubernetes-service in this baseline"
            )

    if config.platform.rate_limit.enabled and not config.platform.rate_limit.backend_enabled:
        errors.append("rate limits are enabled but the rate-limit backend is disabled")
    if not config.platform.rate_limit.backend_enabled:
        for consumer in config.consumers:
            if consumer.rate_limits is not None:
                errors.append(
                    f"consumer {consumer.key} defines rate limits while the rate-limit backend is disabled"
                )

    if config.platform.registry.strict_airgap:
        errors.extend(_validate_strict_airgap_images(config.platform.registry))

    if not config.platform.cluster.expected_context.strip():
        errors.append("cluster.expected_context is required for all future apply-style commands")
    if not config.platform.confirmation_token.strip():
        errors.append("platform.confirmation_token is required for apply-style commands")

    if errors:
        raise ConfigError("\n".join(f"- {error}" for error in errors))


def _validate_service_port(model: ModelConfig) -> list[str]:
    errors: list[str] = []
    ports = model.service.ports
    if not ports:
        return [f"model {model.key} service must declare at least one port"]
    if model.service.target_port_name and model.service.target_port_number:
        errors.append(
            f"model {model.key} service cannot set both target_port_name and target_port_number"
        )
    if (
        len(ports) > 1
        and not model.service.target_port_name
        and not model.service.target_port_number
    ):
        errors.append(
            f"model {model.key} has ambiguous Service ports; set target_port_name or target_port_number"
        )
    if model.service.target_port_name and model.service.target_port_name not in {
        port.name for port in ports if port.name
    }:
        errors.append(
            f"model {model.key} target_port_name does not match any declared Service port"
        )
    if model.service.target_port_number and model.service.target_port_number not in {
        port.number for port in ports
    }:
        errors.append(
            f"model {model.key} target_port_number does not match any declared Service port"
        )
    return errors


def _validate_strict_airgap_images(registry: RegistrySettings) -> list[str]:
    errors: list[str] = []
    prefix = registry.private_registry.rstrip("/") + "/"
    for image in registry.images:
        if not image.target.startswith(prefix):
            errors.append(
                f"image {image.name} target must use private registry {registry.private_registry}"
            )
        if "@sha256:" not in image.target:
            errors.append(
                f"image {image.name} target must be pinned by digest in strict air-gap mode"
            )
        if image.target.endswith(":latest") or ":latest@" in image.target:
            errors.append(f"image {image.name} target must not use the mutable latest tag")
    return errors


def _duplicates(label: str, values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for value in values:
        if value in seen:
            repeated.add(value)
        seen.add(value)
    if not repeated:
        return []
    return [f"duplicate {label}: {', '.join(sorted(repeated))}"]


def _expect_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"{label} must be a mapping"
        raise ConfigError(msg)
    return value


def _expect_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"{label} must be a list"
        raise ConfigError(msg)
    return value


def _expect_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"{label} must be a non-empty string"
        raise ConfigError(msg)
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _expect_string(value, label)


def _expect_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        msg = f"{label} must be a boolean"
        raise ConfigError(msg)
    return value


def _expect_int(value: object, label: str) -> int:
    if not isinstance(value, int):
        msg = f"{label} must be an integer"
        raise ConfigError(msg)
    return value


def _optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _expect_int(value, label)
