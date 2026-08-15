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
