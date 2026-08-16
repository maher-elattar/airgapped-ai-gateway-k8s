"""Static and runtime verification helpers."""

from __future__ import annotations

import json
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib import request

from airgap_ai_gateway.command import CommandResult
from airgap_ai_gateway.conditions import (
    poll_deployment_ready,
    poll_gateway_programmed,
    poll_httproute_ready,
    poll_policy_ready,
)
from airgap_ai_gateway.errors import VerificationError
from airgap_ai_gateway.models import ConsumerConfig, GatewayConfig, ModelConfig, ModelKind


class VerificationRunner(Protocol):
    """Command runner surface required by runtime verification."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        sensitive_output: bool = False,
    ) -> CommandResult:
        """Run a command."""


class HttpProbeFn(Protocol):
    """HTTP probe callable."""

    def __call__(
        self,
        url: str,
        *,
        host: str,
        api_key: str | None,
        kind: ModelKind,
        verify_tls: bool,
        timeout_seconds: int,
    ) -> tuple[int, dict[str, Any]]:
        """Run one HTTP probe."""


@dataclass(frozen=True, slots=True)
class RuntimeVerificationResult:
    """One runtime verification result."""

    name: str
    status: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-ready result."""

        return {"detail": self.detail, "name": self.name, "status": self.status}


def verification_plan(config: GatewayConfig) -> dict[str, object]:
    """Return the static verification plan shape."""

    return {
        "status": "static-verification-plan",
        "expected_context": config.platform.cluster.expected_context,
        "checks": [
            "gateway-programmed",
            "routes-attached",
            "policies-attached",
            "missing-key-401",
            "denied-consumer-403",
            "allowed-consumer-200",
            "rate-limit-429",
        ],
    }


@dataclass(frozen=True, slots=True)
class HttpProbeSpec:
    """HTTP probe settings for public verification paths."""

    url: str
    verify_tls: bool = True
    timeout_seconds: int = 30


class ContextVerifyingRunner:
    """Verify the exact Kubernetes context before every kubectl command."""

    def __init__(self, runner: VerificationRunner, *, expected_context: str) -> None:
        self.runner = runner
        self.expected_context = expected_context

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str | None = None,
        sensitive_output: bool = False,
    ) -> CommandResult:
        """Run a command after context verification."""

        if argv and argv[0] == "kubectl":
            current = self.runner.run(("kubectl", "config", "current-context"))
            actual = current.stdout.strip()
            print(
                f"verifying kubectl context before verification: expected {self.expected_context}"
            )
            if current.returncode != 0 or actual != self.expected_context:
                msg = f"context mismatch: expected {self.expected_context!r}, got {actual!r}"
                raise VerificationError(msg)
        return self.runner.run(
            argv,
            input_text=input_text,
            sensitive_output=sensitive_output,
        )


