# Air-Gapped AI Gateway Platform

This repository is a reference implementation for an air-gapped Kubernetes AI gateway based on a real delivery pattern.

It intentionally contains no private environment domains, private registry names, kubeconfig material, API keys, image archives, chart archives, rendered third-party CRDs, binary handover files, or generated handover artifacts.

Delivered baseline to preserve:

- agentgateway v1.3.1
- Gateway API v1.5.0 experimental
- Kubernetes-native, declarative source of truth
- Runtime secrets supplied outside Git and outside normal command logs

Newer component versions, if added, must live in a separate compatibility track and are not supported until tests pass.

## Repository status

Phase 0 initializes the local-only clean repository, source-review documentation, context log, Git hygiene, and secret boundary.

No remote is configured. Do not push or publish until a later phase explicitly approves it.

## Architecture contract

The repository-level architecture contract is defined in [docs/architecture.md](docs/architecture.md). Generated runtime resources are inspected for verification and troubleshooting, but they are never manually maintained as the source of truth.

## CLI scaffold

The first implementation surface is a typed Python CLI. It is intentionally offline-first in this phase.

Install the project in a clean environment:

```bash
python -m pip install -c constraints.txt -r requirements-dev.txt -e .
```

Inspect the command tree:

```bash
airgap-ai-gateway --help
airgap-ai-gateway deploy apply --help
```

Render fake-only scaffold manifests:

```bash
make render
```

Apply-style commands are safety-gated. They refuse to proceed unless the operator supplies the exact configured disposable context and confirmation token. The scaffold does not run `kubectl`.

## Kubernetes manifests

The authored Kubernetes source of truth is under [manifests/baseline-v1.3.1](manifests/baseline-v1.3.1).

The baseline provides Kustomize bases and three overlays:

- `kind-demo`: disposable, demo-only, single-replica profile.
- `retained-nginx-edge`: brownfield profile where the existing edge remains the public entry point.
- `production-reference`: HA-oriented reference profile that requires an external HA Redis contract instead of rendering the demo Redis workload.

Validate the manifests without a cluster:

```bash
python scripts/validate_manifests.py
```

If standalone `kustomize` is installed, the validator also runs `kustomize build` for every overlay.
