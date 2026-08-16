# Air-Gapped AI Gateway Platform

| <img src="docs/assets/logos/kubernetes.svg" alt="Kubernetes logo" width="42"> | <img src="docs/assets/logos/agentgateway.svg" alt="agentgateway logo" width="42"> | <img src="docs/assets/logos/argocd.svg" alt="Argo CD logo" width="42"> | <img src="docs/assets/logos/envoy.svg" alt="Envoy logo" width="42"> | <img src="docs/assets/logos/nvidia.svg" alt="NVIDIA logo" width="42"> | <img src="docs/assets/logos/redis.svg" alt="Redis logo" width="42"> | <img src="docs/assets/logos/nginx.svg" alt="NGINX logo" width="42"> | <img src="docs/assets/logos/python.svg" alt="Python logo" width="42"> | <img src="docs/assets/logos/github-actions.svg" alt="GitHub Actions logo" width="42"> |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Kubernetes | agentgateway | Argo CD | Envoy | NVIDIA NIM | Redis | NGINX | Python | GitHub Actions |

A reference implementation of a governed internal AI API platform for Kubernetes
clusters that have no internet access. It places a policy-enforcing gateway in
front of self-hosted inference services, gives every calling application its own
identity and model entitlements, and delivers the whole stack through an offline
supply chain with reviewable, reversible change control.

---

## Contents

