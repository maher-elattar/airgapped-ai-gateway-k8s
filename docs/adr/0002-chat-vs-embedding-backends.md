# ADR 0002: Chat Routes and Embedding Routes Use Different Backend Patterns

## Context

The delivered baseline is agentgateway v1.3.1. In that version, OpenAI-compatible
chat endpoints work naturally with AgentgatewayBackend configured as an AI
provider backend.

Embeddings are a different API shape. A chat request carries a `messages[]`
array; an embedding request carries `input` and goes to `/v1/embeddings`. Trying
to represent the embedding endpoint through the same chat-style
AgentgatewayBackend made the gateway expect chat request semantics, and requests
failed on a missing `messages` field.

Rather than bend the embedding API into an abstraction that does not fit it, the
baseline routes embeddings differently. The route still needs every gateway
policy; only the final backend representation changes.

## Decision

For the v1.3.1 baseline:

- OpenAI-compatible chat completion routes use AgentgatewayBackend.
- Embedding APIs route through agentgateway to the normal Kubernetes Service
  backend.

Both route types still receive:

- authentication;
- authorization;
- rate limiting;
- observability attributes;
- default-deny onboarding.

## Alternatives

1. Force embeddings through the chat AgentgatewayBackend abstraction.
   - Rejected. The tested baseline expected chat request semantics and failed on
     the embedding request shape.
2. Bypass agentgateway for embeddings.
   - Rejected. Embeddings still need identity, authorization, rate limiting, and
     observability, and routing around the gateway would give up all four.
3. Claim newer agentgateway behavior without testing it.
   - Rejected. The delivered baseline is preserved until compatibility tests
     pass.

## Consequences

- Chat and embedding routes have different backend manifests.
- Rate limits may need different descriptors, since chat token accounting and
  embedding request accounting are not equivalent.
- Tests have to cover both route types.
- Any upgrade has to explicitly retest embedding behavior, because this is
  version-specific behavior rather than a permanent property of the design.

## Validation

Validation has to prove:

- the chat route accepts a valid chat completion request shape;
- the embedding route accepts a valid embedding request shape;
- the embedding route does not fail on missing chat message fields;
- authorized and denied consumers behave consistently across both route classes;
- rate-limit descriptors are correct for both route classes.
