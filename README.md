# Air-Gapped AI Gateway Platform

| <img src="docs/assets/logos/kubernetes.svg" alt="Kubernetes logo" width="42"> | <img src="docs/assets/logos/agentgateway.svg" alt="agentgateway logo" width="42"> | <img src="docs/assets/logos/envoy.svg" alt="Envoy logo" width="42"> | <img src="docs/assets/logos/nvidia.svg" alt="NVIDIA logo" width="42"> | <img src="docs/assets/logos/redis.svg" alt="Redis logo" width="42"> | <img src="docs/assets/logos/nginx.svg" alt="NGINX logo" width="42"> | <img src="docs/assets/logos/python.svg" alt="Python logo" width="42"> | <img src="docs/assets/logos/github-actions.svg" alt="GitHub Actions logo" width="42"> |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Kubernetes | agentgateway | Envoy | NVIDIA NIM | Redis | NGINX | Python | GitHub Actions |

Getting a model serving traffic inside Kubernetes is usually the easy part.

Things get harder once there are several models, several applications that each
need different access to them, no registry access during the maintenance window,
and a production traffic path you are not allowed to break. A Service and an
Ingress will move the packets, but they will not answer the questions that come
up in every review before the change is approved:

- Which workload is calling the model?
- Which models is that workload allowed to use?
- Which route, backend, and policy handled the request?
- Which rate limit applies before a single consumer exhausts the model?
- Which image, chart, CRD, and wheel entered the disconnected environment?
- What proof exists before traffic moves?
- What can be rolled back without touching the model runtime?

This repository is my answer to those questions as a working Kubernetes
reference implementation. Gateway API carries the routing contract, agentgateway
carries the AI policy layer, Envoy-style rate limiting backed by Redis handles
the demo quota path, Kustomize holds the authored manifests, a typed Python CLI
plans and executes changes, and a disposable kind lab proves the behavior.

The versions are pinned on purpose:

- agentgateway v1.3.1 is the delivered baseline.
- Gateway API v1.5.0 experimental is the delivered Gateway API track.
- agentgateway v1.4.0 stays a separate validation target until tests prove
  hashed-key authentication, metadata propagation, authorization, rate limits,
  observability, Gateway API changes, rollback, and documentation behavior.

What comes out of that is a repeatable pattern for air-gapped AI access: one
gateway entry point, one route per model, one policy boundary per route,
per-workload identity, offline dependencies resolved ahead of time, runtime
secrets kept out of Git, and rollback that respects who owns the models.

## The sequence

Treat this repository as a sequence rather than a pile of YAML. The local lab
and the production reference both follow the same path: prepare the dependency
set, render the source of truth, plan the change, apply only the approved plan,
verify the internal gateway path, cut over traffic, keep rollback ready.

![Discover, apply, verify, cutover, and rollback sequence](docs/assets/diagrams/rendered/deployment-sequence.svg)

Start with the local proof path:

```bash
python -m pip install -c constraints.txt -r requirements-dev.txt -e .
make airgap-demo
make render
make validate
make kind-test
```

The same command family drives an environment-specific rollout:

```bash
airgap-ai-gateway --config examples/config discover

airgap-ai-gateway --config examples/config deploy plan \
  --overlay retained-nginx-edge \
  --apply-mode server-side-dry-run \
  --output-dir runs/plans/deploy

airgap-ai-gateway --config examples/config deploy apply \
  --expected-context kind-airgap-ai-gateway \
  --apply-mode server-side-dry-run \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY \
  --plan-file runs/plans/deploy/plan.json \
  --snapshot-file runs/snapshots/pre-change.json \
  --commands-log runs/reports/deploy/commands.log
```

That shape is not accidental. Planning stays offline and reviewable. Applying is
gated. Verification is a separate step. Cutover is separate again. Rollback reads
the state ledger instead of guessing which resources are safe to delete.

## What this solution provides