1. [Problem statement](#1-problem-statement)
2. [Solution overview](#2-solution-overview)
3. [Architecture](#3-architecture)
4. [Supported baseline](#4-supported-baseline)
5. [Operational sequence](#5-operational-sequence)
6. [Deployment steps](#6-deployment-steps)
7. [GitOps with Argo CD](#7-gitops-with-argo-cd)
8. [Verification](#8-verification)
9. [Platform operations](#9-platform-operations)
10. [Repository layout](#10-repository-layout)
11. [Development workflow](#11-development-workflow)
12. [Continuous integration](#12-continuous-integration)
13. [Security](#13-security)
14. [Architecture decisions](#14-architecture-decisions)
15. [References](#15-references)

---

## 1. Problem statement

### 1.1 Operating context

An air-gapped environment is a network with no route to the public internet.
Regulated sectors such as banking, government, defence, and healthcare run this
way by policy: every container image, Helm chart, Custom Resource Definition
(CRD), and software dependency must be reviewed and imported deliberately before
a workload can use it.

Organisations in these sectors increasingly self-host large language models
rather than call an external API, because prompts and responses cannot leave the
network. NVIDIA NIM is one common way to do this — it packages a model behind an
OpenAI-compatible HTTP interface, so applications call familiar endpoints such as
`/v1/chat/completions` and `/v1/embeddings` against a service running inside the
cluster.

### 1.2 What breaks at scale

A single model serving a single application needs no platform layer. A Kubernetes
Service and an Ingress are sufficient. The architecture stops holding once the
environment contains several models and several consuming applications, because
the following questions have no owner:

| Question | Consequence when unanswered |
| --- | --- |
| Which application issued this request? | No attribution, no usage accounting, no audit trail |
| Which models may that application call? | Any workload with network reach can call any model |
| How is access revoked for one application? | Revocation requires changing the model deployment itself |
| What limits one consumer's throughput? | A single caller can exhaust GPU capacity for everyone |
| Which route, policy, and backend served the request? | Incidents are debugged by inspecting several layers at once |
| How does any of this get installed offline? | Installation depends on registries the cluster cannot reach |

Answering these inside each model deployment does not scale. Every new model
would reimplement authentication, entitlement, quota, and telemetry, and each
implementation would drift from the others.

### 1.3 Derived requirements

The platform therefore has to provide:

- **Identity** — a stable, per-workload credential resolved to a named consumer.
- **Authorization** — per-model entitlement, denied by default.
- **Quota** — throughput limits attributed to the consumer, not the source IP.
- **Observability** — consistent identity across policy decisions and metrics.
- **Offline delivery** — every dependency resolved, verified, and imported before
  installation begins.
- **Controlled change** — planned, gated, verifiable, and reversible operations
  against production traffic.

---

## 2. Solution overview

### 2.1 What the platform delivers

The solution introduces one gateway as the single entry point for internal model
traffic. Applications no longer address model services directly. They present a
credential to the gateway, which resolves it to a consumer identity, checks that
consumer's entitlement for the requested model, applies the relevant rate limit,
records telemetry, and only then forwards the request to the model service.

Around that runtime behaviour, the repository provides the delivery and
operational machinery: declarative manifests as the source of truth, an offline
dependency bundle with checksum verification, a command-line tool that plans
changes before applying them, a disposable test cluster that proves policy
behaviour, and rollback driven by recorded resource ownership.

### 2.2 Design principles

| Principle | Rationale |
| --- | --- |
| One gateway, one route per model | Keeps the relationship between a model and its policy direct and auditable |
| Default deny on model onboarding | Adding a model never widens an existing consumer's access |
| Application identity, not human identity | The gateway authenticates workloads; user sessions remain the application's concern |
| Separate installation from cutover | The gateway is proven internally before production traffic moves |
| Declarative source of truth | Runtime objects are inspected for health; durable change happens in authored inputs |
| Ownership-aware rollback | Recovery removes only what a run created and never the model workloads |
| Pinned, tested version sets | Support is defined by passing tests, not by a compatibility claim in prose |

---

## 3. Architecture

### 3.1 Component inventory

The platform composes upstream projects rather than replacing them. Each
component owns one concern:

| Component | Role in the solution |
| --- | --- |
| **Kubernetes Gateway API** | The routing contract. Successor to Ingress, splitting responsibilities across `GatewayClass` (which controller implements it), `Gateway` (the listener), and `HTTPRoute` (host and path rules) |
| **agentgateway** | The AI policy layer. Adds custom resources for consumer authentication, per-model authorization, AI backend behaviour, rate-limit policy, and telemetry attributes |
| **Argo CD** | Optional GitOps reconciliation. Watches the approved branch and converges the cluster to it continuously; treated as an existing cluster service, not installed by this repository |
| **Envoy** | The proxy technology underneath the gateway data plane that carries model traffic |
| **NVIDIA NIM** | The model runtime. Serves OpenAI-compatible chat and embedding APIs; owned by the model platform, not by this gateway |
| **Envoy ratelimit + Redis** | The quota service and its counter store, evaluating rate-limit descriptors emitted by gateway policy |
| **NGINX** | Optional retained edge. Where one already terminates public DNS and TLS, it is kept and repointed rather than replaced |
| **Kustomize manifests** | The declarative source of truth, organised as bases plus environment overlays |
| **Python CLI** | Discovery, rendering, planning, gated apply, verification, rollback, and offline bundle handling |
| **kind lab** | A disposable local cluster that proves policy behaviour with mock model services |

### 3.2 Request path

Before the gateway is introduced, applications reach model services directly or
through whatever edge already exists. Afterwards, applications keep one stable
entry point and the platform enforces identity, routing, entitlement, quota, and
telemetry in a single place. The model service returns to serving inference only.

![Opening architecture showing retained edge and gateway-controlled model access](docs/assets/diagrams/article/01-opening-architecture.png)

The same transition, as a maintained diagram source:

![Before and after traffic architecture](docs/assets/diagrams/rendered/before-after-traffic-architecture.svg)

Two exposure patterns are supported, and the choice does not change the internal
design:

| Pattern | External entry point | When it applies |
| --- | --- | --- |
| Retained edge | Existing NGINX keeps DNS, TLS, and public routing; forwards to the internal gateway Service | Brownfield environments with a working production edge |
| Direct exposure | The gateway data plane is published through an approved load balancer or ingress path | Greenfield environments, once DNS, TLS, firewall, and rollback ownership are designed |

Both converge on the same internal contract: Gateway API owns listener and route
behaviour, agentgateway owns AI policy, the controller reconciles declared state,
the data plane serves requests, and model services stay focused on inference.

Reference: [docs/architecture.md](docs/architecture.md).

### 3.3 Control plane and data plane

agentgateway runs as two distinct workloads, and separating them is essential for
diagnosis. The **controller** watches Kubernetes resources and reconciles desired
state; it does not carry model traffic. The **data plane** is the proxy the
controller generates from a `Gateway` resource, and it is what actually serves
requests.

![Control plane and data plane separation](docs/assets/diagrams/article/03-control-plane-data-plane.png)

![Mermaid control plane and data plane flow](docs/assets/diagrams/rendered/control-plane-data-plane.svg)

The distinction determines where an investigation starts:

| Symptom | Layer to investigate first |
| --- | --- |
| `Gateway` never reaches `Programmed=True` | CRDs, controller readiness, `GatewayClass`, parameters, reconciliation |
| `Gateway` is programmed but requests fail | Host matching, route attachment, policy attachment, rate-limit state, backend resolution, model health |

Because the data plane is generated, it is treated as output. Editing the
generated Deployment directly creates drift that reconciliation may overwrite;
durable changes belong in the authored manifests and configuration.

### 3.4 Chat and embedding backends

Chat completions and embeddings are both OpenAI-compatible, but they are not
interchangeable request shapes. A chat request carries a `messages[]` array; an
embedding request carries `input`. In the tested baseline they therefore use
different backend representations:

![Chat versus embedding backend](docs/assets/diagrams/article/04-chat-vs-embedding-backend.png)

| API | Backend representation |
| --- | --- |
| `/v1/chat/completions` | `AgentgatewayBackend` configured as an OpenAI-compatible AI provider |
| `/v1/embeddings` | Kubernetes Service backend, routed through the gateway |

The embedding route still receives authentication, authorization, rate limiting,
and telemetry. Only the final backend representation differs. This is
version-specific behaviour and is retested on upgrade.

Reference: [ADR 0002](docs/adr/0002-chat-vs-embedding-backends.md).

## 4. Supported baseline

Support in this repository means a version set whose behaviour is proven by
manifests, configuration, and passing tests — not a compatibility claim in
documentation. The delivered baseline is:

| Component | Version | Status |
| --- | --- | --- |
| agentgateway | v1.3.1 | Delivered baseline |
| Gateway API | v1.5.0 experimental | Delivered baseline |
| agentgateway | v1.4.0 | Validation target, not supported |

agentgateway v1.4.0 advertises security-relevant behaviour, notably API keys
stored as SHA-256 hashes rather than raw values. That is a desirable improvement,
so it is tracked deliberately rather than adopted silently. It becomes the
baseline once tests prove hashed-key authentication on the intended routes,
continued metadata propagation into authorization and quota and telemetry, clean
handling of the Gateway API version change, reliable rollback, and documentation
that no longer implies raw-key storage.

Run the baseline checks from source:

```bash
make test
make validate
airgap-ai-gateway --config examples/config verify \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --registry registry.example.internal:5000
```

The full matrix — delivered version, supported API, status, evidence, and upgrade
risk for every component — is in [docs/compatibility.md](docs/compatibility.md).

---

## 5. Operational sequence

The platform is operated as an ordered sequence rather than a set of manifests
applied at once. The same sequence is used by the local lab and by an
environment-specific rollout. Read-only and offline stages carry no risk to the
cluster; mutating stages are gated; rollback is a first-class stage rather than
an improvised recovery.

![Discover, plan, apply, verify, cut over, and roll back sequence](docs/assets/diagrams/rendered/deployment-sequence.svg)

| Stage | Purpose | Cluster impact |
| --- | --- | --- |
| 1. Discover | Read the current model, route, and edge state | None — read-only |
| 2. Plan | Render the overlay and produce a reviewable plan | None — offline |
| 3. Apply | Execute only the approved plan, after snapshotting | Installs the gateway; production traffic unchanged |
| 4. Verify | Prove conditions and the request matrix internally | None — reads and test requests |
| 5. Cut over | Repoint the edge to the gateway Service | Changes the production request path |
| 6. Roll back | Restore the previous path, then remove owned resources | Recovery only |

The separation between stages 3 and 5 is the central operational decision. If the
gateway fails its internal tests, production traffic never moves. If the cutover
fails after those tests passed, the fault is isolated to the edge layer, and the
first recovery action is to restore the previous edge backend rather than to
uninstall the gateway.

### 5.1 Command surface

Every operation that changes anything is a `plan` / `apply` pair. Planning is
offline and produces a reviewable artefact; applying executes only what the
approved plan contains. No operation mutates state as a side effect of being
invoked.

| Operation | Plan command | Apply command | What `apply` changes |
| --- | --- | --- | --- |
| Gateway install | `deploy plan` | `deploy apply` | Cluster resources |
| Traffic cutover | `cutover plan` | `cutover apply` | Cluster resources |
| Recovery | `rollback plan` | `rollback apply` | Cluster resources |
| Decommission | `destroy plan` | `destroy apply` | Cluster resources |
| Argo CD bootstrap | `gitops plan` | `gitops apply` | Argo CD AppProject and Application |
| Model onboarding | `model add plan` | `model add apply` | Repository source files |
| Consumer add | `consumer add plan` | `consumer add apply` | Repository source files |
| Consumer rotate | `consumer rotate plan` | `consumer rotate apply` | Repository source files |
| Consumer revoke | `consumer revoke plan` | `consumer revoke apply` | Repository source files |
| Image promotion | `registry promote plan` | `registry promote apply` | Internal registry contents |

The distinction in the last column matters more than it may appear. The model
and consumer commands are **source-side automation**: they generate and modify
the authored manifests and configuration in the repository, and they never
contact Kubernetes or create credential material. Their output is a change to
review and commit, which then reaches a cluster through the ordinary deploy
path. The deploy, cutover, rollback, destroy, and GitOps bootstrap commands are **cluster-side**
and carry the full safety gate — expected context, apply mode, confirmation
token, and pre-change snapshot.

Source-side applies carry a different guarantee. The plan records a content hash
for every file it intends to write, and `apply` refuses to proceed if any of
those files changed after the plan was reviewed. Approving a diff and applying a
different one is therefore not possible.

Two supporting commands complete the surface:

| Command | Purpose |
| --- | --- |
| `snapshot create` | Capture the pre-change state of exactly the resources an approved plan names |
| `verify static` / `verify runtime` | Prove the source offline, or exercise the request matrix against a live gateway |

---

## 6. Deployment steps

### 6.1 Local proof path

The fastest way to evaluate the solution is the disposable local lab, which
requires no GPUs, no model weights, and no NVIDIA images:

```bash
python -m pip install -c constraints.txt -r requirements-dev.txt -e .
make airgap-demo
make render
make validate
make kind-test
```

This builds and verifies an offline bundle, renders the manifests, validates
them, then creates a temporary kind cluster with mock OpenAI-compatible model
services and runs the full policy matrix against it.

### 6.2 Prepare the offline bundle

In a disconnected environment, dependency resolution happens before installation
rather than during it. The dependency set covers Gateway API CRDs, agentgateway
CRD and controller charts, controller and data-plane images, Redis, the Envoy
ratelimit image, Python wheels, and required tooling.

![Air-gap dependency graph](docs/assets/diagrams/article/05-airgap-dependency-graph.png)

Everything allowed into the environment is pinned in
[airgap/sources.lock.yaml](airgap/sources.lock.yaml). Nothing is fetched,
transferred, or installed unless it appears there, so the lock is the single file
to review when asking what this platform will introduce into a cluster. Each
entry records the version, canonical source, destination name, checksum or OCI
digest, provenance note, license note, and compatibility set it belongs to.

The work runs on two machines, because no single machine has both the internet
access needed to fetch the dependencies and the cluster access needed to install
them. Steps 1 and 2 run on the connected side, steps 4 to 6 on the disconnected
side, and step 3 is the transfer between them.

**Step 1 — Build the bundle (connected side).** Resolve the lock, download each
artefact from its canonical source, check every payload against the lock, and
package the result. The bundle carries the payload, an inventory, checksums, and
the lock file itself.

```bash
airgap-ai-gateway bundle build \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --registry registry.example.internal:5000 \
  --dist-dir dist/airgap-demo \
  --payload-mode fetch
```

Use `--payload-mode fetch` when exporting real payloads for transfer. Use
`--payload-mode descriptor` for a fast local audit that writes the inventory and
checksums without downloading anything — this is what `make airgap-demo` runs.

**Step 2 — Confirm the bundle before it leaves (connected side).**

```bash
airgap-ai-gateway bundle verify \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --registry registry.example.internal:5000 \
  --bundle-dir dist/airgap-demo/baseline-v1.3.1
```

**Step 3 — Cross the air gap.** Transfer the bundle by whatever controlled route
the organisation uses: removable media, a transfer station, a scanning gateway.
Large bundles can be split into parts, each carrying its own checksum. The
crossing is one-way — once the bundle is inside, there is no network path back to
correct a mistake.

**Step 4 — Verify again, offline (disconnected side).** The same command runs
against the bundled lock and makes no network requests. A single changed byte
fails here. Running the check on both sides is deliberate: step 2 proves the
artefacts were fetched correctly, step 4 proves nothing changed in transit.

```bash
airgap-ai-gateway bundle verify \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --bundle-dir dist/airgap-demo/baseline-v1.3.1
```

**Step 5 — Promote images into the internal registry.** Promotion is planned and
applied as a pair, like every other state-changing operation.

```bash
airgap-ai-gateway --config examples/config registry promote plan \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --registry registry.example.internal:5000 \
  --output-file dist/airgap-demo/promotion-plan.json

airgap-ai-gateway --config examples/config registry promote apply \
  --plan-file dist/airgap-demo/promotion-plan.json \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY \
  --commands-log dist/airgap-demo/promotion-commands.log
```

Promote to a registry every cluster node can reach. Loading images onto
individual nodes is not a supported strategy, because the first reschedule onto
another node will fail to pull.

**Step 6 — Prove the manifests use only promoted images.**

```bash
airgap-ai-gateway --config examples/config verify \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --overlay production-reference \
  --registry registry.example.internal:5000
```

This rejects public registry references, mutable tags, unprotected routes, and
rendered Secret data, which closes the loop between what was imported and what
the cluster will actually run.

Reference: [docs/airgap-bundle.md](docs/airgap-bundle.md).

### 6.3 Render and validate the manifests

The Kubernetes source of truth is a Kustomize tree under
[manifests/baseline-v1.3.1](manifests/baseline-v1.3.1): bases describe the
platform objects, and overlays describe environment shape.

```bash
make render
make validate
python scripts/validate_manifests.py
```

| Overlay | Purpose |
| --- | --- |
| `kind-demo` | Small static demo render, single replica |
| `kind-e2e-lab` | Disposable behavioural proof with mock model Services |
| `retained-nginx-edge` | Brownfield migration keeping the existing edge |
| `production-reference` | Production-oriented shape with explicit HA and persistence decisions |

Validation rejects public registry references, mutable production tags,
unprotected routes, ambiguous Service ports, missing route policies,
cross-namespace references without explicit trust, and rendered Secret data. The
production reference does not represent a single Redis Pod as highly available;
it points to an external HA Redis contract instead.

Reference: [manifests/README.md](manifests/README.md).

### 6.4 Discover the target environment

Discovery is read-only. It reports the observed model services, routes, and edge
state, and identifies any ambiguity an operator must resolve explicitly rather
than letting automation choose.

```bash
airgap-ai-gateway --config examples/config discover
```

### 6.5 Plan the change

Planning is offline and produces two artefacts for review: `plan.json` as the
deterministic contract the executor follows, and `plan.md` as the human summary.

```bash
airgap-ai-gateway --config examples/config deploy plan \
  --overlay retained-nginx-edge \
  --apply-mode server-side-dry-run \
  --output-dir runs/plans/deploy
```

If the plan does not describe the intended change, the correction belongs in the
source, not in a manual adjustment after applying.

### 6.6 Apply the approved plan

Every state-changing operation requires the exact expected cluster context, an
apply mode matching the reviewed plan, a confirmation token, the approved plan
file, and a saved pre-change snapshot. A context mismatch fails closed.

```bash
airgap-ai-gateway snapshot create \
  --plan-file runs/plans/deploy/plan.json \
  --expected-context kind-airgap-ai-gateway \
  --output-file runs/snapshots/pre-change.json

airgap-ai-gateway --config examples/config deploy apply \
  --expected-context kind-airgap-ai-gateway \
  --apply-mode server-side-dry-run \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY \
  --plan-file runs/plans/deploy/plan.json \
  --snapshot-file runs/snapshots/pre-change.json \
  --commands-log runs/reports/deploy/commands.log
```

Reference: [docs/deployment.md](docs/deployment.md).

### 6.7 Verify the internal path

The gateway is tested through its internal Service before any production traffic
is involved, which removes DNS, TLS, external load balancers, and legacy ingress
behaviour from the diagnosis.

![Direct internal test path](docs/assets/diagrams/article/11-direct-internal-test-path.png)

```bash
airgap-ai-gateway --config examples/config verify runtime \
  --expected-context kind-airgap-ai-gateway \
  --gateway-url https://gateway.example.internal \
  --credential internal-chat=example-only-do-not-use \
  --credential rag-indexer=example-only-do-not-use \
  --credential testing-client=example-only-do-not-use \
  --credential unknown=example-only-do-not-use
```

### 6.8 Cut over the edge

Cutover is planned and applied as a separate operation. With a retained edge,
DNS and TLS do not move; only the edge backend changes.

![Cutover path through the retained edge](docs/assets/diagrams/article/13-cutover-path.png)

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

Reference: [ADR 0003](docs/adr/0003-staged-cutover-and-rollback.md).

---

## 7. GitOps with Argo CD

GitOps inverts the direction of deployment. Rather than an operator pushing a
change into a cluster, a controller inside the cluster continuously pulls the
declared state from Git and converges the cluster towards it. Argo CD is the
controller used here. The practical consequence is that drift is corrected
automatically: a resource edited by hand is reverted on the next reconciliation,
because Git — not the live cluster — is the authority.

This repository supports both paths. The direct CLI path suits a staged rollout
where an operator wants a gate at each step. The GitOps path suits steady-state
operation, where the platform should stay aligned with a reviewed branch without
anyone running a command.

![Argo CD GitOps reconciliation architecture](docs/assets/diagrams/article/21-argocd-gitops-reconciliation.png)

### 7.1 What Argo CD owns

Argo CD owns the same gateway source that the direct deployment path validates:
Gateway API resources, agentgateway backends and policies, rate-limit resources,
NetworkPolicies, ServiceAccounts, ConfigMaps, and environment overlays.

It does not own production model Pods, model weights, runtime API key values, or
the private registry. Those remain separate platform responsibilities.

Two exclusions are enforced rather than merely documented. The AppProject does
not allow the `Secret` kind at all, so an Application cannot reconcile credential
material even if a Secret were committed by mistake. And the model Services carry
`Prune=false`, so an Application lifecycle event cannot delete the model
workloads the gateway routes to — the same ownership boundary the rollback ledger
enforces on the direct path, expressed in Argo CD's own terms.

The Application points to a managed overlay wrapper, and that wrapper points back
to the tested Kustomize overlay.

```text
gitops/argocd/bootstrap/production-reference
  -> AppProject + Application in argocd
  -> gitops/argocd/managed-overlays/production-reference
  -> manifests/baseline-v1.3.1/overlays/production-reference
```

### 7.2 Validate the GitOps source

Run the GitOps validation before bootstrapping an Application:

```bash
make gitops-validate
```

Render the exact Argo CD bootstrap and managed overlay for review:

```bash
make gitops-render GITOPS_ENV=production-reference
```

The validator checks that the AppProject is least privilege, the Application
points to the expected managed overlay, automated sync is configured as declared,
no Secret is rendered, model Service contracts have prune protection, and the
managed overlay still passes the same route, policy, and private-registry checks
as the normal manifests.

### 7.3 Bootstrap Argo CD with the same safety gate

Plan the bootstrap:

```bash
airgap-ai-gateway --config examples/config gitops plan \
  --environment production-reference \
  --apply-mode server-side-dry-run \
  --output-dir runs/plans/gitops-production
```

Capture the existing Argo CD resource state:

```bash
airgap-ai-gateway snapshot create \
  --plan-file runs/plans/gitops-production/plan.json \
  --expected-context kind-airgap-ai-gateway \
  --output-file runs/snapshots/gitops-production-pre-change.json
```

Apply only the reviewed bootstrap plan:

```bash
airgap-ai-gateway --config examples/config gitops apply \
  --expected-context kind-airgap-ai-gateway \
  --apply-mode server-side-dry-run \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY \
  --plan-file runs/plans/gitops-production/plan.json \
  --snapshot-file runs/snapshots/gitops-production-pre-change.json \
  --commands-log runs/reports/gitops-production/commands.log
```

The bootstrap applies the AppProject and one Application. Argo CD then reconciles
the selected managed overlay from Git. The same runtime verification matrix
still decides whether the gateway is usable.

Reference: [docs/gitops-argocd.md](docs/gitops-argocd.md).

---

## 8. Verification

### 8.1 Why failures are part of the proof

A gateway is a policy boundary, so readiness probes and successful requests are
insufficient evidence. The platform must demonstrate that unauthorised requests
fail for the correct reason as reliably as it demonstrates that authorised
requests succeed.

![Policy test matrix](docs/assets/diagrams/article/12-policy-test-matrix.png)

| Request | Expected signal | What it proves |
| --- | --- | --- |
| No API key | 401 | Anonymous access is closed |
| Unknown API key | 401 | Only known runtime credentials are accepted |
| Qwen consumer with Qwen grant | 200 | Chat route, backend, and policy align |
| Valid consumer without Qwen grant | 403 | Entitlement is route-specific |
| Gemma consumer with Gemma grant | 200 | The second chat route is independent |
| Embedding consumer with embedding grant | 200 with vector length greater than zero | The embedding response shape is validated |
| Valid consumer without embedding grant | 403 | Embedding access is not granted broadly |
| Low-limit consumer under repeated traffic | 429 | Descriptor and counter path are active |
| Wrong Host header | 404 | Host and route matching bound the surface |
| Broken backend route | Diagnostic condition or expected upstream failure | Backend errors remain visible |
| Gateway cleanup | Model Services remain present | Gateway lifecycle is separate from model lifecycle |

The embedding assertion checks vector length rather than status code alone, so a
`200` proves an actual embedding response instead of any successful HTTP reply.

### 8.2 Resource conditions

Runtime verification also polls bounded Kubernetes conditions, because a resource
existing is not the same as a resource working:

- `Gateway` — `Programmed=True`
- `HTTPRoute` — `Accepted=True` and `ResolvedRefs=True`
- `AgentgatewayPolicy` — `Accepted=True` and `Attached=True`
- `Deployment` — observed generation and available replicas match the request

### 8.3 Running the proof

```bash
make lint
make test
make validate
make kind-test
```

The lab creates a uniquely named cluster and registry, tears down only what it
created, and writes JUnit, JSON, and Markdown evidence.

Verification has two modes. `verify static` is the default and runs entirely
offline: it checks configuration consistency, renders the overlays, and enforces
the image, tag, route, policy, and Secret rules against the rendered output. It
requires no cluster.

`verify runtime` exercises the same behavioural matrix against a gateway that is
already running. It requires the expected cluster context and a gateway URL, and
it refuses to run without both. Test credentials are supplied on the command
line and are expected to come from a temporary source rather than from anything
tracked in the repository:

```bash
airgap-ai-gateway --config examples/config verify runtime \
  --expected-context kind-airgap-ai-gateway \
  --gateway-url https://gateway.example.internal \
  --credential internal-chat=example-only-do-not-use \
  --credential rag-indexer=example-only-do-not-use \
  --credential testing-client=example-only-do-not-use \
  --credential unknown=example-only-do-not-use
```

This is what makes the request matrix reusable outside the disposable lab: the
same assertions can be pointed at a staging or production gateway after a change,
rather than existing only as a local test.

References: [docs/verification.md](docs/verification.md),
[docs/kind-e2e-lab.md](docs/kind-e2e-lab.md).

---

## 9. Platform operations

### 9.1 Onboarding a model

Adding a model is a routine platform operation rather than a bespoke migration.
Each model is identified by a stable model key, which propagates to the route
name, backend name, policy name, permission field, labels, tests, and report
fields.

![Model resource naming](docs/assets/diagrams/article/14-model-resource-naming.png)

Onboarding is default-deny. Publishing a model route grants no access to any
existing consumer; entitlement is added afterwards, to one named consumer at a
time. The procedure is therefore structured as six phases separated by two
verification gates, and a failed gate stops the onboarding rather than
downgrading it to a warning. The first gate proves that the route is genuinely
closed before any entitlement exists; the second proves that the entitlement
granted reaches exactly one consumer and no others.

![Six-phase default-deny model onboarding with verification gates](docs/assets/diagrams/rendered/model-onboarding-default-deny.svg)

| Phase | Action | Exit condition |
| --- | --- | --- |
| 1. Define the contract | Assign the model key; declare backend Service, port, and API shape; confirm the model Service responds directly | Model answers on its own endpoint |
| 2. Publish under policy | Create the `HTTPRoute` and attach its route policy with no grants | Route exists and the policy reports `Attached=True` |
| 3. Prove default deny | Issue requests as existing consumers | **Gate:** every existing consumer receives `403` |
| 4. Grant entitlement | Add the model to one selected consumer; add its rate-limit entries | Consumer record and quota descriptor updated |
| 5. Prove entitlement scope | Re-issue requests as the granted consumer and as unrelated consumers | **Gate:** granted consumer receives `200`, unrelated consumers still `403` |
| 6. Release | Commit the `401`, `403`, `200`, and `429` assertions; promote through the bundle and deployment workflow | Behaviour is enforced by the test suite |

A failure at either gate indicates that the policy is not attached to the
intended route, or that entitlement was applied more broadly than intended.
Neither condition should be resolved by granting access more widely.

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

airgap-ai-gateway --config examples/config model add apply \
  --plan-file runs/plans/model-falcon-chat/plan.json \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY
```

Reference: [docs/model-onboarding.md](docs/model-onboarding.md).

### 9.2 Consumer lifecycle

A consumer record represents one calling workload. Its credential value is never
repository content; it is held in the environment's runtime secret system, and
the repository defines only the Secret names, labels, and metadata schema.

![API key metadata becoming gateway consumer identity](docs/assets/diagrams/article/08-api-key-metadata.png)

Rotation is performed with overlap so no application outage occurs: add a second
credential for the same consumer identity, move the application to it, confirm
that traffic and metrics still report the same `consumer_id`, then remove the
old credential.

![Consumer disable versus revoke](docs/assets/diagrams/article/16-consumer-disable-vs-revoke.png)

Disable and revoke are separate operations with different purposes. Disabling
retains the identity record and removes model access, preserving audit
continuity. Revoking invalidates the credential material and is required when a
key is exposed or suspected of exposure.

Removing a route policy is never the correct way to remove one consumer's access,
because it would affect every consumer of that model and leave the route
unprotected in the interim. The consumer's entitlement is changed instead.

```bash
airgap-ai-gateway --config examples/config consumer add plan \
  --consumer-key search-app \
  --display-name "Search App" \
  --allowed-model qwen-chat \
  --allowed-model gemma-chat \
  --requests-per-minute 60 \
  --output-dir runs/plans/consumer-search-app

airgap-ai-gateway --config examples/config consumer add apply \
  --plan-file runs/plans/consumer-search-app/plan.json \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY

airgap-ai-gateway --config examples/config consumer rotate plan \
  --consumer-key search-app \
  --output-dir runs/plans/consumer-search-app-rotate

airgap-ai-gateway --config examples/config consumer rotate apply \
  --plan-file runs/plans/consumer-search-app-rotate/plan.json \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY

airgap-ai-gateway --config examples/config consumer revoke plan \
  --consumer-key search-app \
  --output-dir runs/plans/consumer-search-app-revoke

airgap-ai-gateway --config examples/config consumer revoke apply \
  --plan-file runs/plans/consumer-search-app-revoke/plan.json \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY
```

Reference: [docs/consumer-lifecycle.md](docs/consumer-lifecycle.md).

### 9.3 Troubleshooting

Because each control fails with a distinct signal, the response code identifies
the responsible layer before any manifest is opened.

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

A response code is only available once a request completes. When requests fail
before that point, or when the gateway never becomes ready at all, the Kubernetes
resource conditions carry the diagnosis instead. Three conditions account for
most cases, and they are not interchangeable — each names a different owner.

`Programmed=False` on the Gateway means the control plane never produced a
working data plane. Nothing downstream is worth examining yet, because there is
no proxy serving requests. Investigate the CRDs, the controller's readiness, the
`GatewayClass`, and the parameters the Gateway references.

`ResolvedRefs=False` on an HTTPRoute means the route cannot resolve its backend
reference. The route exists and is syntactically valid, but it points at
something the controller cannot find — usually a Service name, namespace, or port
that does not match, or a cross-namespace reference without a `ReferenceGrant`.
Credentials are downstream of this: rotating a key while a route cannot resolve
its backend changes nothing.

`Attached=False` on an AgentgatewayPolicy means the policy did not bind to its
target. This is the most consequential of the three, because the object is
otherwise healthy. A typo in `targetRef` produces a policy the API server accepts
without complaint and that protects nothing at all. The API server validates the
shape of the reference; only the controller status reports whether the
relationship actually exists. A route reporting `Accepted=True` with a policy
reporting `Attached=False` is an unprotected route.

The practical order is therefore to establish that the Gateway is programmed,
that each route is accepted and resolved, and that each policy is attached —
before drawing any conclusion from a response code. A `200` from a route whose
policy never attached is not a passing test.

Reference: [docs/troubleshooting.md](docs/troubleshooting.md).

### 9.4 Rollback and decommission

Rollback is driven by a state ledger that records, for every resource, whether it
was created by the run, updated by the run, or already present beforehand. That
record is what prevents recovery from deleting resources the gateway operation
never owned — most importantly the model workloads themselves.

![Decommission order](docs/assets/diagrams/article/19-decommission-order.png)

1. Restore the previous edge or exposure path.
2. Confirm client traffic reaches the previous backend.
3. Retain the gateway installation if it assists diagnosis.
4. Restore updated resources from the snapshot.
5. Delete only resources recorded as created by the selected run.
6. Remove gateway resources once traffic no longer depends on them.

The cleanup guard fails closed when edge or ingress state cannot be read: absent
proof that traffic no longer depends on the gateway, no gateway resources are
removed.

```bash
airgap-ai-gateway --config examples/config rollback plan \
  --ledger-file runs/reports/deploy/ledger.json \
  --run-id run-1 \
  --apply-mode server-side-dry-run \
  --output-dir runs/plans/rollback

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

---

## 10. Repository layout

```text
.
├── airgap/                         # Source lock and example bundle reports
├── docs/                           # Architecture, security, runbooks, ADRs, and assets
├── examples/config/                # Example platform, model, consumer, and registry config
├── gitops/argocd/                  # Argo CD AppProject, Applications, and managed overlays
├── lab/                            # Mock OpenAI-compatible services and E2E fixtures
├── manifests/baseline-v1.3.1/      # Kustomize source of truth for the tested baseline
├── scripts/                        # Validation, lab, asset, and link-check scripts
├── src/airgap_ai_gateway/          # Typed CLI, planner, executor, verifier, bundle logic
└── tests/                          # Unit, regression, manifest, and lab safety tests
```

Source, tests, documentation, schemas, and example reports are tracked. Runtime
credentials, kubeconfig material, generated run directories, OCI archives, chart
archives, wheelhouses, and offline bundle payloads are not.

---

## 11. Development workflow

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
make gitops-validate
```

Run documentation checks:

```bash
make diagrams
make docs-check
python scripts/check_links.py --check-external README.md CONTRIBUTING.md SECURITY.md CHANGELOG.md docs manifests lab
```

Run the full local lab when Kubernetes behaviour changes:

```bash
make kind-test
```

Contribution expectations: [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 12. Continuous integration

The same checks that run locally are enforced in CI, so the guarantees the
documentation claims are the guarantees the repository actually holds.

| Workflow | Enforces |
| --- | --- |
| `lint` | Formatting, linting, typing, YAML, Markdown, shell, and workflow policy |
| `unit` | Unit tests with a coverage floor and JUnit output |
| `manifests` | Kustomize rendering, Argo CD GitOps validation, schema validation, image policy, and route policy |
| `kind-e2e` | The disposable behavioural matrix on a real cluster |
| `security` | Full-history secret scanning, dependency audit, Trivy scans, and SBOM generation |

### 12.1 Workflow policy as code

CI configuration is itself validated, by `scripts/validate_workflows.py`. A
workflow that violates the policy fails the `lint` gate rather than being caught
in review. The policy requires that:

- top-level permissions are exactly `contents: read`;
- `pull_request_target` is never used;
- concurrency is declared and cancels superseded runs;
- every job sets `timeout-minutes` and cannot broaden permissions;
- every external action is pinned to a full commit SHA, not a tag;
- cache keys are exact and content-derived, with no `restore-keys` fallback;
- artefact uploads declare explicit inputs, retention, and failure on missing
  files.

Pinning actions by SHA and refusing `pull_request_target` are the two rules that
matter most for a repository handling an offline supply chain: both close paths
by which an upstream change could alter what CI executes.

---

## 13. Security

Security-sensitive areas include authentication, authorization, Secret handling,
redaction, image provenance, registry promotion, context verification, apply
gates, rollback behaviour, and route exposure.

One baseline limitation is stated explicitly rather than omitted: agentgateway
v1.3.1 stores API keys as raw runtime credentials in Kubernetes Secret objects,
which makes Secret protection part of the security boundary. Production use of
this baseline requires Secret encryption at rest, least-privilege RBAC, external
secret integration where available, rotation with overlap, and redaction in logs
and reports. The v1.4.0 hashed-key path is the intended improvement and is
tracked as a validation target.

Reference: [SECURITY.md](SECURITY.md).

---

## 14. Architecture decisions

Architecture Decision Records capture the reasoning behind platform boundaries,
including the alternatives considered and rejected. A future implementation that
needs a different route shape, backend pattern, cutover sequence, artefact
boundary, or secret model should update the relevant record rather than only
changing manifests.

- [ADR 0001: One Gateway, separate model routes](docs/adr/0001-one-gateway-separate-model-routes.md)
- [ADR 0002: Chat versus embedding backends](docs/adr/0002-chat-vs-embedding-backends.md)
- [ADR 0003: Staged cutover and rollback](docs/adr/0003-staged-cutover-and-rollback.md)
- [ADR 0004: Air-gap artifacts outside Git](docs/adr/0004-airgap-artifacts-outside-git.md)
- [ADR 0005: Secret management boundary](docs/adr/0005-secret-management-boundary.md)
- [ADR 0006: Argo CD GitOps reconciliation](docs/adr/0006-argocd-gitops-reconciliation.md)

---

## 15. References

Full implementation write-up:

- [Building an Air-Gapped AI Gateway on Kubernetes with AgentGateway, Envoy, and NVIDIA NIM](https://medium.com/@ahmedmaherbf/building-an-air-gapped-ai-gateway-on-kubernetes-with-agentgateway-envoy-and-nvidia-nim-880141f333d5)

Project documentation:

| Document | Contents |
| --- | --- |
| [Architecture contract](docs/architecture.md) | Full architecture and ownership boundaries |
| [Security model](docs/security-model.md) | Assets, trust boundaries, threats, and controls |
| [Compatibility](docs/compatibility.md) | Version matrix, evidence, and upgrade risk |
| [Deployment](docs/deployment.md) | Staged deployment and cutover runbook |
| [Argo CD GitOps](docs/gitops-argocd.md) | GitOps reconciliation and bootstrap workflow |
| [Verification](docs/verification.md) | Static and runtime proof procedure |
| [Rollback](docs/rollback.md) | Recovery and decommission order |
| [Model onboarding](docs/model-onboarding.md) | Adding a model under default deny |
| [Consumer lifecycle](docs/consumer-lifecycle.md) | Identity, rotation, disable, and revoke |
| [Troubleshooting](docs/troubleshooting.md) | Signal-driven diagnosis |
| [Air-gap bundle](docs/airgap-bundle.md) | Offline supply chain workflow |
| [Disposable lab](docs/kind-e2e-lab.md) | Local behavioural proof environment |
| [Context log](docs/context-log.md) | Delivered baseline, current status, and open design decisions |

Upstream projects:

| Component | Reference |
| --- | --- |
| <img src="docs/assets/logos/agentgateway.svg" alt="agentgateway logo" width="20"> agentgateway | [agentgateway documentation](https://agentgateway.dev/) |
| <img src="docs/assets/logos/kubernetes.svg" alt="Kubernetes logo" width="20"> Kubernetes Gateway API | [Gateway API project](https://gateway-api.sigs.k8s.io/) |
| <img src="docs/assets/logos/argocd.svg" alt="Argo CD logo" width="20"> Argo CD | [Argo CD documentation](https://argo-cd.readthedocs.io/) |
| <img src="docs/assets/logos/envoy.svg" alt="Envoy logo" width="20"> Envoy | [Envoy Proxy](https://www.envoyproxy.io/) |
| <img src="docs/assets/logos/nvidia.svg" alt="NVIDIA logo" width="20"> NVIDIA NIM | [NVIDIA NIM](https://www.nvidia.com/en-us/ai/) |
| <img src="docs/assets/logos/redis.svg" alt="Redis logo" width="20"> Redis | [Redis](https://redis.io/) |
| <img src="docs/assets/logos/nginx.svg" alt="NGINX logo" width="20"> NGINX | [NGINX](https://nginx.org/) |
| <img src="docs/assets/logos/python.svg" alt="Python logo" width="20"> Python | [Python](https://www.python.org/) |
| <img src="docs/assets/logos/github-actions.svg" alt="GitHub Actions logo" width="20"> GitHub Actions | [GitHub Actions](https://docs.github.com/actions) |
