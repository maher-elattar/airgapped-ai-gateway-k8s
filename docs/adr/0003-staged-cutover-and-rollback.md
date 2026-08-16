# ADR 0003: Staged Cutover and Rollback

## Context

This platform usually arrives in an environment that already has a working path
from applications to NIM Services. The job is to add policy and observability
without turning traffic migration and gateway installation into one irreversible
action.

Combining them is tempting because it is fewer steps. It is also how a failed
change turns into a debugging session across five layers at once, during a
maintenance window, with traffic down.

So discovery, installation, internal verification, edge cutover, public
verification, and rollback stay separate operations.

## Decision

Use staged cutover:

1. Discover existing Services, routes, and edge state.
2. Back up the relevant existing state before changing anything.
3. Install or render the gateway layer without touching production traffic.
4. Verify the internal gateway path directly.
5. Change the external edge only after the policy tests pass.
6. Verify through the real client path.
7. Roll the edge route back first if public traffic fails.

Retained NGINX environments roll back by restoring the previous edge backend.
Greenfield environments roll back by restoring the previous load-balancer or
exposure route.

## Alternatives

1. Install the gateway and cut over in one step.
   - Rejected. It merges too many failure domains into one change.
2. Uninstall the gateway immediately on any public failure.
   - Rejected. Rollback should restore the last known working traffic path
     first, then investigate.
3. Patch live edge resources with no retained source or backup.
   - Rejected. It is not reproducible, and the old working path is the simplest
     rollback anchor there is.

## Consequences

- The first production recovery action is small and targeted.
- Internal success with public failure narrows the search to edge, DNS, TLS, or
  firewall layers.
- Gateway resources can stay installed during rollback, which helps debugging.
- Backups need protecting, because they can contain sensitive operational state.

## Validation

Validation has to prove:

- discovery produces a reviewable plan before apply;
- backups are created before cutover;
- the internal path works before the edge changes;
- the public path works after cutover;
- rollback restores the previous route;
- every mutating action comes from checked-in source or retained scripts.
