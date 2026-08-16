# Security Model

The gateway sits between internal applications and model APIs, which means its
security model has to be written down rather than assumed. It owns workload
identity, per-model authorization, rate-limit metadata, observability, and the
handling of runtime credentials. What it must not become is a convenient place to
hide unclear ownership behind a proxy.

![Request evaluation through routing, authentication, authorization, rate limiting, and backend dispatch](assets/diagrams/rendered/policy-decision-flow.svg)

## Scope

In scope:

- Gateway-facing API identity.
- Per-model authorization.
- Runtime secret boundaries.
- Air-gap artifact integrity.
- Rate-limit state.
- Observability and redaction.
- Safe discovery, backup, cleanup, and context verification.

Out of scope here:

- Direct production cluster operations.
- Real secrets.
- Production identity-provider integration.
- Browser frontend implementation.
- NVIDIA NIM model hardening beyond the gateway integration boundary.

## Assets

| Asset | Why it matters | Protection requirement |
| --- | --- | --- |
| Consumer credentials | Authenticate application workloads at the gateway | Never store in Git; encrypt at rest in cluster; rotate; redact from logs |
| Consumer metadata | Drives authorization, rate limits, and observability | Treat as policy source; review changes; avoid accidental permission expansion |
| Gateway routes and policies | Decide which model each consumer can reach | Declarative source of truth; default-deny model onboarding |
| Generated gateway data plane | Enforces the runtime policy path | Inspect health; make durable changes through authored inputs |
| agentgateway controller | Reconciles desired state into runtime proxy resources | Least-privilege RBAC; controlled upgrades |
| NIM Services | Serve model inference and embedding requests | Verify before gateway onboarding; keep model lifecycle separate |
| Rate-limit state | Prevents one consumer from exhausting platform capacity | Network isolation; availability design matching SLO |
| Air-gap artifacts | Supply all offline dependencies | Checksums, inventory, private registry import, immutable image references |
| Logs and metrics | Support audit and troubleshooting | Redact credentials and sensitive payloads where required; bound label cardinality |
| Kubeconfig and operator context | Controls cluster mutation authority | Never commit; verify context before any cluster call |

## Trust boundaries

| Boundary | Crossing | Required control |
| --- | --- | --- |
| Client to edge | External or internal application enters platform network | TLS, firewalling, approved edge path |
| Edge to gateway | Retained NGINX or greenfield exposure forwards to agentgateway | Explicit backend target, preserved Host behavior, rollback-ready configuration |
| Gateway to NIM Services | Policy-enforced request reaches model Service | Route-specific authorization and NetworkPolicies |
| Gateway to rate-limit service | Gateway requests quota decisions | NetworkPolicies and fail-safe policy design |
| Controller to Kubernetes API | Controller reconciles custom resources | Least-privilege RBAC and version-pinned CRDs |
| Operator workstation to cluster | Human or automation applies source-controlled changes | Explicit context verification and reviewed apply path |
| Connected side to air-gap side | Artifacts move into disconnected environment | Checksums, inventory, private registry mapping, no secret material in bundle |
| Runtime secret store to gateway | Credentials become available to policy | External secret integration or Kubernetes Secret with encryption and RBAC |

## Threat actors

- External client attempting unauthenticated access.
- Authorized application calling a model outside its entitlement.
- Compromised application leaking or abusing its gateway credential.
- Internal operator applying a partial or wrong policy manifest.
- Developer accidentally committing runtime credentials or environment-specific
  identifiers.
- Supply-chain actor replacing an air-gap image, chart, or CRD payload.
- Workload in the cluster attempting lateral movement to NIM or rate-limit
  services.
- Browser user extracting a long-lived key from frontend JavaScript.

## Abuse cases and mitigations

| Abuse case | Impact | Mitigation |
| --- | --- | --- |
| Missing or invalid API key | Unauthorized use of model API | Strict authentication; 401 response; no anonymous production model routes |
| Known key calls unauthorized model | Permission bypass attempt | Per-route authorization; default-deny model onboarding; 403 response |
| One shared API key for many apps | No attribution; wide blast radius | One stable consumer identity per workload |
| Key copied into browser JavaScript | Public credential exposure | Browser calls the application backend; backend stores runtime key outside frontend code |
| Raw key leaked through logs | Credential compromise | Log redaction; never echo runtime secrets; scanner blocks key-like material |
| Partial consumer state applied | Existing consumers deleted or permissions changed accidentally | Back up state first; generate full intended state; review diffs; test allowed and denied paths |
| Generated data-plane Deployment patched manually | Drift; reconciliation overwrite; non-reproducible state | Change authored inputs instead of editing reconciled output |
| Wrong cluster context | Real environment changed by mistake | Print and verify context before any cluster-changing command; fail closed on mismatch |
| Air-gap artifact replaced | Untrusted runtime code | SHA-256 inventory; private registry import; immutable tags or digests |
| Cross-namespace backend target added casually | Unauthorized namespace trust | Keep same namespace by default; require explicit ReferenceGrant for cross-namespace routing |
| Rate-limit service unavailable or bypassed | Quota policy fails or traffic outage | Choose the fail behavior explicitly; isolate with NetworkPolicies; design HA if the SLO requires it |
| NIM admin or health endpoint exposed | Expanded attack surface | Publish only intended inference APIs; keep operational endpoints internal |