def run_runtime_verification(
    config: GatewayConfig,
    *,
    runner: VerificationRunner,
    expected_context: str,
    gateway_url: str,
    credentials: dict[str, str],
    wrong_host: str = "wrong.ai.example.internal",
    low_limit_consumer: str = "testing-client",
    http_probe: HttpProbeFn | None = None,
    verify_tls: bool = True,
    timeout_seconds: int = 30,
    rate_limit_attempts: int = 20,
) -> dict[str, object]:
    """Run runtime verification against an explicit gateway endpoint."""

    if expected_context != config.platform.cluster.expected_context:
        msg = "runtime verification refused: expected context does not match configuration"
        raise VerificationError(msg)
    verifying_runner = ContextVerifyingRunner(runner, expected_context=expected_context)
    namespace = config.platform.gateway.namespace
    results: list[RuntimeVerificationResult] = []

    _record(
        results,
        "Gateway Programmed=True",
        lambda: poll_gateway_programmed(
            verifying_runner,
            namespace=namespace,
            name=config.platform.gateway.name,
        ),
    )
    for model in config.models:
        route_name = f"route-{model.key}"
        policy_name = f"policy-{model.key}"

        def poll_route(route: str = route_name) -> None:
            poll_httproute_ready(
                verifying_runner,
                namespace=namespace,
                name=route,
            )

        def poll_policy(policy: str = policy_name) -> None:
            poll_policy_ready(
                verifying_runner,
                namespace=namespace,
                name=policy,
            )

        _record(
            results,
            f"HTTPRoute {route_name} ready",
            poll_route,
        )
        _record(
            results,
            f"AgentgatewayPolicy {policy_name} attached",
            poll_policy,
        )

    for deployment in ("envoy-ratelimit", "redis"):

        def poll_deployment(deployment_name: str = deployment) -> None:
            poll_deployment_ready(
                verifying_runner,
                namespace=namespace,
                name=deployment_name,
            )

        _record(
            results,
            f"Deployment {deployment} ready",
            poll_deployment,
        )

    probe = http_probe or default_http_probe
    first_model = config.models[0]
    results.append(
        _http_result(
            name="missing key returns 401",
            expected=401,
            actual=_probe_model(
                probe,
                gateway_url=gateway_url,
                model=first_model,
                api_key=None,
                verify_tls=verify_tls,
                timeout_seconds=timeout_seconds,
            ),
        )
    )
    results.append(
        _http_result(
            name="unknown key returns 401",
            expected=401,
            actual=_probe_model(
                probe,
                gateway_url=gateway_url,
                model=first_model,
                api_key=credentials.get("unknown", "example-only-do-not-use"),
                verify_tls=verify_tls,
                timeout_seconds=timeout_seconds,
            ),
        )
    )
    for model in config.models:
        allowed = _consumer_for_model(config, model, allowed=True)
        denied = _consumer_for_model(config, model, allowed=False)
        if allowed is not None and allowed.key in credentials:
            try:
                actual = _probe_model(
                    probe,
                    gateway_url=gateway_url,
                    model=model,
                    api_key=credentials[allowed.key],
                    verify_tls=verify_tls,
                    timeout_seconds=timeout_seconds,
                )
                if model.kind is ModelKind.EMBEDDING and actual[0] == 200:
                    verify_embedding_response(actual[0], actual[1])
                results.append(
                    _http_result(
                        name=f"allowed {allowed.key} reaches {model.key}",
                        expected=200,
                        actual=actual,
                    )
                )
            except VerificationError as exc:
                results.append(
                    RuntimeVerificationResult(
                        name=f"allowed {allowed.key} reaches {model.key}",
                        status="failed",
                        detail=str(exc),
                    )
                )
        if denied is not None and denied.key in credentials:
            results.append(
                _http_result(
                    name=f"denied {denied.key} blocked from {model.key}",
                    expected=403,
                    actual=_probe_model(
                        probe,
                        gateway_url=gateway_url,
                        model=model,
                        api_key=credentials[denied.key],
                        verify_tls=verify_tls,
                        timeout_seconds=timeout_seconds,
                    ),
                )
            )

    allowed_for_wrong_host = _consumer_for_model(config, first_model, allowed=True)
    results.append(
        _http_result(
            name="wrong Host returns 404",
            expected=404,
            actual=probe(
                f"{gateway_url.rstrip('/')}{first_model.route_path}",
                host=wrong_host,
                api_key=credentials.get(allowed_for_wrong_host.key)
                if allowed_for_wrong_host is not None
                else None,
                kind=first_model.kind,
                verify_tls=verify_tls,
                timeout_seconds=timeout_seconds,
            ),
        )
    )

    low_limit = next((item for item in config.consumers if item.key == low_limit_consumer), None)
    if low_limit is not None and low_limit.key in credentials and low_limit.allowed_models:
        low_model = next(
            (model for model in config.models if model.key == low_limit.allowed_models[0]),
            first_model,
        )
        statuses: list[int] = []
        for _ in range(rate_limit_attempts):
            http_status, _payload = _probe_model(
                probe,
                gateway_url=gateway_url,
                model=low_model,
                api_key=credentials[low_limit.key],
                verify_tls=verify_tls,
                timeout_seconds=timeout_seconds,
            )
            statuses.append(http_status)
            if http_status == 429:
                break
        results.append(
            RuntimeVerificationResult(
                name="low-limit consumer reaches 429",
                status="passed" if 429 in statuses else "failed",
                detail=f"statuses={statuses}",
            )
        )

    overall_status = "passed" if all(item.status == "passed" for item in results) else "failed"
    return {
        "expected_context": expected_context,
        "results": [item.to_dict() for item in results],
        "status": overall_status,
        "tls_verify": verify_tls,
    }


