# Contributing

The main thing to preserve here is that a clean checkout stays reproducible and
safe to run. Everything below follows from that.

## Development setup

```bash
python -m pip install -c constraints.txt -r requirements-dev.txt -e .
```

Run the normal checks:

```bash
make lint
make test
make validate
make gitops-validate
make security-scan
```

Run the disposable lab when you change gateway behavior:

```bash
make kind-test
```

## Contribution rules

- Keep runtime secrets out of Git.
- Use fake values in examples.
- Do not commit bundle payloads, image archives, chart archives, generated run
  outputs, kubeconfig files, or private environment identifiers.
- Keep durable Kubernetes behavior in the authored manifests, Kustomize overlays,
  configuration, scripts, and tests.
- Keep Argo CD Applications pointed at managed overlays that pass the same
  manifest and image policy checks as the direct deployment path.
- Do not add a support claim for a new upstream version until the tests for it
  pass.
- Update the docs alongside any operator-facing behavior change.

## CI quality gates

Every change is expected to pass the same gates that protect the published
branch:

- `lint / static-analysis` for Ruff, format checks, typing, YAML, Markdown,
  shell, and workflow policy validation.
- `unit / pytest` for unit tests, coverage, and JUnit output.
- `manifests / manifest-validation` for Kustomize rendering, schema validation,
  image-policy checks, and route-policy checks.
- `kind-e2e / disposable-gateway-lab` for the disposable behavioral matrix when
  gateway, manifest, bundle, or lab code changes.
- `security / security-gates` for full-history secret scanning, dependency
  audit, Trivy scans, workflow scanning, and SBOM generation.

The e2e job is intentionally heavier than the static gates. It should run on
main and on pull requests that change gateway behavior, manifests, tests, or lab
fixtures.

## Branch protection recommendation

Protect `main` before using this repository as a published reference:

- Require at least one approving review before merge.
- Require the CI status checks listed above.
- Block force pushes and branch deletion.
- Require branches to be up to date before merge where practical.
- Require signed commits, verified provenance, or an equivalent organization
  policy when available.
- Use Dependabot or Renovate for dependency and GitHub Actions updates, and let
  compatibility tests prove that an update is safe before merging it.

## Documentation boundary

The docs should read as standalone documentation for the project. Leave out local
machine paths, working notes, and process instructions that are not part of the
operating model.

## Diagrams and assets

Editable diagram sources live under
[docs/assets/diagrams/mermaid](docs/assets/diagrams/mermaid). Rendered SVG and
PNG outputs live under [docs/assets/diagrams/rendered](docs/assets/diagrams/rendered).

Logo and image provenance is recorded in
[docs/assets/sources.yaml](docs/assets/sources.yaml). Do not hotlink remote
images from Markdown.

To re-render one diagram:

```bash
npx -y @mermaid-js/mermaid-cli@11.12.0 \
  -p scripts/mermaid-puppeteer-config.json \
  -i docs/assets/diagrams/mermaid/before-after-traffic-architecture.mmd \
  -o docs/assets/diagrams/rendered/before-after-traffic-architecture.svg
```

Render both SVG and PNG when the source changes, then update the SHA-256 entries
in [docs/assets/sources.yaml](docs/assets/sources.yaml).

## Pull request checklist

- [ ] Tests pass.
- [ ] Documentation is updated.
- [ ] New diagrams have editable source and local rendered output.
- [ ] New assets have provenance and SHA-256 entries.
- [ ] No runtime secret, kubeconfig, archive, or generated run output is tracked.
- [ ] Compatibility claims match tested behavior.
