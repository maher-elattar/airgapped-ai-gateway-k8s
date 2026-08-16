# Changelog

Notable changes to this project.

## 0.1.0

- Architecture, security model, compatibility matrix, and ADRs 0001 through 0005.
- Typed Python CLI for discovery, rendering, planning, apply, verification,
  rollback, and air-gap bundle handling.
- Kustomize bases and overlays for the agentgateway v1.3.1 baseline, covering the
  demo, retained NGINX edge, and production-reference shapes.
- Deterministic plans, pre-change snapshots, state ledger, redacted reports, and
  gated apply for every state-changing workflow.
- Reproducible air-gap bundle workflow: immutable source lock, connected-side
  build, offline verification, registry promotion, and rendered-manifest proof.
- Disposable kind end-to-end lab with mock OpenAI-compatible model Services and
  the full request matrix.
- Operator guides for deployment, verification, rollback, model onboarding,
  consumer lifecycle, and troubleshooting.
