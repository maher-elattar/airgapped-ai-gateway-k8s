# ADR 0001: One Gateway with Separate Model Routes

## Context

The platform needs one stable entry point for internal AI traffic, and it needs
model authorization to stay comprehensible as the number of models and consumers
grows. Those two goals pull in slightly different directions: one object is
easier to operate, many objects are easier to reason about individually.

The compromise here is a single model gateway with one HTTPRoute per model. It
keeps the model-to-policy relationship direct. When an application is denied on
one model and allowed on another, there is one route and one policy to inspect,
not a shared object with conditional behavior inside it.

## Decision

Use one Gateway for the AI platform and a separate HTTPRoute for each
model-facing API.

Each model route owns:

- host and path matching for that model API;
- the backend reference for the tested route type;
- route-targeted authorization and rate-limit policy;
- model-specific validation tests.

The Gateway owns:

- listener scope;
- allowed route attachment;
- generated data-plane lifecycle;
- platform entry-point behavior.

## Alternatives

1. One route containing all model paths.
   - Rejected. Policy ownership stops being obvious and failures get harder to
     isolate.
2. One Gateway per model.
   - Rejected for the baseline. It multiplies generated data planes and
     operational overhead before there is any need for it.
3. Keep using only the existing edge routes.
   - Rejected. The edge does not provide the AI-specific policy and
     observability boundary this platform exists for.

## Consequences

- Adding a model produces a visible route and policy unit.
- Authorization stays auditable per model.
- Same-namespace routing is the default trust boundary.
- Cross-namespace routing requires explicit ReferenceGrant design.
- Route sprawl becomes a real risk, so names stay derived from one model key.

## Validation

Validation has to prove:

- the Gateway is programmed;
- each HTTPRoute is attached and resolved;
- a missing key returns 401;
- a known but unauthorized consumer returns 403;
- an authorized consumer reaches only the intended model;
- unknown model paths do not expose unintended NIM endpoints.
