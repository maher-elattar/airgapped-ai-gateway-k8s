# Compatibility and Version Track

This repository is conservative about support claims. A version counts as
supported only when the manifests, planner, tests, and disposable lab prove the
behavior from a clean checkout.

The current implementation preserves the tested agentgateway v1.3.1 and Gateway
API v1.5.0 experimental path. agentgateway v1.4.0 is tracked separately because
it advertises changes worth validating, hashed-key support in particular.

Evidence levels used in the table:

- Repository baseline: proven by the docs, examples, Kustomize manifests, and
  tests in this repository.
- Architecture contract: described by the architecture docs and ADRs.
- Official release evidence: linked upstream release or documentation.
- Not tested: no support claim here.

| component | delivered version | supported API | status | evidence | upgrade risk |
| --- | --- | --- | --- | --- | --- |
| agentgateway Kubernetes controller and data plane | v1.3.1 | `agentgateway.dev/v1alpha1` resources, Gateway API-backed controller reconciliation, AgentgatewayBackend for chat routes | Delivered baseline | Repository baseline; [agentgateway v1.3.1 release](https://github.com/agentgateway/agentgateway/releases/tag/v1.3.1) | Tested behavior must be preserved. Do not upgrade in place without compatibility tests. |
| agentgateway compatibility track | v1.4.0 | Newer Kubernetes agentgateway APIs including experimental AgentgatewayModel, hashed virtual keys, and Gateway API v1.6 support | Validation target only; not supported yet | [agentgateway v1.4.0 release](https://github.com/agentgateway/agentgateway/releases/tag/v1.4.0) | Breaking Gateway API dependency change, new API behavior, hashed-key migration, telemetry and policy behavior changes. |
| Gateway API | v1.5.0 experimental | `gateway.networking.k8s.io/v1` GatewayClass, Gateway, HTTPRoute; ReferenceGrant for explicit cross-namespace trust | Delivered baseline | Repository baseline; [Gateway API v1.5.0 release](https://github.com/kubernetes-sigs/gateway-api/releases/tag/v1.5.0) | The experimental channel carries compatibility risk; upgrade admission policies and CRD size/apply behavior need explicit planning. |
| Gateway API compatibility track | v1.6.x, not delivered | Gateway API version expected by agentgateway v1.4.0 | Validation target only; not supported yet | agentgateway v1.4.0 release notes state Gateway API v1.6 support | Matching CRDs must be reapplied, then Gateway, HTTPRoute, ReferenceGrant, and controller reconciliation retested. |
| OpenAI-compatible chat NIM Services | External model runtime; version not owned here | `/v1/chat/completions`-style API through AgentgatewayBackend | Supported by the delivered baseline when the NIM Service is already healthy | Architecture contract; repository manifests | The model runtime stays separate. Retest if the NIM API shape or agentgateway provider behavior changes. |
| Embedding NIM Services | External model runtime; version not owned here | `/v1/embeddings`-style API routed through the gateway to a Kubernetes Service backend | Supported by the delivered baseline through direct Service routing, not a chat AgentgatewayBackend | Architecture contract; repository manifests | Retest on every agentgateway upgrade. Chat provider semantics do not carry over to embeddings. |
| Existing NGINX edge | External existing edge; version not owned here | HTTP/TLS host routing to the internal gateway Service | Supported integration pattern, not a required component | Architecture contract; retained-nginx-edge overlay | Cutover and rollback depend on correct edge backups. Greenfield environments can drop NGINX entirely. |
| Greenfield exposure layer | Not delivered | Approved load balancer or ingress exposure to the generated data plane | Design option only; not implemented | Architecture contract | DNS, TLS, firewall, and Service exposure have to be designed declaratively before any support claim. |
| Generated gateway data-plane Deployment and Service | Generated from agentgateway v1.3.1 inputs | Runtime proxy resources produced by the controller | Operational output reconciled from authored inputs | Architecture contract; repository manifests | Direct runtime edits create drift and can be overwritten by reconciliation. |
| Envoy rate-limit service | Demo dependency pinned through the image map | Remote rate-limit descriptor evaluation | Baseline dependency pattern; production pinning required | Repository manifests and tests | Mutable image tags and a single-replica topology will not carry a real SLO. |
| Redis counter store | Demo Redis pinned through the image map; external HA Redis in production-reference | Counter storage for the rate-limit service | Demo in kind and retained-edge; external HA contract in production-reference | Repository manifests and tests | One Redis Deployment may not meet availability or durability requirements. |
| Kubernetes cluster | Environment-specific | CRDs, Services, RBAC, NetworkPolicies, Secrets, Gateway API resources | Environmental prerequisite; no version claim from this repository yet | Not tested | Cluster version, admission policy, and controller compatibility should be tested in disposable kind before any claim. |
| Helm charts | Not vendored | Optional upstream install/render input outside the authored manifests | Not part of the repository source of truth | Not tested | Chart archives stay outside Git. Rendered output is inspectable, not permanent source. |

## Support rule

This repository supports what it can test from clean source. For now:

- v1.3.1 is the delivered baseline to preserve.
- v1.4.0 is a separate validation target.
- Anything newer is unsupported until tests are added here and pass.
