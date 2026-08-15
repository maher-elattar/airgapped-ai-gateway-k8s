"""Project-specific exceptions."""


class AirgapGatewayError(Exception):
    """Base class for expected CLI failures."""


class ConfigError(AirgapGatewayError):
    """Raised when declarative configuration is invalid."""


class SafetyError(AirgapGatewayError):
    """Raised when a mutating command does not pass the safety gate."""