## Required security controls

### Secret encryption at rest

If Kubernetes Secrets hold the credentials, the cluster must have Secret
encryption at rest enabled. This matters more than usual on the tested v1.3.1
raw-key path.

### Least-privilege RBAC

RBAC has to restrict:

- Who can read or update runtime credential objects.
- Who can modify Gateway, HTTPRoute, and agentgateway policy resources.
- What the controller service account can read and write.
- What CI or operator automation can apply.

### NetworkPolicies

NetworkPolicies should restrict:

- Edge-to-gateway traffic to approved ingress paths.
- Gateway-to-NIM traffic to intended Services and ports.
- Gateway-to-rate-limit-service traffic.
- Rate-limit-service-to-Redis traffic.
- Default east-west access from unrelated namespaces.

### Key rotation

Rotation has to support overlap:

1. Add a new credential for the same consumer identity.
2. Deploy the application with the new key.
3. Verify traffic with the new key.
4. Remove or disable the old key.

Disabling a consumer is not the same as revoking a key. Disabling keeps the audit
record and blocks model access. Revocation removes or invalidates the credential
material itself.

### Log redaction

Normal logs and generated reports must not contain:

- API keys.
- Secret values.
- kubeconfig material.
- environment-specific domains or registry names.
- raw Authorization headers.

Scripts and tests print object names and status. They do not print payloads.

### External secret integrations

Production integrations should pull from an approved secret manager through a
controller such as External Secrets Operator, the Secrets Store CSI Driver, a
sealed-secret workflow, or whatever the organization already standardizes on.

The repository holds:

- Secret names.
- Label contracts.
- Required metadata schema.
- External-secret wiring examples with fake values only.

It does not hold runtime secret values.

## Fail-closed operations

### Discovery

Discovery fails closed when inputs are missing, ambiguous, or inconsistent, and
it produces a plan or report before anything applies. An operator resolves an
ambiguous choice explicitly rather than letting automation guess at a target.

### Backup

Before cutover, consumer-state changes, route changes, or cleanup, automation
captures a recoverable backup of the relevant existing state. Those backups are
operational artifacts and carry sensitive environment data, so handle them
accordingly.

### Cleanup

Cleanup checks whether traffic still depends on the gateway before it deletes
anything. Removing a policy is also the wrong way to remove one consumer's
access; change the consumer state instead.

### Context verification

Any cluster-changing workflow in this project has to:

- print the current Kubernetes context;
- verify it matches the explicitly configured expected context;
- refuse to continue when the expected context is missing or does not match.

## Default-deny model onboarding

Adding a model must not grant access to existing consumers automatically.

Required behavior:

- Verify the NIM Service works before routing through the gateway.
- Add route and backend resources for the model.
- Keep existing consumers denied by default.
- Grant only selected consumers, explicitly.
- Add rate-limit entries explicitly.
- Test allowed, denied, unknown, and rate-limited paths.

## Browser frontend rule

Long-lived model or gateway API keys do not go into public browser JavaScript.

The pattern:

```text
browser
  -> application backend with user session
  -> gateway using backend-held application credential
  -> model
```

If direct browser access is ever genuinely required, it needs a short-lived token
design and its own threat review. That is not part of the delivered baseline.

## Important v1.3.1 security note

The tested agentgateway v1.3.1 path stores API keys as raw runtime credentials in
Kubernetes Secret objects. That is a real constraint, and it should be treated as
one rather than papered over.

Minimum controls while it applies:

- Kubernetes Secret encryption at rest.
- Strict RBAC on credential objects.
- No committed Secret manifests containing values.
- No key values in generated reports.
- Regular rotation.
- External secret integration for production.

## Upgrade note: hashed keys in v1.4.0

agentgateway v1.4.0 advertises virtual keys sourced from ConfigMaps and API keys
stored as SHA-256 hashes. That is a genuine security improvement and a target
worth aiming at, but it is not a baseline change until it is tested here.

v1.4.0 stays on a separate compatibility track until tests prove:

- hashed-key authentication works for the intended routes;
- metadata still drives authorization, rate limits, and observability;
- Gateway API version changes are handled;
- rollback still works;
- docs and examples no longer imply raw-key storage.

## Residual risks

- API keys are bearer credentials. A stolen key works until it is disabled,
  revoked, or expired.
- A single Redis and a single rate-limit Deployment will not meet
  high-availability requirements.
- Gateway-level policy does not replace model-level capacity management.
- Cross-namespace routing is only safe with explicit trust objects and clear
  ownership rules.
- Air-gap integrity depends on disciplined artifact inventory and a well-run
  private registry.
