"""Model onboarding render helpers."""

from __future__ import annotations

import yaml

from airgap_ai_gateway.errors import PlanError
from airgap_ai_gateway.manifest import Manifest
from airgap_ai_gateway.models import ModelConfig, ModelKind


def render_chat_model_onboarding(model: ModelConfig, *, namespace: str) -> str:
    """Render clean YAML documents for onboarding one chat model."""

    if model.kind is not ModelKind.CHAT:
        msg = f"model {model.key} is not a chat model"
        raise PlanError(msg)
    documents: list[Manifest] = [
        {
            "apiVersion": "agentgateway.dev/v1alpha1",
            "kind": "AgentgatewayBackend",
            "metadata": {
                "name": f"{model.key}-backend",
                "namespace": namespace,
                "labels": {
                    "ai.gateway/model-key": model.key,
                    "ai.gateway/api-shape": model.kind.value,
                },
            },
            "spec": {
                "schema": {
                    "name": "openai",
                },
                "backend": {
                    "host": f"{model.service.name}.{model.service.namespace}.svc.cluster.local",
                    "port": model.service.ports[0].number,
                },
            },
        },
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {
                "name": f"{model.key}-route",
                "namespace": namespace,
                "labels": {
                    "ai.gateway/model-key": model.key,
                    "ai.gateway/api-shape": model.kind.value,
                },
            },
            "spec": {
                "hostnames": [model.host],
                "rules": [
                    {
                        "matches": [{"path": {"type": "PathPrefix", "value": model.route_path}}],
                        "backendRefs": [
                            {
                                "group": "agentgateway.dev",
                                "kind": "AgentgatewayBackend",
                                "name": f"{model.key}-backend",
                            }
                        ],
                    }
                ],
            },
        },
        {
            "apiVersion": "agentgateway.dev/v1alpha1",
            "kind": "AgentgatewayPolicy",
            "metadata": {
                "name": f"{model.key}-policy",
                "namespace": namespace,
                "labels": {
                    "ai.gateway/model-key": model.key,
                    "ai.gateway/api-shape": model.kind.value,
                },
            },
            "spec": {
                "targetRefs": [
                    {
                        "group": "gateway.networking.k8s.io",
                        "kind": "HTTPRoute",
                        "name": f"{model.key}-route",
                    }
                ],
                "traffic": {
                    "apiKeyAuthentication": {
                        "mode": "Strict",
                        "secretRef": {"name": "consumer-api-keys-runtime"},
                    },
                    "authorization": {
                        "action": "Require",
                        "policy": {
                            "matchExpressions": [
                                {
                                    "key": f"metadata.permissions.{model.permission}",
                                    "operator": "In",
                                    "values": ["true"],
                                }
                            ]
                        },
                    },
                },
            },
        },
    ]
    return yaml.safe_dump_all(documents, explicit_start=True, sort_keys=False)
