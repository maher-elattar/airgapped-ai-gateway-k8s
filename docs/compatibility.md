# Compatibility Matrix

The delivered baseline remains agentgateway v1.3.1 with Gateway API v1.5.0 experimental. Newer versions are validation targets only until static tests, unit tests, and disposable kind-cluster tests pass.

Evidence levels used below:

- Delivered artifact evidence: present in the source archive inventory.
- Article evidence: described in the local Medium article evidence.
- Source workflow evidence: reusable behavior observed in the Python and Markdown source.
- Official release evidence: linked upstream release or documentation.
- Not tested: no support claim in this repository.

| component | delivered version | supported API | status | evidence | upgrade risk |
| --- | --- | --- | --- | --- | --- |
| agentgateway Kubernetes controller and data plane | v1.3.1 | `agentgateway.dev/v1alpha1` resources, Gateway API-backed controller reconciliation, AgentgatewayBackend for chat routes | Delivered baseline | Delivered artifact evidence; article evidence; [agentgateway v1.3.1 release](https://github.com/agentgateway/agentgateway/releases/tag/v1.3.1) | Must preserve tested behavior. Do not upgrade in-place without compatibility tests. |
| agentgateway compatibility track | v1.4.0 | Newer Kubernetes agentgateway APIs including experimental AgentgatewayModel, hashed virtual keys, and Gateway API v1.6 support | Validation target only; not supported yet | [agentgateway v1.4.0 release](https://github.com/agentgateway/agentgateway/releases/tag/v1.4.0) | Breaking Gateway API dependency change, new API behavior, hashed-key migration, telemetry and policy behavior changes. |
| Gateway API | v1.5.0 experimental | `gateway.networking.k8s.io/v1` GatewayClass, Gateway, HTTPRoute; ReferenceGrant for explicit cross-namespace trust | Delivered baseline | Delivered artifact evidence; [Gateway API v1.5.0 release](https://github.com/kubernetes-sigs/gateway-api/releases/tag/v1.5.0) | Experimental channel has compatibility risk; upgrade admission policies and CRD size/apply behavior require explicit planning. |
| Gateway API compatibility track | v1.6.x, not delivered | Gateway API version expected by agentgateway v1.4.0 | Validation target only; not supported yet | Official agentgateway v1.4.0 release notes state Gateway API v1.6 support | Must reapply matching CRDs and retest Gateway, HTTPRoute, ReferenceGrant, and controller reconciliation. |
| OpenAI-compatible chat NIM Services | External model runtime; version not owned here | `/v1/chat/completions`-style API through AgentgatewayBackend | Supported by delivered baseline when NIM Service is already healthy | Article evidence; source workflow evidence | Model runtime remains separate. Retest if NIM API shape or agentgateway provider behavior changes. |
| Embedding NIM Services | External model runtime; version not owned here | `/v1/embeddings`-style API routed through gateway to Kubernetes Service backend | Supported by delivered baseline through direct Service routing, not chat AgentgatewayBackend | Article evidence; source workflow evidence | Must retest on agentgateway upgrades. Do not assume chat provider semantics apply to embeddings. |
| Existing NGINX edge | External existing edge; version not owned here | HTTP/TLS host routing to internal gateway Service | Supported integration pattern, not a required component | Article evidence; source workflow evidence | Cutover and rollback depend on correct edge backups. Greenfield environments may omit NGINX. |
| Greenfield exposure layer | Not delivered | Approved load balancer or ingress exposure to generated data plane | Design option only; not implemented | Article evidence | DNS, TLS, firewall, and Service exposure must be designed declaratively before support is claimed. |
| Generated gateway data-plane Deployment and Service | Generated from agentgateway v1.3.1 inputs | Runtime proxy resources produced by the controller | Inspected operational state; never source of truth | Article evidence; source workflow evidence | Manual patches create drift and may be overwritten by reconciliation. |
| Envoy rate-limit service | Delivered as a source-side image snapshot, not copied | Remote rate-limit descriptor evaluation | Baseline dependency pattern; production pinning required | Delivered artifact evidence; source workflow evidence | Mutable image tags and simple deployment topology are not sufficient for larger SLOs. |
| Redis counter store | Source-side Redis 7 Alpine image archive, not copied | Counter storage for rate-limit service | Baseline dependency pattern; production HA not claimed | Delivered artifact evidence; article evidence | Single Redis deployment may not meet availability or durability requirements. |
| Kubernetes cluster | Existing cluster; version not captured in public evidence | CRDs, Services, RBAC, NetworkPolicies, Secrets, Gateway API resources | Environmental prerequisite, not version-supported by this repository yet | Source workflow evidence | Cluster version, admission policy, and controller compatibility must be tested in disposable kind before claims. |
| Helm charts | agentgateway chart packages v1.3.1 in source archive, excluded from Git | Chart rendering/install input | Evidence only until rewritten as clean declarative source | Delivered artifact evidence | Chart archives stay outside Git. Rendered output is inspectable, not permanent source of truth. |

## Support rule

The repository supports only what it can test from clean source.

For now:

- v1.3.1 is the delivered baseline to preserve.
- v1.4.0 is a separate validation target.
- Current or newer upstream versions are not supported unless this repository adds and passes tests for them.
