# Consumer Lifecycle Guide

A consumer is an application workload identity. It is the name the gateway uses
when it decides access, counts usage, emits metrics, and gives an operator
something to search for at three in the morning.

![Consumer disable versus revoke](assets/diagrams/article/16-consumer-disable-vs-revoke.png)

## Consumer metadata

A useful consumer record has:

- Stable key.
- Display name.
- Runtime credential reference.
- Allowed model list.
- Rate-limit tier or explicit limits.
- Enough workload metadata to identify it later.

The credential value is not repository source. Only the contract and the fake
examples belong here.

## What the CLI owns, and what it does not

Consumer operations are split across two systems, and keeping the boundary clear
avoids the most common mistake in this area.

The CLI owns the **entitlement record**: the consumer key, display name, allowed
model list, and rate-limit tier, all of which live in repository source. Every
consumer command is a `plan` / `apply` pair that edits those files, verifies the
recorded content hashes before writing, and never contacts Kubernetes.

The environment's secret workflow owns the **credential material** — the key
value itself. No CLI command creates, prints, or stores one.

So `consumer revoke apply` removes the entitlement from source. It does not
invalidate a key that is already in circulation. Revoking a compromised
credential means doing both: remove the entitlement here, and invalidate the
material in the secret system.

## Add a consumer

Plan the addition:

```bash
airgap-ai-gateway --config examples/config consumer add plan \
  --consumer-key search-app \
  --display-name "Search App" \
  --allowed-model qwen-chat \
  --allowed-model gemma-chat \
  --requests-per-minute 60 \
  --output-dir runs/plans/consumer-search-app
```

Apply the approved source plan:

```bash
airgap-ai-gateway --config examples/config consumer add apply \
  --plan-file runs/plans/consumer-search-app/plan.json \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY
```

Then add the runtime credential material through the environment's secret
workflow. Start with the smallest model list the workload actually needs;
widening it later is easy, and narrowing it after something depends on it is not.

## Rotate a key

Rotate with overlap:

1. Add a new runtime credential for the same consumer identity.
2. Deploy the workload with the new credential.
3. Verify traffic and metrics still use the same `consumer_id`.
4. Remove or disable the old credential.

That keeps the application up and keeps attribution stable across the change.

Steps 1 and 4 happen in the secret system. The CLI records the source-side
rotation boundary, so the entitlement record and its audit trail move with the
credential rather than lagging behind it:

```bash
airgap-ai-gateway --config examples/config consumer rotate plan \
  --consumer-key search-app \
  --output-dir runs/plans/consumer-search-app-rotate

airgap-ai-gateway --config examples/config consumer rotate apply \
  --plan-file runs/plans/consumer-search-app-rotate/plan.json \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY
```

## Disable versus revoke

Two different operations:

- Disable keeps the identity record and blocks model access.
- Revoke removes or invalidates the credential material.

Disable is what you want when audit continuity matters. Revoke is what you need
when a key is exposed, or when you merely suspect it is.

Note that deleting a route policy is not a way to remove one consumer's access.
That would affect every consumer of the model and leave the route unprotected in
the meantime. Change the consumer's entitlement instead.

Removing the entitlement record is the source-side half of a revocation. Pair it
with invalidating the credential in the secret system, as described above:

```bash
airgap-ai-gateway --config examples/config consumer revoke plan \
  --consumer-key search-app \
  --output-dir runs/plans/consumer-search-app-revoke

airgap-ai-gateway --config examples/config consumer revoke apply \
  --plan-file runs/plans/consumer-search-app-revoke/plan.json \
  --confirm I_UNDERSTAND_DISPOSABLE_CONTEXT_ONLY
```

## Browser rule

Long-lived gateway keys do not go into public browser JavaScript.

```text
browser -> application backend -> gateway -> model
```

The backend holds the application credential. A direct browser path to the model
needs a short-lived token design and its own threat review.

## Rate-limit descriptors

Descriptors should use stable metadata: `consumer_id`, model key, and tier. Keep
the same `consumer_id` in metrics so a request can be traced end to end without
guesswork.

## Verify after change

After any consumer change:

- Missing key still returns 401.
- Unknown key still returns 401.
- The changed consumer gets 200 only on allowed models.
- The changed consumer gets 403 on denied models.
- Low-limit consumers reach 429.
- Reports redact credential values.
