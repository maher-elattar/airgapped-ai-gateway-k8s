# Verification Guide

Verification is not the smoke test you run at the end. It is how the platform
shows that identity, authorization, routing, rate limits, and rollback boundaries
actually behave the way the documentation claims.

![Authentication, authorization, rate-limit, and backend decision flow](assets/diagrams/rendered/policy-decision-flow.svg)

## Static proof

Run the static suite first:

```bash
python -m pip install -c constraints.txt -r requirements-dev.txt -e .
make lint
make test
make validate
make render
make diagrams
make security-scan
```

That proves the source is internally consistent:

- Configuration rejects duplicate hosts, routes, model keys, and permission
  fields.
- Kustomize overlays render deterministically.
- Manifest validation rejects public images, mutable tags, unprotected routes,
  and rendered Secret data.
- Plans are deterministic and redacted.
- Rollback respects the state ledger.

## Runtime proof in kind

Use the local lab whenever gateway behavior changes:

```bash
make kind-test
```

Optional retained-edge pass:

```bash
python scripts/kind_e2e_lab.py run --with-nginx
```

The lab writes JUnit, JSON, and Markdown evidence into ignored run directories.
Example outputs are in [lab/samples](../lab/samples).

## Behavioral matrix

| Signal | Expected result | What it proves |
| --- | --- | --- |
| Missing key | 401 | Anonymous model access is closed |
| Unknown key | 401 | Only known runtime credentials work |
| Allowed Qwen consumer | 200 | Chat route, backend, and policy are aligned |
| Denied Qwen consumer | 403 | Entitlement is model-specific |
| Allowed Gemma consumer | 200 | The second chat route is independent |
| Allowed embedding consumer | 200 with vector length greater than zero | Embedding path is not treated like a chat request |
| Denied embedding consumer | 403 | Embedding entitlement is enforced |
| Repeated low-limit traffic | 429 | Descriptor and counter path are active |
| Wrong Host | 404 | Host matching protects the route boundary |
| Broken backend | Route diagnostic or expected upstream failure | Backend failures stay visible |
| Gateway cleanup | Model Services remain present | Gateway lifecycle is separate from model lifecycle |

The failures in that table carry as much weight as the successes. A gateway that
only proves its 200s has not proven it is a policy boundary.

## Kubernetes conditions

Runtime verification polls bounded conditions:

- Gateway: `Programmed=True`.
- HTTPRoute: `Accepted=True` and `ResolvedRefs=True`.
- AgentgatewayPolicy: `Accepted=True` and `Attached=True`.
- Deployment: observed generation and available replicas match the requested
  rollout.

When a condition is false, do not start guessing. Go to
[troubleshooting.md](troubleshooting.md).

## Air-gap proof

After connected preparation, the lab checks that runtime workload images use the
local registry, and reads namespace events for unexpected public pulls.

For multi-node clusters the supported proof is an internal registry reachable by
every node. Loading images onto one node does not satisfy this platform model.

## Evidence quality

Good evidence keeps:

- Test name.
- Target host and API path.
- HTTP status.
- Route and condition diagnostics.
- Command metadata.
- Redacted output.

Good evidence never keeps:

- API keys.
- Authorization headers.
- Secret values.
- kubeconfig data.
- Sensitive request or response payloads.
