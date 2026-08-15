# ADR 0005: Secret Management Boundary

## Context

The gateway requires consumer credentials and may require backend credentials in future integrations. The v1.3.1 baseline documents the raw-key runtime path and keeps hashed-key support in the upgrade validation track.

This is a production limitation, not a desired end state.

## Decision

Runtime secrets stay outside Git and outside normal command logs.

For the v1.3.1 baseline:

- raw keys may exist only in the runtime secret store;
- Kubernetes Secret encryption at rest is required if native Secrets are used;
- RBAC must restrict credential reads and writes;
- generated reports must redact values;
- examples must use unmistakably fake values;
- browser JavaScript must not contain long-lived gateway keys.

For future production designs:

- prefer external-secret integration;
- keep secret names, labels, and metadata schema in Git;
- keep secret values in approved secret-management systems;
- validate v1.4.0 hashed-key support before changing the baseline.

## Alternatives

1. Store keys directly in Git for reproducibility.
   - Rejected because credential values are runtime material.
2. Use one shared key for all consumers.
   - Rejected because it destroys attribution and widens blast radius.
3. Move immediately to v1.4.0 hashed keys without tests.
   - Rejected because the repository must not claim support until compatibility tests pass.
4. Put a gateway key in browser JavaScript.
   - Rejected because public browser code cannot keep a long-lived bearer credential secret.

## Consequences

- Local render, lint, and dry-run must not create real secret values.
- Test fixtures must use fake, non-sensitive tokens.
- Consumer metadata becomes the policy object; key material remains a runtime dependency.
- Rotations require overlap and verification.
- The baseline remains less secure than the v1.4.0 hashed-key target until tested.

## Validation

Future validation must prove:

- secret scanner blocks known secret patterns and environment-specific identifiers;
- no real secret files are tracked;
- generated reports redact secret values;
- missing or invalid credentials fail with 401;
- valid but unauthorized consumers fail with 403;
- rotation can happen without application outage;
- v1.4.0 hashed-key migration works before baseline changes.
