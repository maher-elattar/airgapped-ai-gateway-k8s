# Air-Gap Bundle Workflow

![Air-gap dependency graph](assets/diagrams/article/05-airgap-dependency-graph.png)

The platform uses a two-sided delivery model. The connected side resolves and
packages the exact dependency set. The disconnected side verifies that set,
promotes images into the internal registry, and proves that the rendered
deployment points only at promoted internal names.

This keeps the installation path predictable. The cluster does not become the
place where dependencies are discovered, patched, or improvised. The cluster
receives a declared version set, a declared image map, and manifests rendered
from the repository source of truth.

## Compatibility set

The delivered compatibility set is `baseline-v1.3.1`.

It includes:

- Gateway API v1.5.0 experimental CRDs.
- agentgateway v1.3.1 CRD and controller charts.
- agentgateway controller and generated data-plane images.
- Redis and Envoy ratelimit images used by the demo rate-limit path.
- Runtime Python wheel dependencies for the CLI.
- Required validation and packaging tools.
- Small test fixture images for disposable validation workflows.

Each entry in `airgap/sources.lock.yaml` has a version, canonical source,
destination name, checksum or OCI digest, provenance note, license note, and
compatibility-set membership.

## Connected build side

The connected side performs dependency acquisition and bundle assembly.

The normal flow is:

1. Validate `airgap/sources.lock.yaml`.
2. Fetch every source artifact from its canonical source.
3. Verify every fetched payload against the lock.
4. Export OCI images as OCI-native archives where practical.
5. Include Helm charts, CRD manifests, Python wheels, and tool archives.
6. Write a deterministic bundle inventory and checksum manifest.
7. Attach optional metadata such as SBOM, malware scan, and signature results.
8. Split the bundle into transfer-media parts when required.

The repository implementation keeps the deterministic inventory, checksum, and
promotion planning logic in the CLI. Large payloads are produced under `dist/`,
which is intentionally outside version control.

Example:

```bash
airgap-ai-gateway bundle build \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --dist-dir dist/airgap-bundles \
  --metadata-hook sbom \
  --metadata-hook signature
```

The build output is an audit package. It records the logical inventory, payload
checksums, optional metadata hook declarations, and the internal image names that
the disconnected side must promote.

## Disconnected verification side

The disconnected side must be able to verify the bundle with no network path.

The normal flow is:

1. Read the source lock and bundle inventory.
2. Recompute every payload checksum.
3. Compare the inventory against the lock.
4. Verify split parts before reassembly.
5. Produce a JSON verification report.
6. Refuse promotion or install when any byte has changed.

Example:

```bash
airgap-ai-gateway bundle verify \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --bundle-dir dist/airgap-bundles/baseline-v1.3.1
```

The verifier is intentionally offline. It reads local files only. If the
inventory, checksum file, payload descriptor, complete bundle, or transfer part
does not match the lock, verification fails.

## Registry promotion

Image promotion is planned as an exact source-to-destination mapping.

The preferred copy strategy is OCI-native:

```bash
skopeo copy docker://SOURCE_DIGEST docker://INTERNAL_DESTINATION_DIGEST
```

The Docker fallback remains documented for environments where `skopeo` is not
available:

```bash
docker pull SOURCE_DIGEST
docker tag SOURCE_DIGEST INTERNAL_DESTINATION_DIGEST
docker push INTERNAL_DESTINATION_DIGEST
```

For multi-node clusters, the supported strategy is to promote images into an
internal registry reachable by every node. Loading images into only one node is
not a reliable installation strategy for this platform.

Generate the promotion plan:

```bash
airgap-ai-gateway registry promote \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --registry registry.example.internal:5000 \
  --output-file dist/airgap-bundles/promotion-plan.json
```

The plan includes destination existence checks. Operators should run those
checks before pushing, then promote the exact source digest to the exact internal
destination name.

## Rendered manifest proof

The final disconnected-side proof compares rendered manifests with the promoted
image map.

```bash
airgap-ai-gateway verify \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --overlay production-reference \
  --registry registry.example.internal:5000
```

The check rejects public registries, mutable image tags, missing policies,
unprotected routes, and rendered Secret data. The output must show that every
image in the rendered deployment is one of the promoted internal references.

## Transfer media

Large bundles can be split into fixed-size parts. Each part gets an individual
SHA-256 checksum, and the complete file keeps its own checksum.

The disconnected side verifies parts before reassembly and verifies the complete
file after reassembly. A single modified byte is enough to fail verification.

## Repository boundary

The repository stores the lock, validation logic, source manifests, schemas,
tests, and sample reports. It does not store bundle payloads, OCI
archives, chart archives, wheelhouses, or generated install media.