| Capability | Implementation | Primary reference |
| --- | --- | --- |
| Gateway contract | One Gateway with one HTTPRoute per model-facing API | [Architecture contract](docs/architecture.md) |
| AI policy | Route-scoped authentication, authorization, rate-limit metadata, and metrics | [Security model](docs/security-model.md) |
| Chat models | OpenAI-compatible chat routes through `AgentgatewayBackend` | [Model onboarding](docs/model-onboarding.md) |
| Embeddings | OpenAI-compatible embedding route through a Kubernetes Service backend in the tested baseline | [ADR 0002](docs/adr/0002-chat-vs-embedding-backends.md) |
| Application identity | One stable consumer identity per workload | [Consumer lifecycle](docs/consumer-lifecycle.md) |
| Declarative manifests | Kustomize bases and overlays for demo, retained-edge, and production-reference paths | [Manifest source](manifests/baseline-v1.3.1) |
| Air-gap supply chain | Immutable source lock, bundle build, offline verify, registry promotion, and rendered-manifest proof | [Air-gap bundle workflow](docs/airgap-bundle.md) |
| Safe operations | Deterministic plans, pre-change snapshots, state ledger, redacted reports, and gated apply | [Deployment](docs/deployment.md) |
| Runtime proof | Disposable kind cluster, local registry, mock OpenAI-compatible Services, and request matrix | [Disposable gateway lab](docs/kind-e2e-lab.md) |
| Recovery | State-aware rollback and decommission order | [Rollback guide](docs/rollback.md) |

## The traffic path

The first decision is not the controller, the chart, or the manifest layout. It
is the traffic contract.

Before the gateway exists, applications call model Services directly or through
whatever edge is already in place. That works for one model and one caller. It
starts to hurt when every application needs different model access, different
limits, and different evidence during an incident, because the model runtime
ends up carrying responsibilities that belong at the platform boundary.

After the gateway, applications keep one stable entry point, and identity, route
selection, entitlement, quota, and observability are enforced in one place. The
model Service goes back to doing what it is good at, which is inference. It does
not become an authorization system, a rate-limit system, or a cutover mechanism.

![Opening architecture showing retained edge and gateway-controlled model access](docs/assets/diagrams/article/01-opening-architecture.png)

The same idea appears below as a repository-authored Mermaid diagram. The
article-style image tells the architecture story; the Mermaid version is the one
to edit when the documentation changes.

![Before and after traffic architecture](docs/assets/diagrams/rendered/before-after-traffic-architecture.svg)

Where an NGINX edge is already in the path, it can keep owning DNS, TLS, and the
external entry point. The gateway goes in behind it and gets tested before the
edge forwards anything real. In a greenfield environment the gateway can be the
direct north-south exposure point, but only once load balancer, firewall, DNS,
TLS, and rollback ownership have been designed rather than assumed.

Both paths land on the same internal contract:

- Gateway API owns listener and HTTPRoute behavior.
- agentgateway owns AI-specific policy.
- The controller reconciles the declared state.
- The data plane serves the request path.
- Model Services stay focused on inference.

Reference: [docs/architecture.md](docs/architecture.md).

## Run the gateway as the policy boundary

The gateway should own the decisions every model-facing request needs and that
no individual model should have to reimplement.

It owns authentication at the application boundary. A workload presents a runtime
credential, and the gateway maps it to a stable consumer identity. That identity
is not a person. It is the platform identity of an application or workload. Human
login stays with the application backend unless someone designs and tests a
separate user-delegation model.

It owns authorization at the model boundary. A valid key does not mean access to
every model. Each model route carries its own policy, so the same workload can
get `200` on one model and `403` on another. That is the design working, not a
bug report.

It owns rate-limit descriptors and observability metadata. The `consumer_id` that
drives authorization is the same one that shows up in quota decisions and in
metrics, which is what makes troubleshooting follow a straight line instead of a
guess.

It does not own the model lifecycle. NVIDIA NIM Services keep responsibility for
model images, readiness, GPU scheduling, scaling, and model-specific behavior.
The gateway checks that a backend is healthy before publishing a route to it, but
it should never hide a broken model behind new routing.

