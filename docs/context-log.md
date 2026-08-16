# Context Log

This log records repository milestones and open engineering decisions for the
public reference implementation.

## Objective

Build a reproducible air-gapped Kubernetes AI gateway platform with a typed CLI,
declarative manifests, a two-sided bundle workflow, disposable verification, and
public operator documentation.

## Baseline

- agentgateway v1.3.1 is the supported implementation baseline.
- Gateway API v1.5.0 experimental is the supported Gateway API baseline.
- agentgateway v1.4.0 remains a validation target until the compatibility track
  proves hashed-key authentication, policy metadata propagation, Gateway API
  changes, rollback behavior, and updated documentation examples.

## Current status

- Architecture, security, compatibility, ADRs, operator docs, and diagrams are
  present.
- The Python CLI includes deterministic planning, gated execution, pre-change
  snapshot capture, redacted reporting, runtime verification, model and consumer
  source lifecycle automation, registry promotion apply, rollback state tracking,
  bundle handling, and disposable lab orchestration.
- Kustomize bases and overlays render the baseline gateway model.
- The disposable kind lab owns mock model services and the behavioral proof
  matrix.
- The Argo CD GitOps layer now provides AppProject/Application bootstrap,
  managed overlay wrappers, automated reconciliation policy, and static
  validation while keeping Argo CD itself as a platform prerequisite.
- CI workflows now cover linting, unit tests, manifest validation, disposable
  e2e, security scans, workflow policy validation, and SBOM generation.
- Unit coverage is above the 85 percent safety gate, with new regression tests
  for lifecycle source plans, snapshot capture, runtime verification, registry
  promotion, and descriptor/fetch bundle modes.

## Open decisions

- Prove the v1.4.0 compatibility track before changing authentication storage
  assumptions in examples or docs.
- Decide whether production examples should use a specific external secret
  operator or keep the current integration contract.
- Decide whether to require signed commits, verified builder provenance, or both
  when the repository is published under an organization.
- Decide which internal Git mirror URL each disconnected environment should use
  for Argo CD reconciliation.
