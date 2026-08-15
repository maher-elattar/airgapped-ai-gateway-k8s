# ADR 0001: One Gateway with Separate Model Routes

## Context

The platform needs one stable entry point for internal AI traffic while keeping model authorization understandable as the number of models and consumers grows.

The source evidence used a single model gateway and one HTTPRoute per model. That kept the model-to-policy relationship direct: if an application is denied on one model but allowed on another, the operator can inspect one route and one policy boundary for that model.

## Decision

Use one Gateway for the AI platform and a separate HTTPRoute for each model-facing API.

Each model route owns:

- host and path matching for that model API;
- backend reference for the tested route type;
- route-targeted authorization and rate-limit policy;
- model-specific validation tests.

The Gateway owns:

- listener scope;
- allowed route attachment;
- generated data-plane lifecycle;
- broad platform entry-point behavior.

## Alternatives

1. One route containing all model paths.
   - Rejected because policy ownership becomes less obvious and failures are harder to isolate.
2. One Gateway per model.
   - Rejected for the baseline because it creates more generated data planes and operational overhead before there is a need.
3. Keep using only the existing edge routes.
   - Rejected because the edge does not provide the AI-specific policy and observability boundary.

## Consequences

- Adding a model creates a visible route/policy unit.
- Authorization remains easy to audit per model.
- Same-namespace routing is the default trust boundary.
- Cross-namespace routing requires explicit ReferenceGrant design.
- Operators must avoid route sprawl by keeping names derived from one model key.

## Validation

Future validation must prove:

- Gateway is programmed.
- Each HTTPRoute is attached and resolved.
- Missing key returns 401.
- Known but unauthorized consumer returns 403.
- Authorized consumer reaches only the intended model.
- Unknown model paths do not expose unintended NIM endpoints.