![Authentication, authorization, rate-limit, and backend decision flow](docs/assets/diagrams/rendered/policy-decision-flow.svg)

That ownership boundary is also why runtime proxy objects stay separate from the
authored source here. Generated proxy resources are for inspection during
verification. Lasting changes go through configuration, manifests, policies,
overlays, and tests.

References:

- [Architecture contract](docs/architecture.md)
- [Security model](docs/security-model.md)
- [ADR 0001: One Gateway, separate model routes](docs/adr/0001-one-gateway-separate-model-routes.md)

## Inspect control plane and data plane separately

The controller is not the gateway carrying model traffic.

The controller watches Gateway API and agentgateway resources, validates the
desired state, and reconciles the runtime proxy objects. The generated data plane
is what actually handles HTTP requests. Treating the two as one thing makes
troubleshooting much harder than it needs to be.

If the Gateway is not programmed, the questions are about CRDs, the controller,
GatewayClass, parameters, and reconciliation. If the Gateway is programmed and
requests still fail, the questions move to Host matching, route attachment,
policy attachment, rate-limit state, backend resolution, and model health.

![Control plane and data plane separation](docs/assets/diagrams/article/03-control-plane-data-plane.png)

The editable version below keeps the same distinction and is easier to evolve as
the repository changes.

![Mermaid control plane and data plane flow](docs/assets/diagrams/rendered/control-plane-data-plane.svg)

That split drives the CLI and manifest design:

- `discover` is read-only and reports current state.
- `render` works offline and produces deterministic output.
- `deploy plan` produces a JSON plan and a Markdown summary.
- `deploy apply` executes only the actions in an approved plan.
- `verify` checks conditions and request behavior.
- `rollback apply` restores or removes resources based on the state ledger.

Create the deploy plan before any cluster-changing step:

```bash
airgap-ai-gateway --config examples/config deploy plan \
  --overlay retained-nginx-edge \
  --apply-mode server-side-dry-run \
  --output-dir runs/plans/deploy
```

This is also why `kubectl patch`, `kubectl set image`, and live edits are not the
operating model. They are fine while you are investigating an incident at 2 a.m.
They are not the source of truth for the platform.

Reference: [docs/architecture.md](docs/architecture.md).

## Verify the supported baseline

Version discipline is part of the design.

The baseline is agentgateway v1.3.1 with Gateway API v1.5.0 experimental. That is
not a claim that newer upstream versions are worse. It is a claim about what has
been proven here, because support is not a sentence in a README. Support is the
set of manifests, configuration, behaviors, and tests that show the version does
what the documentation says it does.

The v1.4.0 track matters because it advertises security-relevant behavior such as
hashed-key support. That is worth validating, and it is exactly why it should not
quietly replace the baseline until the full path holds:

- hashed-key authentication works for the intended routes;
- metadata still drives authorization, rate limits, and observability;
- Gateway API version changes are handled cleanly;
- rollback still works;
- docs and examples no longer imply raw-key runtime storage.

Until that is true, v1.4.0 is a validation target and nothing more.

The compatibility matrix records the delivered version, supported API, status,
evidence, and upgrade risk per component: agentgateway, Gateway API, chat routes,
embedding routes, the retained NGINX edge, greenfield exposure, generated
data-plane resources, Redis, Envoy ratelimit, Kubernetes, and Helm chart inputs.

Run the baseline checks from source:

```bash
make test
make validate
airgap-ai-gateway --config examples/config verify \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --registry registry.example.internal:5000
```

Reference: [docs/compatibility.md](docs/compatibility.md).

## Render the Kubernetes manifests

The Kubernetes source of truth lives under
[manifests/baseline-v1.3.1](manifests/baseline-v1.3.1).

The structure is boring: bases describe the platform objects, overlays describe
the environment shape. Boring is the point. It means the route, policy, backend,
image map, NetworkPolicy, ServiceAccount, and availability assumptions can all be
read and argued about before anything renders.

