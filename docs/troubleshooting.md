# Troubleshooting Guide

Start from the signal. A response code or a Kubernetes condition will point you
at the right layer far faster than editing manifests and watching to see whether
the symptom moves.

![HTTP and status troubleshooting flow](assets/diagrams/rendered/http-status-troubleshooting-flow.svg)

## 401 identity

401 means the gateway never established a valid application identity.

Check:

- Is the API key missing?
- Is the `Authorization` header formatted correctly?
- Is the key known to the runtime credential source?
- Is the Secret or external-secret object present?
- Is the credential referenced by the policy path?

Expected behavior:

- Missing key returns 401.
- Unknown key returns 401.
- The model backend never sees the request.

## 403 entitlement

403 means the consumer is known and the route permission does not allow the call.

Check:

- Does the consumer have the model permission field?
- Is the policy attached to the intended HTTPRoute?
- Did the model start default-deny?
- Was the wrong consumer key used in the test?

Expected behavior:

- The same consumer can get 200 on one model and 403 on another.

## 404 Host or route match

404 usually means the request never matched the listener or the route. It is
worth remembering that the gateway can be completely healthy and still return
404, because you asked it for a hostname it does not own.

Check:

- Host header.
- Gateway listener hostname.
- HTTPRoute hostname.
- HTTPRoute path match.
- Edge Host preservation.
- Whether the request reached the gateway at all.

Expected behavior:

- Wrong Host returns 404.
- Valid Host and path continue on to the identity checks.

## 429 descriptor and counter path

429 means the request matched a rate-limit descriptor and exceeded its limit. The
more common problem is the opposite: 429 never appears when it should, because a
descriptor name and a config key drifted apart while everything stayed Running.

Check:

- Does the descriptor include the expected `consumer_id`?
- Does the policy use the same metadata field as metrics?
- Is Envoy ratelimit healthy?
- Is Redis reachable from the ratelimit service?
- Is the low-limit test using the intended consumer?
- Are counters shared across replicas the way you expect?

Expected behavior:

- A dedicated low-limit consumer reaches 429 under repeated traffic.
- No other consumer is throttled by that test.

## ResolvedRefs=False backend references

`ResolvedRefs=False` means the route cannot resolve a backend reference. Nothing
downstream of that is worth investigating yet, and rotating keys certainly will
not help.

Check:

- Service name.
- Service namespace.
- Service port name or number.
- ReferenceGrant mode for cross-namespace targets.
- CRD version compatibility.

Expected behavior:

- Same-namespace Service backends resolve without a ReferenceGrant.
- Cross-namespace references require an explicit trust decision.

## Attached=False policy target

`Attached=False` means the policy did not attach to its target. A typo in
`targetRef` produces a perfectly valid Kubernetes object that protects nothing,
and the API server has no opinion about it. The controller status does.

Check:

- Policy `targetRef` kind.
- Policy `targetRef` name.
- Policy namespace.
- Route existence.
- agentgateway CRD version.
- Controller logs.

Expected behavior:

- Every model route has an attached policy.
- A route without a policy is not a valid route.

## Programmed=False controller or Gateway

`Programmed=False` points at controller or generated data-plane reconciliation.

Check:

- GatewayClass exists.
- Gateway API CRDs match the compatibility set.
- agentgateway controller is ready.
- AgentgatewayParameters are valid for the baseline.
- Generated gateway Deployment exists and rolls out.
- NetworkPolicies allow the required control and data-plane paths.

Expected behavior:

- Gateway reaches `Programmed=True`.
- Generated data-plane objects are inspected for health, while durable changes go
  through the authored inputs.

## Evidence to collect

Collect the redacted versions of:

- Gateway JSON.
- HTTPRoute JSON.
- AgentgatewayPolicy JSON.
- Deployment rollout status.
- Namespace events.
- Gateway and controller logs.
- Command log.
- Request matrix report.
