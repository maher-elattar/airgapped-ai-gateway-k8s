# Declarative Kubernetes Source

This is the authored Kubernetes source for the air-gapped AI gateway platform.

The manifests define the controller inputs and the supporting resources this
repository owns. Kubernetes controllers generate runtime objects from those
inputs, and those objects are worth inspecting during verification, but durable
behavior changes start here.

The delivered baseline lives under `baseline-v1.3.1`:

- `bases/` holds the namespace, Gateway API resources, agentgateway policy, model
  routes, backend Service contracts, and demo rate-limit resources.
- `overlays/kind-demo/` is a single-node disposable profile, labeled demo-only.
- `overlays/retained-nginx-edge/` keeps the existing edge as the public entry
  point and forwards only inference paths to the internal gateway Service.
- `overlays/production-reference/` makes the availability choices explicit and
  uses an external HA Redis contract rather than pretending one in-cluster Redis
  Pod is production-ready.

A normal render never creates `Secret` objects. Runtime key material comes from
the environment's secret workflow, using the documented Secret names and labels.
