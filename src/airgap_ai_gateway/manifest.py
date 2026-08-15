"""Kustomize rendering and semantic validation helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

Manifest = dict[str, Any]

OVERLAYS = ("kind-demo", "retained-nginx-edge", "production-reference")
BASELINE_DIR = Path("manifests/baseline-v1.3.1")
IMAGE_MAP = BASELINE_DIR / "images.yaml"

CLUSTER_SCOPED_KINDS = {
    "CustomResourceDefinition",
    "GatewayClass",
    "Namespace",
    "Node",
    "PersistentVolume",
    "StorageClass",
}
INFERENCE_PATHS = {"/v1/chat/completions", "/v1/embeddings"}
PRIVATE_REGISTRY = "registry.example.internal:5000"
PUBLIC_IMAGE_MARKERS = (
    "cr.agentgateway.dev/",
    "docker.io/",
    "ghcr.io/",
    "quay.io/",
    "registry.k8s.io/",
)


def overlay_path(name: str) -> Path:
    """Return the path for a named baseline overlay."""

    if name not in OVERLAYS:
        msg = f"unknown overlay {name}; expected one of {', '.join(OVERLAYS)}"
        raise ValueError(msg)
    return BASELINE_DIR / "overlays" / name


def build_overlay(name: str) -> list[Manifest]:
    """Render an overlay with the small Kustomize subset used by this repository."""

    return build_kustomization(overlay_path(name))


def build_kustomization(path: Path) -> list[Manifest]:
    """Build a kustomization directory without requiring cluster access."""

    return _build_kustomization(path.resolve(), set())


def load_image_map(path: Path = IMAGE_MAP) -> dict[str, str]:
    """Load the structured image map as name-to-target references."""

    raw = _load_single_yaml(path)
    spec = _mapping(raw.get("spec"), "ImageMap.spec")
    images = _sequence(spec.get("images"), "ImageMap.spec.images")
    result: dict[str, str] = {}
    for item in images:
        image = _mapping(item, "ImageMap.spec.images[]")
        name = _string(image.get("name"), "image.name")
        target = _string(image.get("target"), "image.target")
        if not target.startswith(f"{PRIVATE_REGISTRY}/"):
            msg = f"image map target for {name} must use {PRIVATE_REGISTRY}: {target}"
            raise ValueError(msg)
        if "@sha256:" not in target or ":latest" in target:
            msg = f"image map target for {name} must be digest-pinned and immutable: {target}"
            raise ValueError(msg)
        result[name] = target
    return result


def dump_documents(documents: list[Manifest]) -> str:
    """Serialize rendered manifests deterministically."""

    return yaml.safe_dump_all(documents, explicit_start=True, sort_keys=False)


def validate_documents(
    documents: list[Manifest],
    *,
    overlay: str,
    image_map: dict[str, str],
) -> list[str]:
    """Return semantic validation errors for rendered Kubernetes documents."""

    errors: list[str] = []
    errors.extend(_validate_required_shape(documents))
    errors.extend(_validate_no_secret_material(documents))
    errors.extend(_validate_images(documents, image_map))
    errors.extend(_validate_routes_and_policies(documents))
    errors.extend(_validate_backend_types(documents))
    errors.extend(_validate_consumer_id_flow(documents))
    errors.extend(_validate_cross_namespace_references(documents))
    errors.extend(_validate_owned_workload_controls(documents))
    errors.extend(_validate_overlay_contract(documents, overlay))
    errors.extend(_validate_inference_only_paths(documents))
    errors.extend(_validate_no_authored_generated_data_plane(documents))
    return errors


def summarize_documents(documents: list[Manifest], overlay: str) -> Manifest:
    """Create a compact golden-test summary of rendered resources."""

    routes = sorted(
        {
            _metadata(item)["labels"]["ai.gateway/model-key"]: _metadata(item)["name"]
            for item in documents
            if item.get("kind") == "HTTPRoute"
        }.items()
    )
    policies = sorted(
        {
            _metadata(item)["labels"]["ai.gateway/model-key"]: _metadata(item)["name"]
            for item in documents
            if item.get("kind") == "AgentgatewayPolicy"
            and _metadata(item).get("labels", {}).get("app.kubernetes.io/component")
            == "model-authorization-policy"
        }.items()
    )
    images = sorted(_rendered_images(documents).items())
    kinds: dict[str, int] = {}
    for item in documents:
        kind = _string(item.get("kind"), "kind")
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "overlay": overlay,
        "resourceCount": len(documents),
        "kinds": dict(sorted(kinds.items())),
        "routes": [{"model": model, "route": route} for model, route in routes],
        "policies": [{"model": model, "policy": policy} for model, policy in policies],
        "images": [{"name": name, "ref": ref} for name, ref in images],
    }


def _build_kustomization(path: Path, seen: set[Path]) -> list[Manifest]:
    if path in seen:
        msg = f"recursive kustomization reference: {path}"
        raise ValueError(msg)
    seen.add(path)
    kustomization = _load_single_yaml(path / "kustomization.yaml")
    documents: list[Manifest] = []
    for resource in _sequence(kustomization.get("resources", []), "resources"):
        resource_path = (path / _string(resource, "resources[]")).resolve()
        if resource_path.is_dir():
            documents.extend(_build_kustomization(resource_path, seen))
        else:
            documents.extend(_load_yaml_documents(resource_path))
    _apply_labels(documents, kustomization)
    _apply_patches(documents, path, kustomization)
    seen.remove(path)
    return documents


def _apply_labels(documents: list[Manifest], kustomization: Manifest) -> None:
    pairs: dict[str, str] = {}
    for item in _sequence(kustomization.get("labels", []), "labels"):
        label_spec = _mapping(item, "labels[]")
        for key, value in _mapping(label_spec.get("pairs"), "labels[].pairs").items():
            pairs[str(key)] = str(value)
    for key, value in _mapping(kustomization.get("commonLabels", {}), "commonLabels").items():
        pairs[str(key)] = str(value)
    if not pairs:
        return
    for document in documents:
        metadata = _metadata(document)
        labels = metadata.setdefault("labels", {})
        if not isinstance(labels, dict):
            labels = {}
            metadata["labels"] = labels
        labels.update(pairs)


def _apply_patches(documents: list[Manifest], path: Path, kustomization: Manifest) -> None:
    for item in _sequence(kustomization.get("patches", []), "patches"):
        if isinstance(item, str):
            patch_path = path / item
        else:
            patch_path = path / _string(_mapping(item, "patches[]").get("path"), "patches[].path")
        for patch in _load_yaml_documents(patch_path):
            _apply_single_patch(documents, patch)


def _apply_single_patch(documents: list[Manifest], patch: Manifest) -> None:
    target = _resource_id(patch)
    index = next(
        (position for position, item in enumerate(documents) if _resource_id(item) == target), None
    )
    if index is None:
        msg = f"patch target not found: {target}"
        raise ValueError(msg)
    if patch.get("$patch") == "delete":
        del documents[index]
        return
    documents[index] = _deep_merge(documents[index], patch)


def _deep_merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = deepcopy(base)
        for key, value in patch.items():
            if key == "$patch":
                continue
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    if isinstance(base, list) and isinstance(patch, list):
        return _merge_lists(base, patch)
    return deepcopy(patch)


def _merge_lists(base: list[Any], patch: list[Any]) -> list[Any]:
    merged = deepcopy(base)
    for patch_item in patch:
        if not isinstance(patch_item, dict) or "name" not in patch_item:
            return deepcopy(patch)
        match = next(
            (
                position
                for position, base_item in enumerate(merged)
                if isinstance(base_item, dict) and base_item.get("name") == patch_item["name"]
            ),
            None,
        )
        if match is None:
            merged.append(deepcopy(patch_item))
        else:
            merged[match] = _deep_merge(merged[match], patch_item)
    return merged


def _validate_required_shape(documents: list[Manifest]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in documents:
        for field in ("apiVersion", "kind", "metadata"):
            if field not in item:
                errors.append(f"document missing {field}")
        metadata = _metadata(item)
        name = metadata.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{item.get('kind', '<unknown>')} missing metadata.name")
            continue
        identity = _resource_id(item)
        if identity in seen:
            errors.append(f"duplicate rendered resource: {identity}")
        seen.add(identity)
    return errors


def _validate_no_secret_material(documents: list[Manifest]) -> list[str]:
    errors: list[str] = []
    for item in documents:
        if item.get("kind") == "Secret":
            errors.append(f"normal render creates Secret {_metadata(item).get('name')}")
        rendered = yaml.safe_dump(item, sort_keys=False)
        kubeconfig_key = "-".join(("client", "key", "data"))
        kubeconfig_cert = "-".join(("client", "certificate", "data"))
        for marker in ("stringData:", kubeconfig_key, kubeconfig_cert):
            if marker in rendered:
                errors.append(f"rendered manifest contains forbidden secret marker {marker}")
    return errors


def _validate_images(documents: list[Manifest], image_map: dict[str, str]) -> list[str]:
    errors: list[str] = []
    allowed = set(image_map.values())
    for name, image in _rendered_images(documents).items():
        if image not in allowed:
            errors.append(f"image {name} is not sourced from images.yaml: {image}")
        if not image.startswith(f"{PRIVATE_REGISTRY}/"):
            errors.append(f"image {name} does not use the private registry: {image}")
        if "@sha256:" not in image:
            errors.append(f"image {name} is not digest pinned: {image}")
        if ":latest" in image:
            errors.append(f"image {name} uses mutable latest tag: {image}")
        for marker in PUBLIC_IMAGE_MARKERS:
            if marker in image:
                errors.append(f"image {name} contains public registry marker {marker}")
    rendered = dump_documents(documents)
    for marker in PUBLIC_IMAGE_MARKERS:
        if marker in rendered:
            errors.append(f"rendered output contains public image marker {marker}")
    return errors


def _validate_routes_and_policies(documents: list[Manifest]) -> list[str]:
    errors: list[str] = []
    policies_by_route: dict[str, Manifest] = {}
    for item in documents:
        if item.get("kind") != "AgentgatewayPolicy":
            continue
        for target in _sequence(
            _mapping(item.get("spec"), "spec").get("targetRefs", []), "targetRefs"
        ):
            target_ref = _mapping(target, "targetRefs[]")
            if target_ref.get("kind") == "HTTPRoute":
                policies_by_route[_string(target_ref.get("name"), "targetRefs[].name")] = item
    for route in _by_kind(documents, "HTTPRoute"):
        route_name = _metadata(route)["name"]
        policy = policies_by_route.get(route_name)
        if policy is None:
            errors.append(f"HTTPRoute {route_name} has no AgentgatewayPolicy")
            continue
        traffic = _mapping(_mapping(policy.get("spec"), "policy.spec").get("traffic"), "traffic")
        authn = _mapping(traffic.get("apiKeyAuthentication"), "apiKeyAuthentication")
        authz = _mapping(traffic.get("authorization"), "authorization")
        if authn.get("mode") != "Strict":
            errors.append(f"HTTPRoute {route_name} policy does not enforce strict API keys")
        if "secretRef" not in authn:
            errors.append(
                f"HTTPRoute {route_name} policy does not reference runtime Secret contract"
            )
        if authz.get("action") != "Require":
            errors.append(f"HTTPRoute {route_name} policy does not require authorization")
        expressions = _sequence(
            _mapping(authz.get("policy"), "authorization.policy").get("matchExpressions"),
            "matchExpressions",
        )
        if not expressions:
            errors.append(f"HTTPRoute {route_name} policy has no authorization expression")
    return errors


def _validate_backend_types(documents: list[Manifest]) -> list[str]:
    errors: list[str] = []
    backends = {_metadata(item)["name"] for item in _by_kind(documents, "AgentgatewayBackend")}
    services = {_metadata(item)["name"] for item in _by_kind(documents, "Service")}
    for route in _by_kind(documents, "HTTPRoute"):
        metadata = _metadata(route)
        model = _mapping(metadata.get("labels"), "route.labels").get("ai.gateway/model-key")
        api_shape = _mapping(metadata.get("labels"), "route.labels").get("ai.gateway/api-shape")
        backend_refs = _route_backend_refs(route)
        if api_shape == "chat":
            for ref in backend_refs:
                if (
                    ref.get("kind") != "AgentgatewayBackend"
                    or ref.get("group") != "agentgateway.dev"
                ):
                    errors.append(f"chat route {model} must use AgentgatewayBackend")
                if ref.get("name") not in backends:
                    errors.append(f"chat route {model} references missing AgentgatewayBackend")
        if api_shape == "embedding":
            for ref in backend_refs:
                if ref.get("kind") not in (None, "Service") or ref.get("group") not in (None, ""):
                    errors.append(f"embedding route {model} must use direct Service backend")
                if ref.get("name") not in services:
                    errors.append(f"embedding route {model} references missing Service backend")
    return errors


def _validate_consumer_id_flow(documents: list[Manifest]) -> list[str]:
    errors: list[str] = []
    expected_expression = "apiKey.metadata.consumer_id"
    metric_policy = next(
        (
            item
            for item in _by_kind(documents, "AgentgatewayPolicy")
            if _metadata(item).get("name") == "gateway-consumer-metrics"
        ),
        None,
    )
    if metric_policy is None:
        return ["gateway consumer metrics policy is missing"]
    frontend = _mapping(
        _mapping(metric_policy.get("spec"), "metrics.spec").get("frontend"), "frontend"
    )
    metrics = _mapping(frontend.get("metrics"), "metrics")
    attributes = _mapping(metrics.get("attributes"), "metrics.attributes")
    additions = _sequence(attributes.get("add"), "metrics.attributes.add")
    if not any(
        _mapping(item, "metric add").get("name") == "consumer_id"
        and _mapping(item, "metric add").get("expression") == expected_expression
        for item in additions
    ):
        errors.append("gateway metrics do not include consumer_id from API key metadata")
    for policy in _by_kind(documents, "AgentgatewayPolicy"):
        labels = _metadata(policy).get("labels", {})
        if (
            not isinstance(labels, dict)
            or labels.get("app.kubernetes.io/component") != "model-authorization-policy"
        ):
            continue
        global_limit = _mapping(
            _mapping(_mapping(policy.get("spec"), "policy.spec").get("traffic"), "traffic").get(
                "rateLimit"
            ),
            "rateLimit",
        ).get("global")
        descriptors = _sequence(
            _mapping(global_limit, "rateLimit.global").get("descriptors"), "descriptors"
        )
        for descriptor in descriptors:
            entries = _sequence(
                _mapping(descriptor, "descriptor").get("entries"), "descriptor.entries"
            )
            if not any(
                _mapping(entry, "entry").get("name") == "consumer_id"
                and _mapping(entry, "entry").get("expression") == expected_expression
                for entry in entries
            ):
                errors.append(
                    f"policy {_metadata(policy)['name']} lacks consumer_id rate-limit descriptor"
                )
    config = next(
        (
            item
            for item in _by_kind(documents, "ConfigMap")
            if _metadata(item).get("name") == "envoy-ratelimit-config"
        ),
        None,
    )
    if config is None or "key: consumer_id" not in str(
        _mapping(config.get("data"), "data").get("config.yaml", "")
    ):
        errors.append("Envoy rate-limit config does not use consumer_id descriptors")
    return errors


def _validate_cross_namespace_references(documents: list[Manifest]) -> list[str]:
    errors: list[str] = []
    grants = _by_kind(documents, "ReferenceGrant")
    for route in _by_kind(documents, "HTTPRoute"):
        route_namespace = str(_metadata(route).get("namespace", "default"))
        for ref in _route_backend_refs(route):
            target_namespace = str(ref.get("namespace", route_namespace))
            if target_namespace != route_namespace and not grants:
                errors.append(
                    f"HTTPRoute {_metadata(route)['name']} crosses namespace without ReferenceGrant"
                )
    return errors


def _validate_owned_workload_controls(documents: list[Manifest]) -> list[str]:
    errors: list[str] = []
    pdb_names = {_metadata(item)["name"] for item in _by_kind(documents, "PodDisruptionBudget")}
    for deployment in _by_kind(documents, "Deployment"):
        name = _metadata(deployment)["name"]
        if name not in pdb_names:
            errors.append(f"Deployment {name} has no PodDisruptionBudget")
        pod_spec = _mapping(
            _mapping(
                _mapping(deployment.get("spec"), "deployment.spec").get("template"), "template"
            ).get("spec"),
            "pod.spec",
        )
        pod_security = _mapping(pod_spec.get("securityContext"), "pod.securityContext")
        if pod_security.get("runAsNonRoot") is not True:
            errors.append(f"Deployment {name} does not set runAsNonRoot")
        for container in _sequence(pod_spec.get("containers"), "containers"):
            container_spec = _mapping(container, "container")
            container_name = _string(container_spec.get("name"), "container.name")
            if "resources" not in container_spec:
                errors.append(f"container {name}/{container_name} has no resources")
            security = _mapping(container_spec.get("securityContext"), "container.securityContext")
            if security.get("allowPrivilegeEscalation") is not False:
                errors.append(f"container {name}/{container_name} allows privilege escalation")
    for service_account in _by_kind(documents, "ServiceAccount"):
        if service_account.get("automountServiceAccountToken") is not False:
            errors.append(f"ServiceAccount {_metadata(service_account)['name']} automounts tokens")
    if not any(
        _metadata(item).get("name") == "default-deny"
        for item in _by_kind(documents, "NetworkPolicy")
    ):
        errors.append("default-deny NetworkPolicy is missing")
    return errors


def _validate_overlay_contract(documents: list[Manifest], overlay: str) -> list[str]:
    errors: list[str] = []
    if overlay == "kind-demo":
        for item in documents:
            labels = _metadata(item).get("labels", {})
            if not isinstance(labels, dict) or labels.get("ai.gateway/demo-only") != "true":
                errors.append(
                    f"{item.get('kind')}/{_metadata(item).get('name')} is not labeled demo-only"
                )
        for deployment in _by_kind(documents, "Deployment"):
            if _mapping(deployment.get("spec"), "deployment.spec").get("replicas") != 1:
                errors.append(
                    f"kind-demo Deployment {_metadata(deployment)['name']} is not single replica"
                )
    if overlay == "production-reference":
        if any(
            _metadata(item).get("name") == "redis" for item in _by_kind(documents, "Deployment")
        ):
            errors.append("production-reference still renders demo Redis Deployment")
        if not any(
            _metadata(item).get("name") == "external-ha-redis"
            for item in _by_kind(documents, "Service")
        ):
            errors.append("production-reference does not declare external HA Redis Service")
        ratelimit = next(
            (
                item
                for item in _by_kind(documents, "Deployment")
                if _metadata(item)["name"] == "envoy-ratelimit"
            ),
            None,
        )
        if (
            ratelimit is None
            or _mapping(ratelimit.get("spec"), "deployment.spec").get("replicas", 0) < 3
        ):
            errors.append("production-reference ratelimit service is not HA")
    return errors


def _validate_inference_only_paths(documents: list[Manifest]) -> list[str]:
    errors: list[str] = []
    for route in _by_kind(documents, "HTTPRoute"):
        for rule in _sequence(_mapping(route.get("spec"), "route.spec").get("rules"), "rules"):
            for match in _sequence(_mapping(rule, "rule").get("matches"), "matches"):
                path = _mapping(_mapping(match, "match").get("path"), "match.path").get("value")
                if path not in INFERENCE_PATHS:
                    errors.append(
                        f"HTTPRoute {_metadata(route)['name']} exposes non-inference path {path}"
                    )
    return errors


def _validate_no_authored_generated_data_plane(documents: list[Manifest]) -> list[str]:
    errors: list[str] = []
    for deployment in _by_kind(documents, "Deployment"):
        labels = _metadata(deployment).get("labels", {})
        if (
            isinstance(labels, dict)
            and labels.get("app.kubernetes.io/component") == "agentgateway-data-plane"
        ):
            errors.append("generated agentgateway data-plane Deployment is authored in manifests")
    return errors


def _rendered_images(documents: list[Manifest]) -> dict[str, str]:
    images: dict[str, str] = {}
    for item in documents:
        if item.get("kind") == "Deployment":
            pod_spec = _mapping(
                _mapping(
                    _mapping(item.get("spec"), "deployment.spec").get("template"), "template"
                ).get("spec"),
                "pod.spec",
            )
            for container in _sequence(pod_spec.get("containers", []), "containers"):
                container_spec = _mapping(container, "container")
                images[f"{_metadata(item)['name']}/{container_spec['name']}"] = _string(
                    container_spec.get("image"), "container.image"
                )
        if item.get("kind") == "AgentgatewayParameters":
            image = _mapping(_mapping(item.get("spec"), "params.spec").get("image"), "params.image")
            images[f"{_metadata(item)['name']}/agentgateway"] = (
                f"{image['registry']}/{image['repository']}@{image['digest']}"
            )
    return images


def _route_backend_refs(route: Manifest) -> list[Manifest]:
    refs: list[Manifest] = []
    for rule in _sequence(_mapping(route.get("spec"), "route.spec").get("rules"), "rules"):
        refs.extend(
            _mapping(ref, "backendRef")
            for ref in _sequence(_mapping(rule, "rule").get("backendRefs"), "backendRefs")
        )
    return refs


def _by_kind(documents: list[Manifest], kind: str) -> list[Manifest]:
    return [item for item in documents if item.get("kind") == kind]


def _resource_id(item: Manifest) -> tuple[str, str, str, str]:
    metadata = _metadata(item)
    kind = _string(item.get("kind"), "kind")
    namespace = ""
    if kind not in CLUSTER_SCOPED_KINDS:
        namespace = str(metadata.get("namespace", "default"))
    return (
        _string(item.get("apiVersion"), "apiVersion"),
        kind,
        namespace,
        _string(metadata.get("name"), "metadata.name"),
    )


def _metadata(item: Manifest) -> Manifest:
    return _mapping(item.get("metadata"), "metadata")


def _load_single_yaml(path: Path) -> Manifest:
    documents = _load_yaml_documents(path)
    if len(documents) != 1:
        msg = f"expected one YAML document in {path}, found {len(documents)}"
        raise ValueError(msg)
    return documents[0]


def _load_yaml_documents(path: Path) -> list[Manifest]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = [item for item in yaml.safe_load_all(handle) if item is not None]
    return [_mapping(item, str(path)) for item in loaded]


def _mapping(value: object, label: str) -> Manifest:
    if not isinstance(value, dict):
        msg = f"{label} must be a mapping"
        raise ValueError(msg)
    return value


def _sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        msg = f"{label} must be a list"
        raise ValueError(msg)
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"{label} must be a non-empty string"
        raise ValueError(msg)
    return value
