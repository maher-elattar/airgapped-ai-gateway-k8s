"""Immutable configuration models for the gateway platform."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ModelKind(StrEnum):
    """Supported model API shapes in the delivered baseline."""

    CHAT = "chat"
    EMBEDDING = "embedding"


class RouteBackend(StrEnum):
    """Backend representation used by a model route."""

    AGENTGATEWAY_BACKEND = "agentgateway-backend"
    KUBERNETES_SERVICE = "kubernetes-service"


class ReferenceGrantMode(StrEnum):
    """Cross-namespace backend reference mode."""

    SAME_NAMESPACE_ONLY = "same-namespace-only"
    EXPLICIT = "explicit"


class ExposureMode(StrEnum):
    """North-south exposure mode."""

    RETAINED_NGINX = "retained-nginx"
    GREENFIELD_DIRECT = "greenfield-direct"


@dataclass(frozen=True, slots=True)
class Baseline:
    """Version set kept together until compatibility tests say otherwise."""

    agentgateway_version: str
    gateway_api_version: str


@dataclass(frozen=True, slots=True)
class ClusterSettings:
    """Cluster-level safety settings."""

    expected_context: str


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    """Gateway API listener and attachment settings."""

    namespace: str
    name: str
    hostname_wildcard: str
    reference_grant_mode: ReferenceGrantMode
    exposure_mode: ExposureMode


@dataclass(frozen=True, slots=True)
class RateLimitSettings:
    """Rate-limit service contract."""

    enabled: bool
    backend_enabled: bool
    namespace: str
    service_name: str


@dataclass(frozen=True, slots=True)
class RegistryImage:
    """Image mapping from an upstream name to an internal registry name."""

    name: str
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class RegistrySettings:
    """Private registry and strict air-gap image policy."""

    private_registry: str
    strict_airgap: bool
    images: tuple[RegistryImage, ...]


@dataclass(frozen=True, slots=True)
class PlatformConfig:
    """Platform-wide configuration."""

    name: str
    baseline: Baseline
    cluster: ClusterSettings
    gateway: GatewaySettings
    rate_limit: RateLimitSettings
    registry: RegistrySettings
    confirmation_token: str


@dataclass(frozen=True, slots=True)
class ServicePort:
    """A Kubernetes Service port candidate."""

    name: str | None
    number: int


@dataclass(frozen=True, slots=True)
class ServiceRef:
    """Kubernetes Service backend reference."""

    name: str
    namespace: str
    ports: tuple[ServicePort, ...]
    target_port_name: str | None = None
    target_port_number: int | None = None


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """A consumer-facing model API."""

    key: str
    display_name: str
    kind: ModelKind
    host: str
    route_path: str
    permission: str
    backend: RouteBackend
    service: ServiceRef


@dataclass(frozen=True, slots=True)
class ConsumerRateLimits:
    """Per-consumer quota settings."""

    requests_per_minute: int
    tokens_per_minute: int | None = None


@dataclass(frozen=True, slots=True)
class ConsumerConfig:
    """Application/workload identity at the gateway."""

    key: str
    display_name: str
    allowed_models: tuple[str, ...]
    credential_placeholder: str
    rate_limits: ConsumerRateLimits | None


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """Complete declarative configuration for the platform."""

    platform: PlatformConfig
    models: tuple[ModelConfig, ...]
    consumers: tuple[ConsumerConfig, ...]