The baseline includes:

- an agentgateway namespace and configuration inputs;
- `AgentgatewayParameters` and `Gateway`;
- chat backend definitions;
- embedding Service backend routes;
- one HTTPRoute per model;
- one authorization policy per model route;
- gateway-level consumer metrics;
- Redis and Envoy ratelimit demo resources;
- owned Services, ConfigMaps, PDBs, NetworkPolicies, ServiceAccounts, security
  contexts, resource requests, and topology constraints.

The overlays are split by purpose:

| Overlay | Purpose | Notes |
| --- | --- | --- |
| `kind-demo` | Small static demo render | Single replica and demo-only labels |
| `kind-e2e-lab` | Disposable behavioral proof | Adds mock model Services and local-registry images |
| `retained-nginx-edge` | Brownfield edge migration | Keeps edge cutover separate from gateway install |
| `production-reference` | Production-oriented shape | Makes HA and persistence decisions explicit |

The production reference does not pretend that one Redis Pod is highly available.
It points at the external HA Redis contract you need once rate-limit state
matters beyond a local demo.

Validation rejects public registry references, mutable production tags,
unprotected routes, ambiguous Service ports, missing route policies,
cross-namespace references without explicit trust, and rendered Secret data.

Render and validate the authored overlays:

```bash
make render
make validate
python scripts/validate_manifests.py
```

For a production-reference render, the proof is not that YAML appeared. The proof
is that routes are protected, images resolve through the internal registry map,
tags are immutable, and a normal render contains no Secret values.

Reference: [manifests/README.md](manifests/README.md).

## Run the disposable gateway lab

A gateway project needs a real behavioral test, not only rendered YAML.

The disposable lab builds a uniquely named kind cluster and a local registry,
then brings up repository-owned OpenAI-compatible mock Services for Qwen chat,
Gemma chat, and embeddings. The embedding mock returns a deterministic non-empty
vector, so a `200` on the embedding route proves an actual embedding response
rather than any successful HTTP response.

![Direct internal test path](docs/assets/diagrams/article/11-direct-internal-test-path.png)

From there the lab installs the pinned Gateway API and agentgateway compatibility
set, deploys the three-model demo overlay, creates runtime-only fake credentials
outside tracked paths, and sends requests through the internal gateway before any
retained-edge path is involved.

```bash
python -m pip install -c constraints.txt -r requirements-dev.txt -e .
make kind-test
```

Optional retained-edge pass:

```bash
python scripts/kind_e2e_lab.py run --with-nginx
```

The lab writes JUnit, JSON, and Markdown evidence. It verifies the exact kind
context before every `kubectl` call and tears down only the uniquely named
cluster it created.

Reference: [docs/kind-e2e-lab.md](docs/kind-e2e-lab.md).

## Build and verify the air-gap bundle

In an air-gapped cluster, dependency management is not an afterthought. It is
part of the architecture.

The dependency set covers Gateway API CRDs, agentgateway CRD and controller
charts, the controller image, the data-plane image, Redis, the Envoy ratelimit
image, CLI wheels, required tools, and the fixture images the lab needs. Every
entry is pinned in [airgap/sources.lock.yaml](airgap/sources.lock.yaml) with its
version, canonical source, destination name, checksum or OCI digest, provenance
note, license note, and compatibility-set membership.

![Air-gap dependency graph](docs/assets/diagrams/article/05-airgap-dependency-graph.png)

The workflow has two sides. On the connected side the bundle is assembled and
checked. On the disconnected side the same bundle is verified without a single
network request, promoted into an internal registry, and matched against the
rendered manifests.

![Connected-side to offline-side supply chain](docs/assets/diagrams/rendered/airgap-supply-chain.svg)

Run the local demonstration:

```bash
make airgap-demo
```

Or run the pieces separately when reviewing the supply chain:

