# Model Onboarding Guide

Adding another model should feel like adding another platform capability, not
like writing a special-case migration script.

![Default-deny model onboarding](assets/diagrams/article/15-default-deny-model-onboarding.png)

The maintained onboarding flow is rendered from Mermaid source:

![Six-phase default-deny model onboarding with verification gates](assets/diagrams/rendered/model-onboarding-default-deny.svg)

## Start with one model key

Pick a stable key:

```text
qwen-chat
gemma-chat
embedding-index
```

That key anchors:

- Service labels.
- HTTPRoute name.
- Backend name.
- Policy name.
- Permission field.
- Rate-limit descriptor.
- Test names.
- Report fields.

Get the key clean and the rest of the resources stay readable. This matters much
more at ten models than it does at three.

## Decide the API shape

| API shape | Baseline backend pattern |
| --- | --- |
| Chat completions | AgentgatewayBackend with OpenAI-compatible provider settings |
| Embeddings | Kubernetes Service backend through the gateway route |

This is tested v1.3.1 behavior. Do not assume a newer backend model works until
the compatibility track proves it.

## Add the Service contract

The gateway routes to a Kubernetes Service. Whatever runs behind that Service
stays owned by the model platform.

Keep the contract simple:

- One clear HTTP port.
- Same namespace, unless an explicit ReferenceGrant mode is enabled.
- No admin or operational endpoints published through the model route.

## Add route and policy together

Every model needs both:

- One HTTPRoute.
- One route-targeted policy.

An unprotected route is not valid. A policy with no healthy route behind it is
not useful. Treat the pair as a single model-facing unit and review them
together.

The CLI produces the source-change plan for the model contract, route, backend,
policy, Kustomize resource list, and optional initial consumer grant:

```bash
airgap-ai-gateway --config examples/config model add plan \
  --model-key falcon-chat \
  --display-name "Falcon Chat" \
  --kind chat \
  --host falcon-chat.ai.example.internal \
  --route-path /v1/falcon/chat/completions \
  --permission model:falcon-chat:invoke \
  --service-name falcon-chat-nim \
  --service-namespace ai-gateway \
  --service-port 8000 \
  --grant-consumer internal-chat \
  --output-dir runs/plans/model-falcon-chat
```

Review `runs/plans/model-falcon-chat/plan.md` and `plan.json`. Apply only the
approved source plan:

```bash
airgap-ai-gateway --config examples/config model add apply \
  --plan-file runs/plans/model-falcon-chat/plan.json \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY
```

This updates repository source files. It does not create runtime credentials and
it does not contact Kubernetes.

## Keep default-deny

A new model grants nothing to existing consumers. The flow is:

1. Add the model route and backend.
2. Attach the policy with no broad grants.
3. Verify existing consumers receive 403.
4. Grant one selected consumer.
5. Verify that consumer receives 200.
6. Verify unrelated consumers still receive 403.

## Validate before release

```bash
make lint
make test
make validate
```

For the full local proof:

```bash
make kind-test
```

For embeddings, a 200 is not sufficient on its own. The response has to contain a
non-empty vector, or all you have proven is that some HTTP handler answered.

## Merge checklist

- Model key is unique.
- Host is unique.
- Route path is what you meant it to be.
- Permission field is unique.
- Service port is unambiguous.
- Route has an attached policy.
- Strict air-gap output uses private immutable image references.
- Allowed, denied, unknown, and rate-limited paths are all tested.
