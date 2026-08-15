# Air-Gapped AI Gateway Platform

This repository is a reference implementation for an air-gapped Kubernetes AI gateway.

It contains fake examples, authored manifests, validation scripts, and documentation. Runtime secrets, kubeconfig material, binary image bundles, chart archives, and generated operational outputs stay outside Git.

Delivered baseline to preserve:

- agentgateway v1.3.1
- Gateway API v1.5.0 experimental
- Kubernetes-native, declarative source of truth
- Runtime secrets supplied outside Git and outside normal command logs

Newer component versions, if added, must live in a separate compatibility track and are not supported until tests pass.

## Repository status

The repository currently includes the architecture contract, typed Python CLI scaffold, Kustomize manifests, offline validation, and local diagram assets.

## Architecture contract

The repository-level architecture contract is defined in [docs/architecture.md](docs/architecture.md). Durable platform changes are made through authored source: configuration, manifests, schemas, scripts, and tests.

## CLI

The first implementation surface is a typed Python CLI. Plan and render workflows
are offline-capable. State-changing workflows are gated by a saved plan, an exact
expected context, an apply mode, a confirmation token, and a pre-change snapshot.

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

Apply-style commands execute only actions listed in an approved plan and write
redacted reports and command logs.

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

## Air-gap bundle workflow

The connected build side and disconnected verification/install side are
documented in [docs/airgap-bundle.md](docs/airgap-bundle.md).

Build and verify the deterministic bundle audit artifacts without touching a
cluster:

```bash
make airgap-demo
```

## Disposable end-to-end lab

The kind-based lab is documented in [docs/kind-e2e-lab.md](docs/kind-e2e-lab.md).
It uses repository-owned OpenAI-compatible mock model services and writes
evidence under ignored run directories.

```bash
make kind-test
```
