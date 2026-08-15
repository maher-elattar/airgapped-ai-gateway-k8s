from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from airgap_ai_gateway.configuration import load_config, validate_config
from airgap_ai_gateway.errors import ConfigError
from airgap_ai_gateway.models import (
    ConsumerConfig,
    ConsumerRateLimits,
    RegistryImage,
    ServicePort,
    ServiceRef,
)

EXAMPLE_CONFIG = Path("examples/config")


def test_example_config_is_valid() -> None:
    config = load_config(EXAMPLE_CONFIG)

    assert config.platform.registry.private_registry == "registry.example.internal:5000"
    assert {model.key for model in config.models} == {
        "qwen-chat",
        "gemma-chat",
        "embedding-index",
    }


def test_duplicate_model_keys_are_rejected() -> None:
    config = load_config(EXAMPLE_CONFIG)
    invalid = replace(config, models=(*config.models, config.models[0]))

    with pytest.raises(ConfigError, match="duplicate model keys"):
        validate_config(invalid)


def test_duplicate_hosts_and_permissions_are_rejected() -> None:
    config = load_config(EXAMPLE_CONFIG)
    duplicate = replace(
        config.models[1], host=config.models[0].host, permission=config.models[0].permission
    )
    invalid = replace(config, models=(config.models[0], duplicate, config.models[2]))

    with pytest.raises(ConfigError) as error:
        validate_config(invalid)

    message = str(error.value)
    assert "duplicate model hosts" in message
    assert "duplicate model permission fields" in message


def test_ambiguous_service_ports_are_rejected() -> None:
    config = load_config(EXAMPLE_CONFIG)
    service = ServiceRef(
        name="ambiguous",
        namespace=config.platform.gateway.namespace,
        ports=(ServicePort(name="http", number=8000), ServicePort(name="metrics", number=9090)),
    )
    model = replace(config.models[0], service=service)
    invalid = replace(config, models=(model, config.models[1], config.models[2]))

    with pytest.raises(ConfigError, match="ambiguous Service ports"):
        validate_config(invalid)


def test_cross_namespace_service_requires_explicit_reference_grant_mode() -> None:
    config = load_config(EXAMPLE_CONFIG)
    service = replace(config.models[0].service, namespace="other-model-namespace")
    model = replace(config.models[0], service=service)
    invalid = replace(config, models=(model, config.models[1], config.models[2]))

    with pytest.raises(ConfigError, match="cross-namespace backends"):
        validate_config(invalid)


def test_rate_limits_require_enabled_backend() -> None:
    config = load_config(EXAMPLE_CONFIG)
    platform = replace(
        config.platform,
        rate_limit=replace(config.platform.rate_limit, enabled=True, backend_enabled=False),
    )
    invalid = replace(config, platform=platform)

    with pytest.raises(ConfigError, match="rate-limit backend is disabled"):
        validate_config(invalid)


def test_strict_airgap_rejects_public_or_mutable_runtime_image_refs() -> None:
    config = load_config(EXAMPLE_CONFIG)
    registry = replace(
        config.platform.registry,
        images=(
            RegistryImage(
                name="bad-runtime-image",
                source="registry.example.invalid/bad-runtime-image:latest",
                target="docker.io/library/bad-runtime-image:latest",
            ),
        ),
    )
    invalid = replace(config, platform=replace(config.platform, registry=registry))

    with pytest.raises(ConfigError) as error:
        validate_config(invalid)

    message = str(error.value)
    assert "private registry" in message
    assert "pinned by digest" in message
    assert "mutable latest tag" in message


def test_consumer_cannot_reference_unknown_model() -> None:
    config = load_config(EXAMPLE_CONFIG)
    consumer = ConsumerConfig(
        key="unknown-model-consumer",
        display_name="Unknown Model Consumer",
        allowed_models=("missing-model",),
        credential_placeholder="REPLACE_AT_RUNTIME",
        rate_limits=ConsumerRateLimits(requests_per_minute=1),
    )
    invalid = replace(config, consumers=(*config.consumers, consumer))

    with pytest.raises(ConfigError, match="unknown models"):
        validate_config(invalid)


def test_model_backend_must_match_api_shape() -> None:
    config = load_config(EXAMPLE_CONFIG)
    invalid_model = replace(config.models[2], backend=config.models[0].backend)
    invalid = replace(config, models=(config.models[0], config.models[1], invalid_model))

    with pytest.raises(ConfigError, match="embedding model embedding-index"):
        validate_config(invalid)
