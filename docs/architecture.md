# Architecture Contract

This repository is a design contract for an air-gapped Kubernetes AI gateway platform. It is based on the reusable architecture and operating model from the source evidence, rewritten without environment identifiers, credentials, generated manifests, rendered CRDs, binary artifacts, or handover material.

## Evidence and scope

Evidence read for this design contract:

- Local Medium article HTML: `Building an Air-Gapped AI Gateway on Kubernetes with AgentGateway, Envoy, and NVIDIA NIM _ by Ahmed Maher _ Aug, 2026 _ Medium.html`
- Article URL: <https://medium.com/@ahmedmaherbf/building-an-air-gapped-ai-gateway-on-kubernetes-with-agentgateway-envoy-and-nvidia-nim-880141f333d5>
- Source archive: `source-evidence.zip`
- Reusable source categories: Python installers/runbooks, Markdown handover/runbooks, source review inventory, and official release documentation.

This phase defines architecture and security decisions only. It does not add implementation manifests or runtime scripts.

## Repository-level contract

All generated runtime resources are inspected but never manually maintained as the source of truth.

That contract has practical consequences:

- Checked-in source is the authority: documentation, future templates, future Kustomize or Helm values, and retained scripts.
- Controller-generated gateway resources, such as data-plane Deployments and Services, may be inspected for health and drift, but must not be permanently edited by ad hoc patching.
- If a generated resource needs different behavior, the change belongs in the declarative input that causes the controller to regenerate it.
- Rendered manifests and generated run directories are operational outputs, not normal Git source.
- Air-gap payloads such as image archives, chart archives, rendered third-party CRDs, DOCX/PDF handover files, and generated handover artifacts stay outside Git.

## Ownership and boundary model

| Area | Owner | Boundary |
| --- | --- | --- |
| Public or internal edge | Platform/network team | Retained NGINX or greenfield load balancer terminates the external path. It does not own model authorization or per-consumer AI policy. |
| Gateway API routing | Kubernetes platform team | GatewayClass, Gateway, and HTTPRoute express listener and HTTP routing intent. They do not know model permissions or consumer entitlements. |
| agentgateway AI policy | AI platform team | Agentgateway resources own API-key authentication, authorization, AI backend policy, rate-limit policy, and AI observability attributes. |
| agentgateway controller | Platform team | Control plane watches Kubernetes resources and reconciles generated gateway data-plane resources. It does not carry model request traffic. |
| Generated data plane | Controller-owned runtime | Proxy workload serves requests. Operators inspect it but do not manually maintain it as source of truth. |
| NVIDIA NIM services | Model platform or application team | NIM model lifecycle, GPU sizing, image choice, and model health are separate from gateway lifecycle. |
| Consumer identity | Application owner and platform security | One stable application/workload identity per consumer. Human identity is upstream context unless explicitly integrated later. |
| Runtime secrets | Security/platform operations | Secret material is injected at runtime through approved secret systems. It is never generated or stored in Git. |
| Rate-limit state | Platform operations | Rate-limit service evaluates descriptors; Redis stores counters. Availability requirements must match the production SLO. |

## Retained NGINX edge versus greenfield direct exposure

The design supports two exposure modes. The policy layer remains the same in both modes.

This is the retained NGINX edge versus greenfield direct exposure boundary.

### Retained NGINX edge

The delivered pattern kept the existing NGINX edge because it already owned public DNS, TLS, and network admission.

Request path:

```text
client
  -> existing NGINX or approved edge
  -> internal agentgateway data-plane Service
  -> selected NVIDIA NIM Kubernetes Service
```

In this mode:

- DNS and TLS stay with the existing edge.
- The generated gateway Service is internal, normally ClusterIP.
- Cutover changes the existing edge backend, not the model Services.
- Rollback restores the previous edge route to the original NIM Services.

### Greenfield direct exposure

In a new environment, NGINX is optional. The generated agentgateway data plane may be exposed through an approved Kubernetes north-south mechanism such as a load balancer or platform ingress.

In this mode:

- The Gateway and route/policy model does not change.
- The exposure mechanism must be designed as infrastructure source of truth.
- DNS, TLS, firewalling, and load-balancer ownership move to the new exposure layer.

The repository does not claim that either exposure mode is implemented yet. Future implementation must keep exposure configuration declarative.

## Gateway API routing versus agentgateway AI policy

Gateway API owns the routing contract:

- GatewayClass identifies the controller that implements Gateways.
- Gateway owns listener intent, accepted host scope, route attachment rules, and data-plane lifecycle.
- HTTPRoute owns host/path matching and backend routing.

