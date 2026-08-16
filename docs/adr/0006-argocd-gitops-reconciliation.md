# ADR 0006: Argo CD GitOps Reconciliation

## Status

Accepted.

## Context

The platform already has a safe direct deployment path: render, validate, plan,
snapshot, apply, verify, cut over, and roll back. That path is useful for a
controlled rollout, but many Kubernetes platforms also expect steady-state
reconciliation from Git.

Argo CD fits that model, provided it does not blur ownership. The gateway
repository owns Gateway API resources, agentgateway policy, route definitions,
rate-limit demo resources, NetworkPolicies, and the supporting source needed to
validate them. It does not own runtime key values, the production Argo CD
installation, or the model runtime lifecycle.

## Decision

Add Argo CD as an additional delivery path:

- One AppProject defines the repository, destination namespace, and allowed
  resource kinds.
- One Application exists per supported profile.
- Applications reconcile managed Kustomize overlays under `gitops/argocd`.
- Automated sync, prune, and self-heal are enabled.
- Model Service contracts carry Argo CD prune protection.
- The direct CLI deployment path remains available and keeps the same safety
  checks.
- Argo CD itself is an existing platform prerequisite, not installed by this
  repository.

## Alternatives

### Keep only the direct CLI deployment path

This is simpler and already safe, but it leaves steady-state drift detection and
reconciliation outside the repository.

### Install Argo CD from this repository

This would make the demo more complete, but it would pull the Argo CD platform
lifecycle into a gateway repository. In an air-gapped environment, Argo CD
usually belongs to the cluster baseline and has its own upgrade, RBAC, SSO, and
secret-management requirements.

### Use one app-of-apps root for all profiles

This creates a convenient bootstrap, but it also makes it easier to deploy
multiple profiles accidentally. Separate Applications keep the selected
environment explicit.

## Consequences

- Git becomes the steady-state source Argo CD reconciles.
- CI and branch protection become part of the production approval boundary.
- Automated sync is acceptable because the AppProject and validation checks
  restrict what the Application can touch.
- Runtime credentials stay outside Git and outside Argo CD Application manifests.
- Operators still need separate runbooks for installing and hardening Argo CD
  itself.

## Validation

- `make gitops-validate` validates the AppProject, Application, managed overlays,
  image policy, route policy, and Secret boundary.
- `airgap-ai-gateway gitops plan` produces deterministic JSON and Markdown
  before bootstrap.
- `airgap-ai-gateway gitops apply` requires exact context, apply mode,
  confirmation token, and a pre-change snapshot.
- Runtime behavior is still proven through the gateway verification matrix.
