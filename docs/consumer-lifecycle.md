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

## Add a consumer

Plan the addition:

```bash
airgap-ai-gateway --config examples/config consumer add \
  --consumer-key internal-chat
```

Then update the configuration and the runtime secret material through the
approved secret workflow for that environment. Start with the smallest model list
the workload actually needs; widening it later is easy, and narrowing it after
something depends on it is not.

## Rotate a key

Rotate with overlap:

1. Add a new runtime credential for the same consumer identity.
2. Deploy the workload with the new credential.
3. Verify traffic and metrics still use the same `consumer_id`.
4. Remove or disable the old credential.

That keeps the application up and keeps attribution stable across the change.

## Disable versus revoke

Two different operations:

- Disable keeps the identity record and blocks model access.
- Revoke removes or invalidates the credential material.

Disable is what you want when audit continuity matters. Revoke is what you need
when a key is exposed, or when you merely suspect it is.

Note that deleting a route policy is not a way to remove one consumer's access.
That would affect every consumer of the model and leave the route unprotected in
the meantime. Change the consumer's entitlement instead.

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