agentgateway owns the AI platform contract:

- Consumer authentication.
- Per-model authorization.
- Chat provider backend configuration where supported.
- Rate-limit policy and descriptors.
- AI-aware telemetry and consumer identity attributes.

This separation keeps HTTP routing understandable as Kubernetes networking while keeping model permissions and AI-specific behavior out of generic edge configuration.

## Control plane versus generated data plane

The agentgateway controller and the generated gateway data plane are different workloads.

The controller:

- Watches Kubernetes resources.
- Reconciles Gateway API and agentgateway custom resources.
- Creates or updates the actual proxy runtime.
- Is the first place to inspect when a Gateway is not programmed.

The generated data plane:

- Receives model requests.
- Enforces configured policy.
- Routes to NIM or other backend Services.
- Is the first place to inspect when the Gateway is programmed but requests fail.

Generated data-plane resources are operational state. They are not the permanent configuration layer.

## Model routing contract

The baseline uses one Gateway for the AI platform and separate routes per model.

This is intentional:

- A single Gateway gives one stable platform entry point.
- A separate HTTPRoute per model gives a direct model-to-policy relationship.
- Per-route policies make 403 failures easier to diagnose.
- Adding models scales by adding a model route and model policy, not by expanding a monolithic route.

The first implementation keeps Gateway, model routes, and model backend references in one namespace. If future teams split models across namespaces, ReferenceGrant and ownership rules must become explicit parts of the trust model.

## Chat backends versus embedding backends

The delivered baseline is agentgateway v1.3.1.

In that tested path:

- OpenAI-compatible chat models are represented with AgentgatewayBackend as AI provider backends.
- Embedding endpoints are routed through agentgateway directly to their Kubernetes Service backend instead of being forced into the chat-style AgentgatewayBackend abstraction.

The reason is functional, not aesthetic. Embedding requests do not use the same chat message structure, and the v1.3.1 behavior tested in the source evidence treated the OpenAI-compatible backend path as chat-oriented. The embedding route still passes through agentgateway and still receives authentication, authorization, rate limiting, and observability. Only the final backend representation differs.

Future agentgateway versions may improve this behavior, but the baseline does not change until tests prove compatibility.

## Published API surface

A NIM Service having an endpoint does not mean every endpoint should become consumer-facing.

Default rule:

- Publish only the inference paths the platform intends applications to use.
- Do not publish model listing, health, metrics, or administrative endpoints through the consumer API by default.
- Onboard every model with default-deny authorization for existing consumers.

## Authentication, authorization, rate limiting, and observability

Authentication answers: is this caller known?

Authorization answers: is this known caller allowed to use this model route?

Rate limiting answers: how much of the allowed capability has this caller consumed in the current window?

Observability answers: which application identity, model, route, status, and policy path produced this traffic?

These controls belong at the gateway layer so that each NIM deployment does not need to reimplement them.

## Application identity versus human identity

The baseline identity is application identity:

- One API key or credential record per workload/application.
- Consumer metadata describes stable application-level identity and allowed models.
- Metrics use bounded application identity dimensions, not arbitrary per-person prompt labels.

Human identity is not ignored, but it is a separate integration:

- A backend application may map human sessions to its own authorization model.
- The gateway receives the backend application identity unless a later design adds user delegation or token exchange.
- Long-lived gateway keys must not be shipped in browser JavaScript.

## Gateway lifecycle versus NVIDIA NIM lifecycle

The gateway lifecycle and model lifecycle are separate.

Gateway lifecycle:

- Gateway API and agentgateway CRDs.
- agentgateway controller.
- generated data plane.
- routes, policies, consumer metadata, rate limits, and cutover.

NIM lifecycle:

- model image and serving runtime.
- GPU scheduling and capacity.
- model readiness, endpoint shape, and performance.
- model upgrades or decommissioning.

The gateway should first verify that NIM Services and endpoints are healthy before adding policy in front of them. A broken model should not be hidden behind a new gateway layer.

## Operating sequence

The intended operating model is staged:

1. Discover existing model Services, edge routes, and relevant cluster state.
2. Build and verify the air-gap dependency graph before touching a cluster.
3. Install CRDs and control plane from a tested version set.
4. Create an internal Gateway and verify the generated data plane.
5. Add consumer identity, route authorization, rate limits, and telemetry attributes.
6. Test the internal gateway path before public cutover.
7. Cut over the retained edge or greenfield exposure layer.
8. Keep rollback focused on restoring the previous traffic path first.

No phase may leave kubectl patch, kubectl edit, or kubectl set as the source of truth.
