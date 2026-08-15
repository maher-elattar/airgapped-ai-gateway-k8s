"""Project-specific exceptions."""


class AirgapGatewayError(Exception):
    """Base class for expected CLI failures."""


class ConfigError(AirgapGatewayError):
    """Raised when declarative configuration is invalid."""


class SafetyError(AirgapGatewayError):
    """Raised when a mutating command does not pass the safety gate."""


class PlanError(AirgapGatewayError):
    """Raised when a deterministic plan cannot be built."""


class ExecutionError(AirgapGatewayError):
    """Raised when an approved plan cannot be executed safely."""


class DiscoveryError(AirgapGatewayError):
    """Raised when read-only discovery is ambiguous or incomplete."""


class VerificationError(AirgapGatewayError):
    """Raised when a verification check fails."""