def verify_embedding_response(status_code: int, payload: dict[str, Any]) -> None:
    """Require a valid embedding vector in a successful embedding response."""

    if status_code != 200:
        msg = f"embedding verification expected HTTP 200, got {status_code}"
        raise VerificationError(msg)
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        msg = "embedding verification failed: response has no data array"
        raise VerificationError(msg)
    first = data[0]
    if not isinstance(first, dict):
        msg = "embedding verification failed: first data item is not an object"
        raise VerificationError(msg)
    embedding = first.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        msg = "embedding verification failed: HTTP 200 response did not include a vector"
        raise VerificationError(msg)
    if not all(isinstance(value, int | float) for value in embedding):
        msg = "embedding verification failed: vector contains non-numeric values"
        raise VerificationError(msg)


def default_http_probe(
    url: str,
    *,
    host: str,
    api_key: str | None,
    kind: ModelKind,
    verify_tls: bool,
    timeout_seconds: int,
) -> tuple[int, dict[str, Any]]:
    """Run one OpenAI-compatible HTTP request."""

    body: dict[str, object]
    if kind is ModelKind.EMBEDDING:
        body = {"input": "verification", "model": "verification"}
    else:
        body = {
            "messages": [{"role": "user", "content": "verification"}],
            "model": "verification",
        }
    headers = {"Content-Type": "application/json", "Host": host}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key
    req = request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    context = None if verify_tls else ssl._create_unverified_context()
    try:
        with request.urlopen(req, timeout=timeout_seconds, context=context) as response:
            return int(response.status), _json_response(response.read())
    except Exception as exc:  # pragma: no cover - exercised through fake probes
        status = getattr(exc, "code", None)
        if isinstance(status, int):
            payload = getattr(exc, "read", lambda: b"{}")()
            return status, _json_response(payload)
        raise VerificationError(f"HTTP probe failed for {host}: {exc}") from exc


def _record(
    results: list[RuntimeVerificationResult],
    name: str,
    callback: Callable[[], None],
) -> None:
    try:
        callback()
    except Exception as exc:
        results.append(RuntimeVerificationResult(name=name, status="failed", detail=str(exc)))
        return
    results.append(RuntimeVerificationResult(name=name, status="passed"))


def _probe_model(
    probe: HttpProbeFn,
    *,
    gateway_url: str,
    model: ModelConfig,
    api_key: str | None,
    verify_tls: bool,
    timeout_seconds: int,
) -> tuple[int, dict[str, Any]]:
    return probe(
        f"{gateway_url.rstrip('/')}{model.route_path}",
        host=model.host,
        api_key=api_key,
        kind=model.kind,
        verify_tls=verify_tls,
        timeout_seconds=timeout_seconds,
    )


def _http_result(
    *,
    name: str,
    expected: int,
    actual: tuple[int, dict[str, Any]],
) -> RuntimeVerificationResult:
    status, _payload = actual
    return RuntimeVerificationResult(
        name=name,
        status="passed" if status == expected else "failed",
        detail=f"expected={expected} actual={status}",
    )


def _consumer_for_model(
    config: GatewayConfig,
    model: ModelConfig,
    *,
    allowed: bool,
) -> ConsumerConfig | None:
    return next(
        (
            consumer
            for consumer in config.consumers
            if (model.key in consumer.allowed_models) is allowed
        ),
        None,
    )


def _json_response(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
