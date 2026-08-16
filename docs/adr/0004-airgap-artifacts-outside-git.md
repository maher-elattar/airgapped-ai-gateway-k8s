# ADR 0004: Air-Gap Runtime Artifacts Stay Outside Git

## Context

An air-gapped installation needs container images, chart packages, CRDs, rendered
manifests, checksums, and transfer records. All of that is necessary operational
material. None of it is the authored source of this implementation.

There is a real pull toward committing it anyway, because having everything in
one place feels reproducible. In practice it bloats history with binaries,
pulls in license-sensitive and environment-specific content, and blurs the line
between what someone wrote and what a tool generated.

## Decision

Keep air-gap runtime artifacts outside Git.

The repository may contain:

- artifact inventory schemas;
- fake examples;
- expected checksums for repository-owned static fixtures;
- scripts or instructions that build an offline bundle;
- private registry mapping logic using placeholder values;
- documentation of the required artifacts and verification steps.

The repository must not contain:

- image archives;
- chart archives;
- rendered third-party CRDs;
- rendered environment-specific manifests;
- generated run directories;
- generated operator packages;
- nested delivery archives;
- environment-specific registry names;
- runtime secrets.

## Alternatives

1. Commit the complete offline bundle.
   - Rejected. Binary artifacts bloat history and can carry environment-specific
     or license-sensitive content.
2. Commit rendered third-party CRDs and Helm output as source.
   - Rejected. Rendered output is a result, not a source of truth.
3. Rely on an operator's shell history to recreate the bundle.
   - Rejected. Air-gap delivery needs repeatable, auditable steps that survive
     the person who ran them last.

## Consequences

- The repository stays a reasonable size and safe to publish.
- Bundle generation has to be reproducible from retained scripts and documented
  inputs.
- Operators need a separate artifact storage and transfer process.
- Checksums and manifests have to tie the artifact set to a specific deployment.
- Generated runtime resources get reviewed but never become permanent source.

## Validation

Acceptance checks have to verify:

- no binary delivery artifact is tracked;
- no rendered third-party CRD is tracked as source;
- no generated run directory is tracked;
- no environment-specific registry or domain identifier appears;
- scripts or docs explain how artifacts are discovered, checksummed,
  transferred, loaded, and mapped to a private registry.
