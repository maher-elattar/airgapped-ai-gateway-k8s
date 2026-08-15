# Declarative Kubernetes Source of Truth

This directory contains the authored Kubernetes source for the
air-gapped AI gateway platform.

The manifests define controller inputs and owned supporting resources. Runtime
objects produced by Kubernetes controllers are operational output; durable
behavior changes are made by updating the authored input manifests.

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

Normal renders do not create `Secret` objects. Runtime key material is provided
by an external secret integration that writes the documented Secret name and
labels for the target environment.
