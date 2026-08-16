# Argo CD GitOps Source

This directory contains the Argo CD delivery layer for the air-gapped AI gateway
platform.

Use `bootstrap/<environment>` to create the Argo CD `AppProject` and
`Application`. The Application then reconciles the matching
`managed-overlays/<environment>` path.

```bash
airgap-ai-gateway --config examples/config gitops validate
airgap-ai-gateway --config examples/config gitops plan \
  --environment production-reference \
  --apply-mode server-side-dry-run \
  --output-dir runs/plans/gitops-production
```

Argo CD itself is expected to exist before this bootstrap is applied. Runtime
credentials are supplied by the environment's secret workflow, not by these
manifests.
