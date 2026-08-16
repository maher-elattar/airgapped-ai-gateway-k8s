# Security Policy

## Supported baseline

- agentgateway v1.3.1.
- Gateway API v1.5.0 experimental.
- The manifests, CLI behavior, and tests in this repository.

Newer versions are validation targets until tests prove compatibility.

## Reporting security issues

Please do not open a public issue containing secret material, exploit details,
kubeconfig content, or private environment identifiers.

Use a private reporting path to the repository owner and include:

- A short description.
- Affected files or commands.
- Reproduction steps using fake values.
- Expected and actual behavior.
- Whether any credential material may have been exposed.

## Secret handling

This repository must not contain:

- Runtime API keys.
- Kubernetes Secret values.
- kubeconfig material.
- Private keys.
- Production registry names.
- Generated run directories.
- Binary transfer bundles.

Examples use values that could not be mistaken for real ones, such as
`REPLACE_AT_RUNTIME` or `example-only-do-not-use`.

## Baseline limitation

The v1.3.1 path stores raw runtime keys in Kubernetes Secret objects. Using it in
production requires:

- Kubernetes Secret encryption at rest.
- Least-privilege RBAC.
- External secret integration where available.
- Rotation with overlap.
- Log and report redaction.

agentgateway v1.4.0 advertises hashed-key support. This repository does not claim
that path until a separate compatibility track passes its tests.

## Security checks

```bash
make security-scan
make test
make validate
```

`make security-scan` blocks key-like tokens, kubeconfig markers, private key
blocks, Secret manifest content, and secret-like file paths. Environment-specific
identifiers such as internal domains and private registry hosts are deliberately
not listed in the repository. Keep those in an untracked
`.secret-scan-denylist` file (one extended-regex pattern per line) or point
`SECRET_SCAN_DENYLIST` at your own file.

For gateway behavior:

```bash
make kind-test
```

## CI security posture

GitHub Actions workflows run with `contents: read` by default and do not use
`pull_request_target` for untrusted code. Every external action is pinned by a
full commit SHA. Network-heavy jobs set explicit timeouts, and superseded runs
are cancelled by workflow concurrency.

Generated test credentials are short lived. The disposable lab writes them to the
job temporary directory and uploads only evidence: JSON summaries,
JUnit output, Markdown reports, manifest summaries, security reports, and SBOM
documents.

The security workflow runs:

- Gitleaks against the full reachable Git history.
- The repository secret scanner against the working tree and history.
- `pip-audit` against the pinned Python requirement files.
- Trivy filesystem and Kubernetes configuration scans.
- `actionlint` plus the repository workflow policy validator.
- SPDX JSON SBOM generation.

No CI cache is used for secrets, bundles, kubeconfig material, registry
credentials, generated test keys, or runtime artifacts.

## Dependency and SBOM policy

Runtime dependencies stay small and pinned. Development dependencies are pinned
through the checked-in requirement files so an offline bundle can be assembled
from an explicit dependency set.

Dependency updates should be proposed by Dependabot or Renovate and merged only
after the compatibility and behavioral tests pass. A new upstream component
version should remain in a validation track until the repository proves the
routes, policy metadata, rollback behavior, and documentation examples still
match the intended baseline.

The normal SBOM path is the `security / security-gates` workflow. Locally, use:

```bash
make security-scan
```

The local target validates repository policy and performs the pre-publication
scan. CI adds dependency, Trivy, workflow, and SBOM evidence.
