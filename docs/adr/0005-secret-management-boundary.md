# ADR 0005: Secret Management Boundary

## Context

The gateway needs consumer credentials, and future integrations may need backend
credentials too. The v1.3.1 baseline stores API keys as raw runtime credentials
in Kubernetes Secret objects, because the native hashed-key path in that version
was not reliable enough for this design.

That is a limitation worth stating plainly rather than hiding behind a clean
architecture diagram. It means Secret protection is part of the security
boundary, and it is the main reason the v1.4.0 hashed-key work sits in the
upgrade validation track.

## Decision

Runtime secrets stay outside Git and outside normal command logs.

For the v1.3.1 baseline:

- raw keys exist only in the runtime secret store;
- Kubernetes Secret encryption at rest is required if native Secrets are used;
- RBAC restricts credential reads and writes;
- generated reports redact values;
- examples use unmistakably fake values;
- browser JavaScript never contains long-lived gateway keys.

For future production designs:

- prefer external-secret integration;
- keep secret names, labels, and metadata schema in Git;
- keep secret values in approved secret-management systems;
- validate v1.4.0 hashed-key support before changing the baseline.

## Alternatives

1. Store keys in Git for reproducibility.
   - Rejected. Credential values are runtime material, and Git is forever.
2. Use one shared key for all consumers.
   - Rejected. It destroys attribution and widens the blast radius of any single
     leak.
3. Move to v1.4.0 hashed keys immediately, without tests.
   - Rejected. No support claim without passing compatibility tests, even for a
     change that improves security.
4. Put a gateway key in browser JavaScript.
   - Rejected. Public browser code cannot keep a long-lived bearer credential
     secret, regardless of how it is obfuscated.

## Consequences

- Local render, lint, and dry-run never produce real secret values.
- Test fixtures use fake, non-sensitive tokens.
- Consumer metadata becomes the policy object; key material stays a runtime
  dependency.
- Rotation requires overlap and verification.
- The baseline stays less secure than the v1.4.0 hashed-key target until that
  path is tested.

## Validation

Validation has to prove:

- the secret scanner blocks known secret patterns and environment-specific
  identifiers;
- no real secret files are tracked;
- generated reports redact secret values;
- missing or invalid credentials fail with 401;
- valid but unauthorized consumers fail with 403;
- rotation happens without an application outage;
- v1.4.0 hashed-key migration works before the baseline changes.
