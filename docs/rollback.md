# Rollback and Decommission Guide

Rollback should be smaller than the deployment that caused it. When the public
path fails after cutover, the first recovery action is to restore the previous
edge path, not to start tearing out the gateway.

## Rollback order

1. Restore the retained edge or exposure path.
2. Verify client traffic reaches the previous backend.
3. Keep the gateway installed if it helps diagnostics.
4. Roll back route or policy changes using the state ledger.
5. Remove gateway resources only after traffic no longer depends on them.

Recovery stays focused on the layer that changed most recently, which is almost
always the layer at fault.

## State ledger

The ledger records ownership:

- `pre-existing`: the resource existed before the run.
- `updated`: the resource existed and the run changed it.
- `created`: the run created the resource.

Rollback restores pre-existing and updated resources from the snapshot, and
deletes only what the same run created. That is what keeps a gateway rollback
from taking a model Service, Secret, Gateway, or policy with it.

## Plan rollback

```bash
airgap-ai-gateway --config examples/config rollback plan \
  --ledger-file runs/reports/deploy/ledger.json \
  --run-id run-1 \
  --apply-mode server-side-dry-run \
  --output-dir runs/plans/rollback
```

Read the plan before applying it. It should be immediately obvious which
resources are being restored and which created resources are being removed.

## Apply rollback

```bash
airgap-ai-gateway --config examples/config rollback apply \
  --expected-context kind-airgap-ai-gateway \
  --apply-mode server-side-dry-run \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY \
  --plan-file runs/plans/rollback/plan.json \
  --snapshot-file runs/snapshots/pre-change.json \
  --ledger-file runs/reports/deploy/ledger.json \
  --commands-log runs/reports/rollback/commands.log
```

Use a live-mode plan only once the rollback target and snapshot have been
reviewed.

## Cleanup guard

Cleanup fails closed when edge or ingress state cannot be read. If the platform
cannot prove that traffic no longer depends on the gateway, it does not get to
remove gateway resources.

## Decommission order

For a planned removal:

1. Drain or restore external edge traffic.
2. Verify no application traffic depends on the gateway.
3. Remove route policies.
4. Remove model routes.
5. Remove Gateway and parameter resources.
6. Remove rate-limit demo resources if nothing else shares them.
7. Remove namespace-scoped gateway support resources.
8. Leave the model Services for their owner to decommission separately.

Step 8 is not a formality. The gateway never owned those workloads, and a cleanup
that removes them is a much worse outage than the one it was meant to fix.

## Evidence to keep

Keep the redacted:

- Rollback plan JSON.
- Rollback Markdown summary.
- Commands log.
- State ledger.
- Pre-change snapshot.
- Post-rollback verification report.
