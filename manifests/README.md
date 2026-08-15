# Declarative Kubernetes Source of Truth

This directory contains the authored Kubernetes source of truth for the
air-gapped AI gateway platform.

The rule is intentionally strict: generated data-plane resources are inspected
after reconciliation, but they are not maintained here as authored manifests.
If runtime behavior must change, the declarative input changes first.

The delivered baseline is kept under `baseline-v1.3.1`:

- `bases/` contains namespace, Gateway API, agentgateway policy, model route,
  backend Service contracts, and demo rate-limit resources.
- `overlays/kind-demo/` is a single-node disposable profile and is labeled
  demo-only.
- `overlays/retained-nginx-edge/` keeps the existing edge as the public entry
  point and forwards only inference paths to the internal gateway Service.
- `overlays/production-reference/` makes availability choices explicit and uses
  an external HA Redis contract instead of pretending one in-cluster Redis pod is
  production-ready.

Normal renders must not create `Secret` objects. Runtime key material is provided
by an external secret integration that writes the documented Secret name and
labels at deployment time.
