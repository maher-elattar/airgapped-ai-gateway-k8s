# Air-Gap Bundle Workflow

In an air-gapped cluster, dependency management is part of the architecture.
There is no safe version of "install it now and let the cluster pull whatever it
needs later." The CRDs, charts, images, Python wheels, tooling, checksums, and
registry names all have to be known before the install starts.

![Air-gap dependency graph](assets/diagrams/article/05-airgap-dependency-graph.png)

## The operating model

The work is split across two machines because no single machine has both the
internet access needed to fetch the dependencies and the cluster access needed to
install them.

- Connected side: resolve the lock, fetch every artefact from its canonical
  source, verify each payload against the lock, and package the bundle.
- Disconnected side: verify the bundle again with no network access, promote
  images to the internal registry, and prove the rendered manifests reference
  only promoted names.

The bundle carries the lock file along with the payload, inventory, and
checksums, which is what makes the second verification possible without a network
path. Running the check on both sides is deliberate: the first confirms the
artefacts were fetched correctly, the second confirms nothing changed while
crossing the boundary.

![Connected side, air-gap boundary, and disconnected side with a verification step on each side](assets/diagrams/rendered/airgap-supply-chain.svg)

## Compatibility set

The delivered set is `baseline-v1.3.1`. It covers:

- Gateway API v1.5.0 experimental CRDs.
- agentgateway v1.3.1 CRD and controller charts.
- agentgateway controller and generated data-plane images.
- Redis and Envoy ratelimit images for the demo rate-limit path.
- Python wheel dependencies for the CLI.
- Required validation and packaging tools.
- Small test fixture images for the disposable lab.

Each entry in [airgap/sources.lock.yaml](../airgap/sources.lock.yaml) records the
version, canonical source, destination name, checksum or OCI digest, provenance
note, license note, and compatibility-set membership.

Prepare and test all of it as one version set. Transferring agentgateway v1.3.1
and then picking up a different CRD revision later is how you find an API
incompatibility during the maintenance window instead of before it.

## Connected-side build

Dependency resolution happens on the connected side. The CLI supports two build
modes:

- `descriptor`: fast audit mode for CI and local demonstrations. It writes the
  locked inventory, checksums, metadata hooks, and transfer structure without
  pulling public artifacts.
- `fetch`: connected-side export mode. It downloads file artifacts, copies OCI
  images with `skopeo` when available, and falls back to Docker image save where
  necessary.

The local demonstration intentionally uses descriptor mode:

```bash
make airgap-demo
```

For a real transfer bundle, use fetch mode on the connected side:

1. Validate the source lock.
2. Fetch every artifact from its canonical source.
3. Verify every fetched payload against the lock.
4. Export images as OCI-native archives where practical.
5. Include charts, CRDs, wheels, and tools.
6. Write the deterministic inventory and checksum manifest.
7. Attach optional metadata such as SBOM, malware scan, or signature reports.
8. Split the bundle for transfer media when required.

```bash
airgap-ai-gateway bundle build \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --dist-dir dist/airgap-bundles \
  --payload-mode fetch \
  --metadata-hook sbom \
  --metadata-hook signature
```

Large payloads are written under `dist/`, which is outside version control.

## Offline verification

The disconnected side has to be able to verify the bundle without a network path:

```bash
airgap-ai-gateway bundle verify \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --bundle-dir dist/airgap-bundles/baseline-v1.3.1
```

The verifier recomputes local checksums and compares the bundle inventory against
the lock. One changed byte fails verification.

## Registry promotion

Promotion is an exact source-to-destination mapping.

Preferred OCI-native copy:

```bash
skopeo copy docker://SOURCE_DIGEST docker://INTERNAL_DESTINATION_DIGEST
```

Docker fallback:

```bash
docker pull SOURCE_DIGEST
docker tag SOURCE_DIGEST INTERNAL_DESTINATION_DIGEST
docker push INTERNAL_DESTINATION_DIGEST
```

Generate the promotion plan:

```bash
airgap-ai-gateway registry promote \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --registry registry.example.internal:5000 \
  --output-file dist/airgap-bundles/promotion-plan.json
```

Apply the approved promotion plan from the disconnected side:

```bash
airgap-ai-gateway --config examples/config registry promote apply \
  --plan-file dist/airgap-bundles/promotion-plan.json \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY \
  --commands-log dist/airgap-bundles/promotion-commands.log
```

For multi-node clusters, use an internal registry every node can reach. Loading
images onto a single node works right up until something reschedules.

## Rendered-manifest proof

The last check compares the rendered manifests against the promoted image map:

```bash
airgap-ai-gateway verify \
  --lock-file airgap/sources.lock.yaml \
  --compatibility-set baseline-v1.3.1 \
  --overlay production-reference \
  --registry registry.example.internal:5000
```

It rejects public registries, mutable tags, missing policies, unprotected routes,
and rendered Secret data.

Worth repeating: air-gap compliance is not a one-time check at install. Any later
chart update or manifest change can quietly reintroduce a public registry
reference, so run this again after upgrades.

## Transfer media

Large bundles can be split into fixed-size parts. Each part carries its own
SHA-256 checksum and so does the complete file. The disconnected side verifies
the parts before reassembly and verifies the whole file afterwards.

## What stays in Git

Tracked here:

- Source lock.
- Schemas and validation logic.
- Kustomize source.
- Bundle and promotion scripts.
- Example reports.

Not tracked: OCI archives, chart archives, wheelhouses, generated install media,
or runtime bundle payloads.
