# Architecture Contract

The point of this platform is not to put a proxy in front of some model Services.

Kubernetes Services and whatever edge is already installed can move HTTP traffic
around perfectly well. The interesting problem starts when several internal
applications need several AI models, and someone has to answer these with a
straight face:

- Which application called which model?
- Is that application allowed to use this model?
- How much traffic can it send?
- What happens when one consumer has to be disabled or rotated?
- How does the whole path get installed in a disconnected environment without
  depending on a public registry during the maintenance window?

This document is the contract behind the answers: one AI gateway, explicit model
routes, application-level consumer identity, default-deny model onboarding,
staged cutover, and an air-gap supply chain you can reproduce.

## Visual architecture sequence

The diagrams are stored in this repository so the documentation does not depend
on remote image hosting. They illustrate the architecture and are not vendor
endorsements.

### 1. Where the AI Gateway fits

![Opening architecture showing retained edge and greenfield exposure options](assets/diagrams/article/01-opening-architecture.png)

The editable view keeps the same before-and-after boundary in Mermaid source, so
the diagram can move with the repository:

![Before and after traffic architecture](assets/diagrams/rendered/before-after-traffic-architecture.svg)

The outer edge can change without changing the AI policy boundary.

In a brownfield environment the existing NGINX edge keeps DNS, TLS, and public
routing. In a greenfield environment agentgateway can be exposed directly through
an approved Kubernetes north-south path. Either way, the decision that matters is
this: once traffic reaches agentgateway, identity, model authorization, rate
limits, routing, and observability belong to the gateway layer.

### 2. Start with the model contract

![Model contract mapping model names to API type, API path, and Kubernetes backend](assets/diagrams/article/02-model-contract.png)

Every model needs a clear contract before it is published:

- model key;
- API type;
- request path;
- Kubernetes backend;
- consumer-facing purpose.

Writing that down is what stops every endpoint a NIM Service happens to expose
from becoming part of the API. A model may serve health, metadata, metrics, and
operational paths. Those can be useful internally. They are not automatically
part of the application-facing surface.

### 3. Separate the control plane from the data plane

![agentgateway control plane and data plane separation](assets/diagrams/article/03-control-plane-data-plane.png)

The same separation is kept as an editable control-plane/data-plane flow:

![Mermaid control plane and data plane flow](assets/diagrams/rendered/control-plane-data-plane.svg)

The agentgateway controller is not the gateway carrying model traffic.

The controller watches Kubernetes resources and reconciles the desired state. The
generated data-plane proxy serves the requests. That distinction pays for itself
the first time something breaks:

- if the Gateway is not programmed, start with the controller and the resources
  it watches;
- if the Gateway is programmed but requests fail, go to the data plane, routes,
  policies, rate-limit path, or backend Services.

The data plane is reconciled from the declared Gateway resources. Inspect the
runtime objects freely during troubleshooting; keep durable changes in the
authored inputs.

### 4. Treat chat and embedding APIs differently

![Chat models using AgentgatewayBackend and embedding models using direct Kubernetes Service routing](assets/diagrams/article/04-chat-vs-embedding-backend.png)

Chat completions and embeddings are both AI APIs, but they do not share backend
semantics.

In the tested agentgateway v1.3.1 baseline:

- OpenAI-compatible chat models use AgentgatewayBackend as an AI provider
  backend.
- Embedding models route through agentgateway to the normal Kubernetes Service
  backend.

The embedding route still gets authentication, authorization, rate limiting, and
observability. Only the final backend representation is different.

### 5. Build the air-gap dependency graph before installation

![Air-gap dependency graph showing artifacts crossing into the offline environment](assets/diagrams/article/05-airgap-dependency-graph.png)

On an internet-connected cluster, a single `helm install` can hide a surprising
number of external dependencies. In an air-gapped cluster nothing gets assumed.

The offline package is really a dependency graph. It has to account for Gateway
API CRDs, agentgateway CRDs, controller images, proxy images, Redis, rate-limit
service images, manifests, chart inputs where those are allowed, rendered output
where they are not, and checksums. Verify the package before it crosses the
boundary, then verify it again before it is used.

The rule is short: runtime workloads pull from the internal registry, never from
the public internet.

### 6. Creating the Gateway creates the data plane

![Internal Gateway creation and generated data-plane resources](assets/diagrams/article/06-internal-gateway.png)

The Gateway is declarative intent. The controller turns that intent into a
running proxy.

With a retained edge, the generated Service can stay internal, and the public
edge only forwards to it after internal tests pass. Removing the edge in a
greenfield environment changes the exposure mechanism, but the route and policy
model should not have to change with it.

## Repository-level authoring contract

The repository defines the platform inputs and the validation rules:

- Checked-in source is documentation, schemas, Kustomize manifests, validation
  scripts, and tests.
- GitOps source is an optional delivery layer that points Argo CD at the same
  authored Kustomize overlays.