```bash
airgap-ai-gateway bundle build \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --registry registry.example.internal:5000 \
  --dist-dir dist/airgap-demo

airgap-ai-gateway bundle verify \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --registry registry.example.internal:5000 \
  --bundle-dir dist/airgap-demo/baseline-v1.3.1

airgap-ai-gateway --config examples/config registry promote \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --registry registry.example.internal:5000 \
  --output-file dist/airgap-demo/promotion-plan.json
```

The workflow prefers OCI-native tooling such as `skopeo` and OCI archives where
that is available, with a documented Docker fallback for environments that do not
have the OCI path. For multi-node clusters the strategy is an internal registry
every node can reach, not loading images onto one node and hoping the scheduler
stays put.

Reference: [docs/airgap-bundle.md](docs/airgap-bundle.md).

## Plan, apply, verify, then cut over

Deployment and cutover are separate operations.

Installation prepares the gateway. Cutover changes the request path. Keeping them
apart is what keeps the production window small. If the gateway cannot pass its
internal tests, the edge never moves at all. If the edge cutover fails after the
internal tests passed, the first rollback action is to restore the previous edge
path, not to start uninstalling the gateway.

![Cutover path through the retained edge](docs/assets/diagrams/article/13-cutover-path.png)

The CLI makes that separation explicit:

```bash
airgap-ai-gateway --config examples/config deploy plan \
  --overlay retained-nginx-edge \
  --apply-mode server-side-dry-run \
  --output-dir runs/plans/deploy
```

The plan comes out as two artifacts:

- `plan.json`, the deterministic executor contract;
- `plan.md`, the human summary.

State-changing operations require the exact expected context, an apply mode, a
confirmation token, the approved plan, and a saved pre-change snapshot.

```bash
airgap-ai-gateway --config examples/config deploy apply \
  --expected-context kind-airgap-ai-gateway \
  --apply-mode server-side-dry-run \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY \
  --plan-file runs/plans/deploy/plan.json \
  --snapshot-file runs/snapshots/pre-change.json \
  --commands-log runs/reports/deploy/commands.log
```

Once the internal gateway path passes, cutover is planned and applied on its own:

```bash
airgap-ai-gateway --config examples/config cutover plan \
  --overlay retained-nginx-edge \
  --apply-mode server-side-dry-run \
  --output-dir runs/plans/cutover

airgap-ai-gateway --config examples/config cutover apply \
  --expected-context kind-airgap-ai-gateway \
  --apply-mode server-side-dry-run \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY \
  --plan-file runs/plans/cutover/plan.json \
  --snapshot-file runs/snapshots/pre-cutover.json \
  --commands-log runs/reports/cutover/commands.log
```

References:

- [Deployment guide](docs/deployment.md)
- [ADR 0003: Staged cutover and rollback](docs/adr/0003-staged-cutover-and-rollback.md)

## Verify the request matrix

Ready Pods prove almost nothing here.

The gateway is a policy boundary, so the proof has to include the failures. A
protected system needs to show that the wrong request fails for the right reason
just as clearly as it shows that the allowed request succeeds.

![Policy test matrix](docs/assets/diagrams/article/12-policy-test-matrix.png)

| Request | Expected signal | What it proves |
| --- | --- | --- |
| No API key | 401 | Anonymous access is closed |
| Unknown API key | 401 | Only known runtime credentials work |
| Qwen consumer with Qwen grant | 200 | Chat route, backend, and policy align |
| Valid consumer without Qwen grant | 403 | Entitlement is route-specific |
| Gemma consumer with Gemma grant | 200 | The second chat route is independent |
| Embedding consumer with embedding grant | 200 with vector length greater than zero | Embedding response shape is validated |
| Valid consumer without embedding grant | 403 | Embedding access is not broad |
| Dedicated low-limit consumer under repeated traffic | 429 | Descriptor and counter path are active |
| Wrong Host header | 404 | Host and route matching protect the boundary |
| Broken backend route | Diagnostic condition or expected upstream failure | Backend errors stay visible |
| Gateway cleanup | Model Services remain present | Gateway lifecycle is separate from model lifecycle |

Runtime verification also polls bounded Kubernetes conditions:

