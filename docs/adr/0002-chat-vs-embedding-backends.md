# ADR 0002: Chat Routes and Embedding Routes Use Different Backend Patterns

## Context

The delivered baseline is agentgateway v1.3.1. In the tested source evidence, OpenAI-compatible chat endpoints worked naturally through AgentgatewayBackend configured as an AI provider backend. Embedding endpoints did not behave like chat completions and should not be forced into a chat-style abstraction.

The embedding route still needs gateway policy. The difference is only the final backend representation.

## Decision

Use AgentgatewayBackend for OpenAI-compatible chat completion routes in the v1.3.1 baseline.

Route embedding APIs through agentgateway to the normal Kubernetes Service backend in the v1.3.1 baseline.

Both route types must still receive:

- authentication;
- authorization;
- rate limiting;
- observability attributes;
- default-deny onboarding.

## Alternatives

1. Force embeddings through the chat AgentgatewayBackend abstraction.
   - Rejected because the tested baseline expected chat request semantics and failed on embedding request shape.
2. Bypass agentgateway for embeddings.
   - Rejected because embeddings still require identity, authorization, rate limiting, and observability.
3. Claim newer agentgateway behavior without testing.
   - Rejected because this repository preserves the delivered baseline until compatibility tests pass.

## Consequences

- Chat and embedding routes have different backend manifests.
- Rate limits may use different descriptors because chat token accounting and embedding request accounting are not equivalent.
- Tests must cover both route types.
- Upgrade work must explicitly retest embedding behavior.

## Validation

Future validation must prove:

- chat route accepts valid chat completion request shape;
- embedding route accepts valid embedding request shape;
- embedding route does not fail due to missing chat message fields;
- authorized and denied consumers behave consistently for both route classes;
- rate-limit descriptors are correct for both route classes.