- Controller-reconciled resources are for health checks and troubleshooting.
- Persistent behavior changes go into the source inputs that produce runtime
  state.
- Rendered manifests, generated run directories, and offline bundle payloads are
  operational outputs.
- Runtime credentials and binary air-gap payloads live in environment-specific
  systems, not here.

## GitOps reconciliation boundary

Argo CD can own steady-state reconciliation without changing the architecture.
It watches a managed overlay, applies the declared gateway resources, and reports
drift. The platform team still owns the promotion decision: dependencies must be
in the internal registry, manifests must pass validation, runtime Secrets must
exist through the environment secret workflow, and the request matrix must pass
after reconciliation.

The Argo CD Application does not install model runtimes or carry credential
values. It reconciles the gateway layer around them.

## Retained NGINX edge versus greenfield direct exposure

The retained edge and the AI policy layer are two separate decisions.

Retained NGINX edge:

```text
client
  -> existing NGINX or approved edge
  -> internal agentgateway data-plane Service
  -> selected NVIDIA NIM Kubernetes Service
```

NGINX keeps public DNS, TLS, and existing network admission. Cutover changes the
edge backend. Rollback restores the previous edge route to the original NIM
Services.

Greenfield direct exposure:

```text
client
  -> approved load balancer or Kubernetes exposure path
  -> agentgateway data plane
  -> selected NVIDIA NIM Kubernetes Service
```

The Gateway, routes, and policies stay identical. What changes is everything
around them: load balancer, firewall, DNS, TLS, and who owns the exposure.

## Gateway API routing versus agentgateway AI policy

Gateway API is the routing contract.

GatewayClass identifies the controller that implements the Gateway. Gateway
describes the listener and the route attachment rules. HTTPRoute describes host
and path routing.

agentgateway adds the AI-specific layer on top: consumer authentication, model
authorization, AI backend behavior, rate-limit policy, and AI observability
attributes.

That split is the whole idea. HTTP routing stays visible as Kubernetes
networking, and model policy does not get buried inside a generic reverse-proxy
configuration where nobody will find it six months later.

## One Gateway, separate model routes

![Consumer capability matrix across separate model routes](assets/diagrams/article/07-consumer-capability-matrix.png)

The baseline uses one Gateway for the AI platform and one HTTPRoute per
model-facing API.

That keeps the relationship between a model and the permission to use it direct.
When an application can call one model and gets 403 on another, there is exactly
one route and one policy to look at.

The first implementation keeps the Gateway, model routes, and model backend
references in a single namespace. If model teams later own separate namespaces,
ReferenceGrant and namespace ownership become part of the trust model.
Cross-namespace routing has to be explicit; it is not something a route should
start doing by accident.

## Application identity versus human identity

![API key metadata becoming gateway consumer identity](assets/diagrams/article/08-api-key-metadata.png)

The baseline identity is application identity.

One stable consumer record belongs to one workload. That identity carries
metadata such as consumer name, team, environment, model permissions, and
rate-limit tier.

Human identity is a different problem and should be treated as one. A browser
user authenticates to an application backend; the gateway should never receive a
long-lived model key straight from browser JavaScript. The backend owns the user
session and calls the gateway with its application credential, unless someone
adds and tests a real user-delegation design later.

## Authentication, authorization, rate limiting, and observability

![Rate-limit descriptor flow from authenticated consumer metadata to counters](assets/diagrams/article/09-rate-limit-descriptor-flow.png)

The request decision path is also kept as an editable Mermaid diagram for
operator documentation:

![Request evaluation through routing, authentication, authorization, rate limiting, and backend dispatch](assets/diagrams/rendered/policy-decision-flow.svg)

Each control answers one question:

- Authentication: do I know this caller?
- Authorization: is this known caller allowed to use this route?
- Rate limiting: how much of the allowed capability has this caller used already?
- Observability: which application identity, model, route, status, and policy
  path produced this request?

They belong at the gateway layer so that every NIM deployment does not end up
implementing its own version of each one.

![Consumer identity attached to gateway metrics](assets/diagrams/article/10-consumer-metrics.png)

Consumer identity should stay a bounded metric dimension. It identifies stable
workloads, not arbitrary people and not request payload contents.

## Prove the policy path before changing production traffic

![Direct internal gateway test path before public edge cutover](assets/diagrams/article/11-direct-internal-test-path.png)

Test the gateway path internally before the public edge changes.

That means proving the generated data plane, Host header behavior, route
matching, authentication, authorization, model routing, and rate limits with no
DNS, TLS, external load balancer, or legacy ingress behavior in the way. If
something fails at this stage, there is exactly one layer to blame.

![Policy test matrix with expected 401, 403, 200, and 429 outcomes](assets/diagrams/article/12-policy-test-matrix.png)

Successful requests are only half the test. The platform also has to prove the
expected failures:

- no key fails with 401;
- unknown key fails with 401;
- known consumer without model permission fails with 403;
- unknown host or path fails as routing, not identity;
- rate-limit exhaustion returns 429;
- authorized model requests return success from the intended backend.

## Keep cutover smaller than deployment

![Cutover path changing the edge backend without changing model hosts](assets/diagrams/article/13-cutover-path.png)

Cutover should be the smaller of the two operations.

If the gateway is installed and internally verified, the production change is
only the edge path. With a retained edge, DNS and TLS do not move at all; the
existing hosts point at the gateway instead of directly at NIM Services.

When public traffic fails after cutover, the first rollback action is not to
uninstall the gateway. It is to restore the previous edge backend so the known
working path comes back quickly, and then investigate with the pressure off.

## Gateway lifecycle versus NVIDIA NIM lifecycle

![Model resource naming derived from one model key across gateway resources](assets/diagrams/article/14-model-resource-naming.png)

These are separate lifecycles and should stay that way.

Gateway lifecycle: Gateway API CRDs, agentgateway CRDs, controller, generated
data plane, routes, policies, consumer metadata, rate limits, observability,
cutover, rollback, and cleanup.

NIM lifecycle: model image choice, GPU scheduling, serving runtime, model
readiness, endpoint behavior, scaling, and model decommissioning.

The gateway should confirm a NIM Service is healthy before publishing it. It
should never hide a broken model behind a new policy layer.

## New models start denied

![Default-deny model onboarding showing existing consumers denied until explicitly granted](assets/diagrams/article/15-default-deny-model-onboarding.png)

Adding a model must not hand anyone new permissions by accident.

The safe order is:

1. verify the NIM Service works;
2. create the model route and backend representation;
3. keep existing consumers denied;
4. grant selected consumers explicitly;
5. add rate-limit entries explicitly;
6. test allowed, denied, unknown, and rate-limited paths.

Done this way, onboarding a model is a normal platform operation instead of a
one-off production change.

## API keys have a lifecycle

![Consumer API-key lifecycle showing create, grant, active, rotate, and revoke states](assets/diagrams/article/16-consumer-disable-vs-revoke.png)

Disabling a consumer and revoking a key are not the same thing.

Disabling preserves the consumer record and its audit meaning while removing
model permissions. Revoking removes or invalidates the credential material.
Rotation should overlap: add the new credential, move the application, verify
traffic, then remove the old one.

Long-lived API keys do not belong in public frontend JavaScript. The browser
calls an application backend, and the backend holds the application credential
through an approved runtime secret path.

## Troubleshooting starts from the response code

Each control fails with a distinct signal, so the response code identifies the
responsible layer before any manifest is opened:

| Response | Most likely cause | Where to investigate |
| --- | --- | --- |
| `200` | Request passed policy and the model responded | No action required |
| `400` | Request body does not match the model API | Client payload shape against the model's expected schema |
| `401` | Missing, invalid, or revoked key | Header format, runtime Secret, credential reference |
| `403` | Valid consumer, model permission denied | Consumer permission field, policy target, default-deny state |
| `404` | Host or path did not match a route | Host header, listener hostname, route hostname and path, edge Host preservation |
| `405` | Wrong HTTP method or endpoint | Route path and method match against the published model API |
| `429` | Consumer rate limit enforced | Descriptor metadata, ratelimit service health, Redis reachability |
| `500` / `502` / `503` | Gateway, Service, or model backend fault | Data-plane health, backend Service endpoints, model readiness |
| Timeout | Network path, or a slow or unhealthy model | NetworkPolicies, model resource pressure, upstream timeouts |

![Rate-limit troubleshooting flow ordered by response code, route state, policy target, and backend health](assets/diagrams/article/18-rate-limit-troubleshooting.png)

Rate-limit failures follow their own order: route match, policy target,
descriptor shape, rate-limit service, Redis state, then client retry behavior.

## Decommissioning starts with traffic dependency

![Gateway decommission order preserving model workloads until traffic is drained](assets/diagrams/article/19-decommission-order.png)

Decommissioning the gateway does not start by deleting resources.

It starts by checking whether live traffic still depends on it. Then move traffic
away, back up policy and consumer state, remove gateway-layer resources, and
leave the NIM workloads alone unless the model lifecycle separately calls for
removing them.

The gateway owns policy and routing. It does not own the models.

## Final architecture

![Final air-gapped AI gateway architecture for NVIDIA NIM](assets/diagrams/article/20-final-architecture.png)

Put together, the platform has a clear shape:

- applications call a stable AI gateway path;
- the edge is either retained NGINX or a greenfield exposure path;
- Gateway API expresses listener and HTTP routing intent;
- agentgateway enforces AI-specific policy;
- the controller reconciles;
- the generated data plane serves;
- Redis and the rate-limit service hold quota state;
- NIM Services stay focused on inference;
- private registry artifacts keep the runtime air-gapped;
- operational changes stay declarative and rollback-aware.

That is the contract to preserve as more implementation lands.