- Gateway `Programmed=True`;
- HTTPRoute `Accepted=True`;
- HTTPRoute `ResolvedRefs=True`;
- AgentgatewayPolicy `Accepted=True`;
- AgentgatewayPolicy `Attached=True`;
- Deployment rollout readiness.

Run the static and runtime proof:

```bash
make lint
make test
make validate
make kind-test
```

Reference: [docs/verification.md](docs/verification.md).

## Add a model with default deny

Adding a model should feel like adding a platform capability, not like writing a
one-off migration.

The model key is the anchor. It drives the route name, backend name, policy name,
permission field, labels, tests, and report fields. Get the key right and the
rest of the platform stays readable.

![Model resource naming](docs/assets/diagrams/article/14-model-resource-naming.png)

In the tested baseline, chat and embedding backends are handled differently on
purpose:

- OpenAI-compatible chat models use `AgentgatewayBackend`.
- OpenAI-compatible embeddings route through the gateway to a Kubernetes Service
  backend.

![Chat versus embedding backend](docs/assets/diagrams/article/04-chat-vs-embedding-backend.png)

Every new model starts denied for existing consumers. The onboarding path is:

1. verify the model Service directly;
2. add the backend representation;
3. add the HTTPRoute;
4. attach the route policy;
5. verify existing consumers receive `403`;
6. grant one selected consumer;
7. verify that consumer receives `200`;
8. verify unrelated consumers still receive `403`;
9. add rate-limit entries explicitly;
10. keep the proof in the test suite.

![Model onboarding with default deny](docs/assets/diagrams/rendered/model-onboarding-default-deny.svg)

Preview the generated chat-model onboarding shape from an existing example model:

```bash
airgap-ai-gateway --config examples/config model add --model-key qwen-chat
```

For a new model, add the model contract to configuration, add or render the
route/backend/policy source, and rerun validation before granting any consumer.

References:

- [Model onboarding guide](docs/model-onboarding.md)
- [ADR 0002: Chat vs embedding backends](docs/adr/0002-chat-vs-embedding-backends.md)

## Add, rotate, and revoke consumers

A consumer is a workload identity.

That sounds like a small definition, but it is the difference between a gateway
you can operate and a gateway that only forwards traffic. The consumer identity
is what ties together authorization, rate limits, metrics, rotation, disablement,
revocation, and troubleshooting.

![API key metadata becoming gateway consumer identity](docs/assets/diagrams/article/08-api-key-metadata.png)

A useful consumer record has a stable key, a display name, a runtime credential
reference, an allowed model list, a rate-limit tier or explicit limits, and
enough workload metadata to identify it later. The credential value itself is not
repository source. It belongs in whatever runtime secret system the environment
already uses.

Rotation should support overlap:

1. add a new credential for the same consumer identity;
2. move the application to the new credential;
3. verify traffic and metrics still use the same `consumer_id`;
4. disable or remove the old credential.

Disable and revoke are different operations. Disable keeps the identity record
and removes model access. Revoke invalidates the credential material. Disable is
what you want for audit continuity. Revoke is what you need when a credential is
exposed, or when you only suspect it is.

![Consumer disable versus revoke](docs/assets/diagrams/article/16-consumer-disable-vs-revoke.png)

Long-lived gateway keys do not belong in browser JavaScript. The pattern is:

```text
browser -> application backend -> gateway -> model
```

Use the CLI to produce the consumer operation plan, then update the runtime
credential material through the environment's secret workflow:

```bash
airgap-ai-gateway --config examples/config consumer add --consumer-key search-app
airgap-ai-gateway --config examples/config consumer rotate --consumer-key search-app
airgap-ai-gateway --config examples/config consumer revoke --consumer-key search-app
```

Reference: [docs/consumer-lifecycle.md](docs/consumer-lifecycle.md).

## Troubleshoot from response and status signals

Start from what the platform is already telling you.

