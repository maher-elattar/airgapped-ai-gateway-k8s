# ADR 0004: Air-Gap Runtime Artifacts Stay Outside Git

## Context

An air-gapped installation needs container images, chart packages, CRDs, rendered manifests, checksums, and transfer records. The private implementation evidence contained binary images, chart archives, rendered third-party CRDs, rendered manifests, and generated handover artifacts. Those are evidence, not clean public source.

This repository must be safe to publish and reproducible from declarative inputs.

## Decision

Keep air-gap runtime artifacts outside Git.

The repository may contain:

- artifact inventory schemas;
- fake examples;
- expected checksums for repository-owned static fixtures;
- scripts or instructions that create an offline bundle;
- private registry mapping logic with placeholders;
- documentation of required artifacts and verification steps.

The repository must not contain:

- image archives;
- chart archives;
- rendered third-party CRDs;
- rendered environment manifests;
- generated run directories;
- DOCX/PDF handover artifacts;
- nested delivery ZIP files;
- private registry names;
- runtime secrets.

## Alternatives

1. Commit the complete offline bundle.
   - Rejected because binary artifacts bloat history and may contain environment-specific or license-sensitive content.
2. Commit rendered third-party CRDs and Helm output as source.
   - Rejected because rendered output is not the clean source of truth.
3. Rely on an operator's shell history to recreate the bundle.
   - Rejected because air-gap delivery needs repeatable, auditable steps.

## Consequences

- The repo stays publishable.
- Offline bundle generation must be reproducible from retained scripts and documented inputs.
- Operators need a separate artifact storage and transfer process.
- Checksums and manifests must tie together the artifact set used for a specific deployment.
- Generated runtime resources are reviewed but not treated as permanent source.

## Validation

Acceptance checks must verify:

- no archive or handover binary is tracked;
- no rendered third-party CRD is tracked as source;
- no generated run directory is tracked;
- no private registry or domain identifier appears;
- clean scripts or docs explain how artifacts are discovered, checksummed, transferred, loaded, and mapped to a private registry.
