"""Command intent helpers for reportable operations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Captured command result."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True, slots=True)
class CommandIntent:
    """A command that a future implementation may execute from retained scripts."""

    label: str
    argv: tuple[str, ...]
    mutating: bool


def describe_intent(label: str, argv: tuple[str, ...], *, mutating: bool) -> CommandIntent:
    """Create a command intent without executing it."""

    return CommandIntent(label=label, argv=argv, mutating=mutating)
