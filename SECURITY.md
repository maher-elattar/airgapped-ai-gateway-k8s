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
