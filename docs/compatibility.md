# Compatibility Matrix

The delivered baseline remains agentgateway v1.3.1 with Gateway API v1.5.0 experimental. Newer versions are validation targets only until static tests, unit tests, and disposable kind-cluster tests pass.

Evidence levels used below:

- Repository baseline: represented by the public docs, examples, Kustomize manifests, and tests in this repository.
- Architecture contract: described by the public architecture and ADRs.
- Official release evidence: linked upstream release or documentation.
- Not tested: no support claim in this repository.

| component | delivered version | supported API | status | evidence | upgrade risk |
| --- | --- | --- | --- | --- | --- |
| agentgateway Kubernetes controller and data plane | v1.3.1 | `agentgateway.dev/v1alpha1` resources, Gateway API-backed controller reconciliation, AgentgatewayBackend for chat routes | Delivered baseline | Repository baseline; [agentgateway v1.3.1 release](https://github.com/agentgateway/agentgateway/releases/tag/v1.3.1) | Must preserve tested behavior. Do not upgrade in-place without compatibility tests. |
| agentgateway compatibility track | v1.4.0 | Newer Kubernetes agentgateway APIs including experimental AgentgatewayModel, hashed virtual keys, and Gateway API v1.6 support | Validation target only; not supported yet | [agentgateway v1.4.0 release](https://github.com/agentgateway/agentgateway/releases/tag/v1.4.0) | Breaking Gateway API dependency change, new API behavior, hashed-key migration, telemetry and policy behavior changes. |
| Gateway API | v1.5.0 experimental | `gateway.networking.k8s.io/v1` GatewayClass, Gateway, HTTPRoute; ReferenceGrant for explicit cross-namespace trust | Delivered baseline | Repository baseline; [Gateway API v1.5.0 release](https://github.com/kubernetes-sigs/gateway-api/releases/tag/v1.5.0) | Experimental channel has compatibility risk; upgrade admission policies and CRD size/apply behavior require explicit planning. |
| Gateway API compatibility track | v1.6.x, not delivered | Gateway API version expected by agentgateway v1.4.0 | Validation target only; not supported yet | Official agentgateway v1.4.0 release notes state Gateway API v1.6 support | Must reapply matching CRDs and retest Gateway, HTTPRoute, ReferenceGrant, and controller reconciliation. |
| OpenAI-compatible chat NIM Services | External model runtime; version not owned here | `/v1/chat/completions`-style API through AgentgatewayBackend | Supported by delivered baseline when NIM Service is already healthy | Architecture contract; repository manifests | Model runtime remains separate. Retest if NIM API shape or agentgateway provider behavior changes. |
| Embedding NIM Services | External model runtime; version not owned here | `/v1/embeddings`-style API routed through gateway to Kubernetes Service backend | Supported by delivered baseline through direct Service routing, not chat AgentgatewayBackend | Architecture contract; repository manifests | Must retest on agentgateway upgrades. Do not assume chat provider semantics apply to embeddings. |
| Existing NGINX edge | External existing edge; version not owned here | HTTP/TLS host routing to internal gateway Service | Supported integration pattern, not a required component | Architecture contract; retained-nginx-edge overlay | Cutover and rollback depend on correct edge backups. Greenfield environments may omit NGINX. |
| Greenfield exposure layer | Not delivered | Approved load balancer or ingress exposure to generated data plane | Design option only; not implemented | Architecture contract | DNS, TLS, firewall, and Service exposure must be designed declaratively before support is claimed. |
| Generated gateway data-plane Deployment and Service | Generated from agentgateway v1.3.1 inputs | Runtime proxy resources produced by the controller | Operational output reconciled from authored inputs | Architecture contract; repository manifests | Direct runtime edits create drift and may be overwritten by reconciliation. |
| Envoy rate-limit service | Demo dependency pinned through image map | Remote rate-limit descriptor evaluation | Baseline dependency pattern; production pinning required | Repository manifests and tests | Mutable image tags and simple deployment topology are not sufficient for larger SLOs. |
| Redis counter store | Demo Redis dependency pinned through image map; external HA Redis in production-reference | Counter storage for rate-limit service | Demo in kind/retained-edge; external HA contract in production-reference | Repository manifests and tests | Single Redis deployment may not meet availability or durability requirements. |
| Kubernetes cluster | Environment-specific | CRDs, Services, RBAC, NetworkPolicies, Secrets, Gateway API resources | Environmental prerequisite, not version-supported by this repository yet | Not tested | Cluster version, admission policy, and controller compatibility must be tested in disposable kind before claims. |
| Helm charts | Not vendored | Optional upstream install/render input outside authored manifests | Not part of repository source of truth | Not tested | Chart archives stay outside Git. Rendered output is inspectable, not permanent source of truth. |

## Support rule

The repository supports only what it can test from clean source.

For now:

- v1.3.1 is the delivered baseline to preserve.
- v1.4.0 is a separate validation target.
- Current or newer upstream versions are not supported unless this repository adds and passes tests for them.
