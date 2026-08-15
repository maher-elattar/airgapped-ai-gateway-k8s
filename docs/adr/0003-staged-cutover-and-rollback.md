# ADR 0003: Staged Cutover and Rollback

## Context

The platform may be introduced into an environment that already has a working public or internal edge path to NIM Services. The goal is to add policy and observability without making traffic migration and gateway installation one irreversible action.

The reusable source workflow separates discovery, installation, internal verification, edge cutover, public verification, and rollback.

## Decision

Use staged cutover:

1. Discover existing Services, routes, and edge state.
2. Back up relevant existing state before changes.
3. Install or render the gateway layer without changing production traffic.
4. Verify the internal gateway path directly.
5. Change the external edge only after policy tests pass.
6. Verify through the real client path.
7. Roll back the edge route first if public traffic fails.

Retained NGINX environments roll back by restoring the previous edge backend. Greenfield environments roll back by restoring the previous load-balancer or exposure route.

## Alternatives

1. Install gateway and cut over in one step.
   - Rejected because it combines too many failure domains.
2. Uninstall the gateway immediately on any public failure.
   - Rejected because rollback should first restore the last known working traffic path.
3. Patch live edge resources without a retained source or backup.
   - Rejected because it is not reproducible.

## Consequences

- The first production recovery action is small and targeted.
- Internal success plus public failure narrows investigation to edge, DNS, TLS, or firewall layers.
- Gateway resources may remain installed during rollback for debugging.
- Backups must be protected because they may contain sensitive operational state.

## Validation

Future validation must prove:

- discovery produces a reviewable plan before apply;
- backups are created before cutover;
- internal path works before edge changes;
- public path works after cutover;
- rollback restores the previous route;
- all mutable actions are driven by checked-in source or retained scripts.