A `401` is an identity problem. A `403` is an entitlement problem. A `404` is a
Host, listener, or route match problem. A `429` means the descriptor and counter
path is alive and doing its job. `ResolvedRefs=False`, `Attached=False`, and
`Programmed=False` are not interchangeable either; each one points at a different
ownership boundary.

![HTTP response troubleshooting](docs/assets/diagrams/article/17-http-response-troubleshooting.png)

The editable troubleshooting flow stays in Mermaid because operators end up
updating it whenever a new condition, status, or policy type shows up.

![HTTP and status troubleshooting flow](docs/assets/diagrams/rendered/http-status-troubleshooting-flow.svg)

Use the response code first, then the conditions:

- `401`: check missing key, unknown key, header format, runtime Secret, and
  credential reference.
- `403`: check known consumer, permission field, policy target, and default-deny
  behavior.
- `404`: check Host header, listener hostname, route hostname, path match, and
  retained-edge Host preservation.
- `429`: check descriptor metadata, rate-limit service health, Redis reachability,
  and low-limit test isolation.
- `ResolvedRefs=False`: check Service name, namespace, port, ReferenceGrant mode,
  and CRD compatibility.
- `Attached=False`: check policy `targetRef`, namespace, route existence, and
  controller logs.
- `Programmed=False`: check GatewayClass, CRDs, controller readiness, parameters,
  generated data-plane rollout, and NetworkPolicies.

Reference: [docs/troubleshooting.md](docs/troubleshooting.md).

## Roll back without deleting model workloads

Rollback has to know who owns what.

The state ledger records whether a resource was created by the run, updated by
the run, or already existed before it. That single distinction is what stops a
rollback from deleting resources the gateway operation never owned.

If a model Service existed before the gateway route was added, rollback removes
the route and policy the run created and leaves the Service alone. If a Secret
existed before the run and was changed through the approved workflow, rollback
restores the recorded pre-change state instead of treating it as disposable.

![Decommission order](docs/assets/diagrams/article/19-decommission-order.png)

The rollback order is conservative on purpose:

1. restore the previous edge or exposure path;
2. verify client traffic reaches the previous backend;
3. keep the gateway installed if it helps diagnostics;
4. restore updated resources from the snapshot;
5. delete only resources proven to have been created by the selected run;
6. remove gateway resources only after traffic no longer depends on them.

The cleanup guard fails closed when edge or ingress state cannot be read. If the
platform cannot prove that traffic no longer depends on the gateway, it does not
get to remove gateway resources.

Plan rollback from the saved state ledger:

```bash
airgap-ai-gateway --config examples/config rollback plan \
  --ledger-file runs/reports/deploy/ledger.json \
  --run-id run-1 \
  --apply-mode server-side-dry-run \
  --output-dir runs/plans/rollback
```

Apply only with the approved rollback plan, exact context, snapshot, and ledger:

```bash
airgap-ai-gateway --config examples/config rollback apply \
  --expected-context kind-airgap-ai-gateway \
  --apply-mode server-side-dry-run \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY \
  --plan-file runs/plans/rollback/plan.json \
  --snapshot-file runs/snapshots/pre-change.json \
  --ledger-file runs/reports/deploy/ledger.json \
  --commands-log runs/reports/rollback/commands.log
```

Reference: [docs/rollback.md](docs/rollback.md).

## Architecture decisions

The ADRs are the design memory of this repository. They hold the decisions that
should not be buried in YAML or left implied by a test name.

Current decisions:

- [ADR 0001: One Gateway, separate model routes](docs/adr/0001-one-gateway-separate-model-routes.md)
- [ADR 0002: Chat versus embedding backends](docs/adr/0002-chat-vs-embedding-backends.md)
- [ADR 0003: Staged cutover and rollback](docs/adr/0003-staged-cutover-and-rollback.md)
- [ADR 0004: Air-gap artifacts outside Git](docs/adr/0004-airgap-artifacts-outside-git.md)
- [ADR 0005: Secret management boundary](docs/adr/0005-secret-management-boundary.md)

