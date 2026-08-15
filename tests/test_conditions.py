from __future__ import annotations

import json

import pytest

from airgap_ai_gateway.command import CommandResult
from airgap_ai_gateway.conditions import (
    poll_deployment_ready,
    poll_gateway_programmed,
    poll_httproute_ready,
    poll_policy_ready,
)
from airgap_ai_gateway.errors import VerificationError
from airgap_ai_gateway.execution import FakeCommandRunner


def test_gateway_condition_polling_is_bounded() -> None:
    command = ("kubectl", "-n", "ai-gateway", "get", "gateway", "ai-gateway", "-o", "json")
    runner = FakeCommandRunner(
        {
            command: [
                CommandResult(0, json.dumps({"status": {"conditions": []}})),
                CommandResult(
                    0,
                    json.dumps(
                        {"status": {"conditions": [{"type": "Programmed", "status": "True"}]}}
                    ),
                ),
            ]
        }
    )

    poll_gateway_programmed(runner, namespace="ai-gateway", name="ai-gateway", attempts=2)

    assert runner.calls == [command, command]


def test_httproute_requires_accepted_and_resolved_refs() -> None:
    command = ("kubectl", "-n", "ai-gateway", "get", "httproute", "route-qwen-chat", "-o", "json")
    runner = FakeCommandRunner(
        {
            command: CommandResult(
                0,
                json.dumps(
                    {
                        "status": {
                            "parents": [
                                {
                                    "conditions": [
                                        {"type": "Accepted", "status": "True"},
                                        {"type": "ResolvedRefs", "status": "True"},
                                    ]
                                }
                            ]
                        }
                    }
                ),
            )
        },
        strict=True,
    )

    poll_httproute_ready(runner, namespace="ai-gateway", name="route-qwen-chat", attempts=1)


def test_policy_condition_timeout_reports_not_ready() -> None:
    command = (
        "kubectl",
        "-n",
        "ai-gateway",
        "get",
        "agentgatewaypolicy",
        "policy-qwen-chat",
        "-o",
        "json",
    )
    runner = FakeCommandRunner(
        {
            command: CommandResult(
                0,
                json.dumps({"status": {"conditions": [{"type": "Accepted", "status": "True"}]}}),
            )
        }
    )

    with pytest.raises(VerificationError, match="Attached=True"):
        poll_policy_ready(runner, namespace="ai-gateway", name="policy-qwen-chat", attempts=1)


def test_deployment_rollout_readiness_requires_observed_generation() -> None:
    command = (
        "kubectl",
        "-n",
        "ai-gateway",
        "get",
        "deployment",
        "envoy-ratelimit",
        "-o",
        "json",
    )
    runner = FakeCommandRunner(
        {
            command: [
                CommandResult(
                    0,
                    json.dumps(
                        {
                            "metadata": {"generation": 3},
                            "spec": {"replicas": 1},
                            "status": {"availableReplicas": 1, "observedGeneration": 2},
                        }
                    ),
                ),
                CommandResult(
                    0,
                    json.dumps(
                        {
                            "metadata": {"generation": 3},
                            "spec": {"replicas": 1},
                            "status": {"availableReplicas": 1, "observedGeneration": 3},
                        }
                    ),
                ),
            ]
        }
    )

    poll_deployment_ready(runner, namespace="ai-gateway", name="envoy-ratelimit", attempts=2)

    assert runner.calls == [command, command]


def test_condition_polling_rejects_zero_attempts() -> None:
    runner = FakeCommandRunner()

    with pytest.raises(VerificationError, match="attempts"):
        poll_gateway_programmed(runner, namespace="ai-gateway", name="ai-gateway", attempts=0)


def test_condition_polling_times_out_on_command_error() -> None:
    command = ("kubectl", "-n", "ai-gateway", "get", "gateway", "ai-gateway", "-o", "json")
    runner = FakeCommandRunner({command: CommandResult(1, stderr="not found")})

    with pytest.raises(VerificationError, match="not found"):
        poll_gateway_programmed(runner, namespace="ai-gateway", name="ai-gateway", attempts=1)


def test_condition_polling_rejects_invalid_json() -> None:
    command = ("kubectl", "-n", "ai-gateway", "get", "gateway", "ai-gateway", "-o", "json")
    runner = FakeCommandRunner({command: CommandResult(0, stdout="not-json")})

    with pytest.raises(VerificationError, match="invalid JSON"):
        poll_gateway_programmed(runner, namespace="ai-gateway", name="ai-gateway", attempts=1)


def test_condition_polling_rejects_non_object_json() -> None:
    command = ("kubectl", "-n", "ai-gateway", "get", "gateway", "ai-gateway", "-o", "json")
    runner = FakeCommandRunner({command: CommandResult(0, stdout="[]")})

    with pytest.raises(VerificationError, match="non-object"):
        poll_gateway_programmed(runner, namespace="ai-gateway", name="ai-gateway", attempts=1)


def test_httproute_polling_handles_malformed_parent_conditions() -> None:
    command = (
        "kubectl",
        "-n",
        "ai-gateway",
        "get",
        "httproute",
        "route-qwen-chat",
        "-o",
        "json",
    )
    runner = FakeCommandRunner(
        {
            command: CommandResult(
                0,
                stdout=json.dumps({"status": {"parents": ["bad", {"conditions": "bad"}]}}),
            )
        }
    )

    with pytest.raises(VerificationError, match="condition not ready"):
        poll_httproute_ready(runner, namespace="ai-gateway", name="route-qwen-chat", attempts=1)


def test_gateway_polling_handles_malformed_status_conditions() -> None:
    command = ("kubectl", "-n", "ai-gateway", "get", "gateway", "ai-gateway", "-o", "json")
    runner = FakeCommandRunner({command: CommandResult(0, stdout=json.dumps({"status": []}))})

    with pytest.raises(VerificationError, match="condition not ready"):
        poll_gateway_programmed(runner, namespace="ai-gateway", name="ai-gateway", attempts=1)


def test_deployment_polling_rejects_malformed_rollout_payloads() -> None:
    command = (
        "kubectl",
        "-n",
        "ai-gateway",
        "get",
        "deployment",
        "envoy-ratelimit",
        "-o",
        "json",
    )
    runner = FakeCommandRunner(
        {
            command: [
                CommandResult(0, stdout=json.dumps({"metadata": [], "spec": {}, "status": {}})),
                CommandResult(
                    0,
                    stdout=json.dumps(
                        {
                            "metadata": {"generation": "bad"},
                            "spec": {"replicas": 1},
                            "status": {"availableReplicas": 1, "observedGeneration": 1},
                        }
                    ),
                ),
            ]
        }
    )

    with pytest.raises(VerificationError, match="condition not ready"):
        poll_deployment_ready(runner, namespace="ai-gateway", name="envoy-ratelimit", attempts=2)