Read these before changing a platform boundary. If a later implementation needs a
different route shape, backend pattern, cutover sequence, artifact boundary, or
secret model, it should update the relevant ADR rather than only changing
manifests.

Reference: [docs/adr](docs/adr).

## Repository layout

```text
.
├── airgap/                         # Source lock and example bundle reports
├── docs/                           # Architecture, security, runbooks, ADRs, and assets
├── examples/config/                # Example platform, model, consumer, and registry config
├── lab/                            # Mock OpenAI-compatible services and E2E fixtures
├── manifests/baseline-v1.3.1/      # Kustomize source of truth for the tested baseline
├── scripts/                        # Validation, lab, asset, and link-check scripts
├── src/airgap_ai_gateway/          # Typed CLI, planner, executor, verifier, bundle logic
└── tests/                          # Unit, regression, manifest, and lab safety tests
```

Source, tests, documentation, schemas, and example reports are tracked. Runtime
credentials, kubeconfig material, generated run directories, OCI archives, chart
archives, wheelhouses, and offline bundle payloads are not.

## Development workflow

Install the project:

```bash
python -m pip install -c constraints.txt -r requirements-dev.txt -e .
```

Inspect the CLI:

```bash
airgap-ai-gateway --help
airgap-ai-gateway deploy plan --help
airgap-ai-gateway deploy apply --help
```

Run static checks:

```bash
make lint
make test
make validate
make render
make security-scan
```

Run documentation checks:

```bash
make diagrams
make docs-check
python scripts/check_links.py --check-external README.md CONTRIBUTING.md SECURITY.md CHANGELOG.md docs manifests lab
```

Run the full local lab when Kubernetes behavior changes:

```bash
make kind-test
```

Contribution expectations are described in [CONTRIBUTING.md](CONTRIBUTING.md).

## Security policy

Security-sensitive changes include authentication, authorization, Secret
handling, redaction, image provenance, registry promotion, context verification,
apply gates, rollback behavior, and route exposure.

The security policy covers the supported baseline, reporting expectations,
secret-handling rules, and the checks that should run before review. The standard
is simple: examples can be fake, but the behavior has to be real enough to test.

Reference: [SECURITY.md](SECURITY.md).

## Changelog

Notable changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## Article and upstream references

For the full implementation story and the reasoning behind the sequence:

- [Building an Air-Gapped AI Gateway on Kubernetes with AgentGateway, Envoy, and NVIDIA NIM](https://medium.com/@ahmedmaherbf/building-an-air-gapped-ai-gateway-on-kubernetes-with-agentgateway-envoy-and-nvidia-nim-880141f333d5)

Core upstream projects:

| Component | Reference |
| --- | --- |
| <img src="docs/assets/logos/agentgateway.svg" alt="agentgateway logo" width="20"> agentgateway | [agentgateway documentation](https://agentgateway.dev/) |
| <img src="docs/assets/logos/kubernetes.svg" alt="Kubernetes logo" width="20"> Kubernetes Gateway API | [Gateway API project](https://gateway-api.sigs.k8s.io/) |
| <img src="docs/assets/logos/envoy.svg" alt="Envoy logo" width="20"> Envoy | [Envoy Proxy](https://www.envoyproxy.io/) |
| <img src="docs/assets/logos/nvidia.svg" alt="NVIDIA logo" width="20"> NVIDIA NIM | [NVIDIA NIM](https://www.nvidia.com/en-us/ai/) |
| <img src="docs/assets/logos/redis.svg" alt="Redis logo" width="20"> Redis | [Redis](https://redis.io/) |
| <img src="docs/assets/logos/nginx.svg" alt="NGINX logo" width="20"> NGINX | [NGINX](https://nginx.org/) |
| <img src="docs/assets/logos/python.svg" alt="Python logo" width="20"> Python | [Python](https://www.python.org/) |
| <img src="docs/assets/logos/github-actions.svg" alt="GitHub Actions logo" width="20"> GitHub Actions | [GitHub Actions](https://docs.github.com/actions) |
