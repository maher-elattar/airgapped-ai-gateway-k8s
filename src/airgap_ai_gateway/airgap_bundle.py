"""Two-sided air-gap bundle planning, verification, and promotion helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from airgap_ai_gateway.errors import BundleError
from airgap_ai_gateway.manifest import OVERLAYS, build_overlay, validate_documents
from airgap_ai_gateway.yaml_io import load_yaml_file

DEFAULT_LOCK_PATH = Path("airgap/sources.lock.yaml")
DEFAULT_DIST_DIR = Path("dist/airgap-bundles")
DEFAULT_COMPATIBILITY_SET = "baseline-v1.3.1"
DEFAULT_PRIVATE_REGISTRY = "registry.example.internal:5000"
DEFAULT_PROMOTION_TOOL = "skopeo"
DEFAULT_RENDERED_OVERLAY = "production-reference"

IMAGE_ARTIFACT_TYPES = frozenset({"oci-image"})
VALID_ARTIFACT_TYPES = frozenset(
    {
        "helm-chart",
        "kubernetes-crds",
        "oci-image",
        "python-wheel",
        "tool-archive",
        "tool-source",
    }
)
REQUIRED_BASELINE_ENTRIES = frozenset(
    {
        "agentgateway",
        "agentgateway-controller",
        "agentgateway-controller-chart",
        "agentgateway-crds-chart",
        "envoy-ratelimit",
        "fixture-agnhost",
        "fixture-registry",
        "gateway-api-crds",
        "helm",
        "kubeconform",
        "kustomize",
        "python-pyyaml-wheel",
        "redis",
        "skopeo",
    }
)
PUBLIC_IMAGE_PREFIXES = (
    "cr.agentgateway.dev/",
    "docker.io/",
    "ghcr.io/",
    "quay.io/",
    "registry.k8s.io/",
)
MUTABLE_TAGS = frozenset({"latest", "main", "master"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OCI_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")

JsonDict = dict[str, object]


@dataclass(frozen=True, slots=True)
class LockEntry:
    """One immutable source artifact in an air-gap compatibility set."""

    name: str
    artifact_type: str
    version: str
    compatibility_set: str
    canonical_source: str
    destination_name: str
    sha256: str | None
    oci_digest: str | None
    license: str
    provenance: str

    @property
    def content_hash(self) -> str:
        """Return the hash field used to prove this source artifact."""

        if self.oci_digest is not None:
            return self.oci_digest
        if self.sha256 is not None:
            return self.sha256
        msg = f"lock entry {self.name} has no content hash"
        raise BundleError(msg)

    @property
    def is_image(self) -> bool:
        """Return whether this entry represents an OCI image."""

        return self.artifact_type in IMAGE_ARTIFACT_TYPES

    def to_inventory(self) -> JsonDict:
        """Return the public logical inventory shape for this entry."""

        payload: JsonDict = {
            "artifactType": self.artifact_type,
            "canonicalSource": self.canonical_source,
            "compatibilitySet": self.compatibility_set,
            "destinationName": self.destination_name,
            "license": self.license,
            "name": self.name,
            "provenance": self.provenance,
            "version": self.version,
        }
        if self.sha256 is not None:
            payload["sha256"] = self.sha256
        if self.oci_digest is not None:
            payload["ociDigest"] = self.oci_digest
        return payload


@dataclass(frozen=True, slots=True)
class SourceLock:
    """Parsed source lock for all known compatibility sets."""

    name: str
    compatibility_sets: tuple[str, ...]
    entries: tuple[LockEntry, ...]

    def entries_for(self, compatibility_set: str) -> tuple[LockEntry, ...]:
        """Return lock entries for one compatibility set in deterministic order."""

        return tuple(
            sorted(
                (entry for entry in self.entries if entry.compatibility_set == compatibility_set),
                key=lambda item: item.name,
            )
        )


@dataclass(frozen=True, slots=True)
class SplitPart:
    """Checksum metadata for one transfer-media part."""

    path: str
    sha256: str
    size: int


def load_source_lock(path: Path = DEFAULT_LOCK_PATH) -> SourceLock:
    """Load and parse an air-gap source lock."""

    raw = load_yaml_file(path)
    metadata = _mapping(raw.get("metadata"), "metadata")
    spec = _mapping(raw.get("spec"), "spec")
    sets = tuple(
        _string(_mapping(item, "spec.compatibilitySets[]").get("name"), "compatibilitySet.name")
        for item in _sequence(spec.get("compatibilitySets"), "spec.compatibilitySets")
    )
    entries = tuple(
        _parse_lock_entry(item) for item in _sequence(spec.get("entries"), "spec.entries")
    )
    return SourceLock(
        name=_string(metadata.get("name"), "metadata.name"),
        compatibility_sets=sets,
        entries=entries,
    )


def validate_source_lock(
    lock: SourceLock,
    *,
    compatibility_set: str = DEFAULT_COMPATIBILITY_SET,
    private_registry: str = DEFAULT_PRIVATE_REGISTRY,
) -> None:
    """Validate lock completeness, uniqueness, and immutability."""

    errors: list[str] = []
    if compatibility_set not in lock.compatibility_sets:
        errors.append(f"unknown compatibility set: {compatibility_set}")

    entries = lock.entries_for(compatibility_set)
    names = [entry.name for entry in entries]
    destinations = [entry.destination_name for entry in entries]
    errors.extend(_duplicate_errors("lock entry names", names))
    errors.extend(_duplicate_errors("destination names", destinations))

    if compatibility_set == DEFAULT_COMPATIBILITY_SET:
        missing = sorted(REQUIRED_BASELINE_ENTRIES - set(names))
        if missing:
            errors.append(f"missing required baseline lock entries: {', '.join(missing)}")

    for entry in entries:
        errors.extend(_validate_entry(entry, private_registry=private_registry))

    if errors:
        raise BundleError("\n".join(f"- {error}" for error in errors))


def build_bundle(
    *,
    lock_path: Path = DEFAULT_LOCK_PATH,
    compatibility_set: str = DEFAULT_COMPATIBILITY_SET,
    output_dir: Path = DEFAULT_DIST_DIR,
    private_registry: str = DEFAULT_PRIVATE_REGISTRY,
    split_size_bytes: int | None = None,
    metadata_hooks: Sequence[str] = (),
) -> JsonDict:
    """Build deterministic connected-side bundle audit artifacts."""

    lock = load_source_lock(lock_path)
    validate_source_lock(
        lock,
        compatibility_set=compatibility_set,
        private_registry=private_registry,
    )
    bundle_dir = output_dir / compatibility_set
    _clear_previous_bundle_files(bundle_dir)
    payload_dir = bundle_dir / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)

    entries = lock.entries_for(compatibility_set)
    artifacts: list[JsonDict] = []
    for entry in entries:
        relative_path = Path("payloads") / f"{_safe_file_name(entry.name)}.json"
        payload_path = bundle_dir / relative_path
        _write_json(payload_path, _payload_descriptor(entry))
        artifacts.append(
            {
                "artifactType": entry.artifact_type,
                "destinationName": entry.destination_name,
                "lockHash": entry.content_hash,
                "name": entry.name,
                "path": relative_path.as_posix(),
                "sha256": _sha256_file(payload_path),
            }
        )

    logical_inventory = _logical_inventory(lock, compatibility_set)
    inventory: JsonDict = {
        "apiVersion": "airgap.ai.gateway/v1alpha1",
        "artifacts": sorted(artifacts, key=lambda item: str(item["name"])),
        "compatibilitySet": compatibility_set,
        "kind": "AirgapBundleInventory",
        "lockName": lock.name,
        "logicalInventory": logical_inventory,
        "logicalInventoryDigest": _json_sha256(logical_inventory),
        "metadataHooks": [
            {
                "name": hook,
                "status": "declared",
            }
            for hook in metadata_hooks
        ],
        "networkRequiredForVerification": False,
        "promotedImages": _image_map_from_lock(lock, compatibility_set, private_registry),
    }
    inventory_path = bundle_dir / "inventory.json"
    _write_json(inventory_path, inventory)
    checksums = _write_checksum_file(bundle_dir, inventory_path, artifacts)

    blob_path = bundle_dir / "bundle.blob"
    _write_bundle_blob(blob_path, bundle_dir, artifacts)
    split_metadata: JsonDict | None = None
    if split_size_bytes is not None:
        split_metadata = split_file(blob_path, part_size_bytes=split_size_bytes)

    report: JsonDict = {
        "artifactCount": len(artifacts),
        "bundleDir": str(bundle_dir),
        "bundleSha256": _sha256_file(blob_path),
        "checksums": str(checksums),
        "compatibilitySet": compatibility_set,
        "inventory": str(inventory_path),
        "logicalInventoryDigest": str(inventory["logicalInventoryDigest"]),
        "metadataHooks": list(metadata_hooks),
        "networkRequiredForVerification": False,
        "status": "built",
    }
    if split_metadata is not None:
        report["split"] = split_metadata
    _write_json(bundle_dir / "build-report.json", report)
    return report


def verify_bundle(
    *,
    bundle_dir: Path,
    lock_path: Path = DEFAULT_LOCK_PATH,
    compatibility_set: str = DEFAULT_COMPATIBILITY_SET,
    private_registry: str = DEFAULT_PRIVATE_REGISTRY,
    write_report: bool = True,
) -> JsonDict:
    """Verify a disconnected-side bundle without network access."""

    lock = load_source_lock(lock_path)
    validate_source_lock(
        lock,
        compatibility_set=compatibility_set,
        private_registry=private_registry,
    )
    inventory_path = bundle_dir / "inventory.json"
    inventory = _load_json(inventory_path)
    errors: list[str] = []

    if inventory.get("compatibilitySet") != compatibility_set:
        errors.append(
            "inventory compatibility set mismatch: "
            f"{inventory.get('compatibilitySet')!r} != {compatibility_set!r}"
        )
    expected_logical = _logical_inventory(lock, compatibility_set)
    expected_digest = _json_sha256(expected_logical)
    if inventory.get("logicalInventoryDigest") != expected_digest:
        errors.append("inventory logical digest does not match sources lock")
    if inventory.get("logicalInventory") != expected_logical:
        errors.append("inventory logical content does not match sources lock")

    expected_entries = {entry.name: entry for entry in lock.entries_for(compatibility_set)}
    actual_artifacts = _json_sequence(inventory.get("artifacts"), "inventory.artifacts")
    actual_names = {
        _json_string(_json_mapping(item, "inventory.artifacts[]").get("name"), "artifact.name")
        for item in actual_artifacts
    }
    missing = sorted(set(expected_entries) - actual_names)
    extra = sorted(actual_names - set(expected_entries))
    if missing:
        errors.append(f"inventory missing artifacts: {', '.join(missing)}")
    if extra:
        errors.append(f"inventory has unexpected artifacts: {', '.join(extra)}")

    checksum_expectations: dict[str, str] = {
        "inventory.json": _sha256_file(inventory_path),
    }
    bundle_root = bundle_dir.resolve()
    for item in actual_artifacts:
        artifact = _json_mapping(item, "inventory.artifacts[]")
        name = _json_string(artifact.get("name"), "artifact.name")
        entry = expected_entries.get(name)
        if entry is None:
            continue
        if artifact.get("lockHash") != entry.content_hash:
            errors.append(f"artifact {name} lock hash mismatch")
        if artifact.get("destinationName") != entry.destination_name:
            errors.append(f"artifact {name} destination mismatch")
        relative = _json_string(artifact.get("path"), "artifact.path")
        payload_path = _safe_relative_path(bundle_root, relative)
        if not payload_path.exists():
            errors.append(f"artifact {name} payload missing: {relative}")
            continue
        actual_sha = _sha256_file(payload_path)
        checksum_expectations[relative] = actual_sha
        if artifact.get("sha256") != actual_sha:
            errors.append(f"artifact {name} payload checksum mismatch")

    errors.extend(_verify_checksum_file(bundle_dir / "checksums.sha256", checksum_expectations))
    parts_path = bundle_dir / "parts" / "parts.json"
    if parts_path.exists():
        errors.extend(_verify_parts(parts_path, bundle_root))

    if errors:
        raise BundleError("\n".join(f"- {error}" for error in errors))

    report: JsonDict = {
        "artifactCount": len(actual_artifacts),
        "bundleDir": str(bundle_dir),
        "compatibilitySet": compatibility_set,
        "logicalInventoryDigest": expected_digest,
        "networkRequests": 0,
        "status": "verified",
    }
    if write_report:
        _write_json(bundle_dir / "verification.json", report)
    return report


def build_registry_promotion_plan(
    lock: SourceLock,
    *,
    compatibility_set: str = DEFAULT_COMPATIBILITY_SET,
    private_registry: str = DEFAULT_PRIVATE_REGISTRY,
    check_existing: bool = True,
    tool: str = DEFAULT_PROMOTION_TOOL,
    output_file: Path | None = None,
) -> JsonDict:
    """Create an exact retag and promotion plan without moving images."""

    validate_source_lock(
        lock,
        compatibility_set=compatibility_set,
        private_registry=private_registry,
    )
    actions: list[JsonDict] = []
    for entry in lock.entries_for(compatibility_set):
        if not entry.is_image:
            continue
        destination = _destination_for_registry(entry.destination_name, private_registry)
        action: JsonDict = {
            "destination": destination,
            "dockerFallback": [
                ["docker", "pull", entry.canonical_source],
                ["docker", "tag", entry.canonical_source, destination],
                ["docker", "push", destination],
            ],
            "name": entry.name,
            "source": entry.canonical_source,
            "tool": tool,
        }
        if tool == "skopeo":
            action["copyCommand"] = [
                "skopeo",
                "copy",
                f"docker://{entry.canonical_source}",
                f"docker://{destination}",
            ]
            action["existenceCheck"] = ["skopeo", "inspect", f"docker://{destination}"]
        else:
            action["copyCommand"] = [
                "docker",
                "image",
                "tag",
                entry.canonical_source,
                destination,
            ]
            action["existenceCheck"] = ["docker", "manifest", "inspect", destination]
        action["checkExistingBeforePush"] = check_existing
        actions.append(action)

    plan: JsonDict = {
        "actionCount": len(actions),
        "actions": sorted(actions, key=lambda item: str(item["name"])),
        "compatibilitySet": compatibility_set,
        "privateRegistry": private_registry,
        "status": "planned",
    }
    if output_file is not None:
        _write_json(output_file, plan)
    return plan


def verify_rendered_manifests_against_lock(
    *,
    lock_path: Path = DEFAULT_LOCK_PATH,
    compatibility_set: str = DEFAULT_COMPATIBILITY_SET,
    overlay: str = DEFAULT_RENDERED_OVERLAY,
    private_registry: str = DEFAULT_PRIVATE_REGISTRY,
) -> JsonDict:
    """Verify that a rendered overlay uses only promoted image references."""

    if overlay not in OVERLAYS:
        msg = f"unknown overlay {overlay}; expected one of {', '.join(OVERLAYS)}"
        raise BundleError(msg)
    lock = load_source_lock(lock_path)
    validate_source_lock(
        lock,
        compatibility_set=compatibility_set,
        private_registry=private_registry,
    )
    documents = build_overlay(overlay)
    image_map = _image_map_from_lock(lock, compatibility_set, private_registry)
    errors = validate_documents(documents, overlay=overlay, image_map=image_map)
    if errors:
        raise BundleError("\n".join(f"- {error}" for error in errors))
    return {
        "compatibilitySet": compatibility_set,
        "imageCount": len(image_map),
        "overlay": overlay,
        "privateRegistry": private_registry,
        "status": "rendered-manifests-verified",
    }


def split_file(path: Path, *, part_size_bytes: int) -> JsonDict:
    """Split a bundle file into checksumed transfer-media parts."""

    if part_size_bytes <= 0:
        msg = "part_size_bytes must be greater than zero"
        raise BundleError(msg)
    parts_dir = path.parent / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    content = path.read_bytes()
    parts: list[SplitPart] = []
    for index, offset in enumerate(range(0, len(content), part_size_bytes), start=1):
        part = content[offset : offset + part_size_bytes]
        part_path = parts_dir / f"{path.name}.part{index:04d}"
        part_path.write_bytes(part)
        parts.append(
            SplitPart(
                path=(Path("parts") / part_path.name).as_posix(),
                sha256=hashlib.sha256(part).hexdigest(),
                size=len(part),
            )
        )
    metadata: JsonDict = {
        "completePath": path.name,
        "completeSha256": _sha256_file(path),
        "partSizeBytes": part_size_bytes,
        "parts": [asdict(part) for part in parts],
        "status": "split",
    }
    _write_json(parts_dir / "parts.json", metadata)
    return metadata


def reassemble_parts(*, parts_metadata: Path, output_path: Path) -> JsonDict:
    """Reassemble transfer-media parts and verify the complete checksum."""

    metadata = _load_json(parts_metadata)
    bundle_root = parts_metadata.parent.parent.resolve()
    parts = _json_sequence(metadata.get("parts"), "parts")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        for item in parts:
            part = _json_mapping(item, "parts[]")
            relative = _json_string(part.get("path"), "part.path")
            part_path = _safe_relative_path(bundle_root, relative)
            data = part_path.read_bytes()
            expected = _json_string(part.get("sha256"), "part.sha256")
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                msg = f"part checksum mismatch for {relative}"
                raise BundleError(msg)
            handle.write(data)
    complete = _sha256_file(output_path)
    expected_complete = _json_string(metadata.get("completeSha256"), "completeSha256")
    if complete != expected_complete:
        msg = "reassembled bundle checksum mismatch"
        raise BundleError(msg)
    return {
        "outputPath": str(output_path),
        "sha256": complete,
        "status": "reassembled",
    }


def _parse_lock_entry(raw: object) -> LockEntry:
    item = _mapping(raw, "spec.entries[]")
    sha256 = _optional_string(item.get("sha256"), "entry.sha256")
    oci_digest = _optional_string(item.get("ociDigest"), "entry.ociDigest")
    return LockEntry(
        name=_string(item.get("name"), "entry.name"),
        artifact_type=_string(item.get("artifactType"), "entry.artifactType"),
        version=_string(item.get("version"), "entry.version"),
        compatibility_set=_string(item.get("compatibilitySet"), "entry.compatibilitySet"),
        canonical_source=_string(item.get("canonicalSource"), "entry.canonicalSource"),
        destination_name=_string(item.get("destinationName"), "entry.destinationName"),
        sha256=sha256,
        oci_digest=oci_digest,
        license=_string(item.get("license"), "entry.license"),
        provenance=_string(item.get("provenance"), "entry.provenance"),
    )


def _validate_entry(entry: LockEntry, *, private_registry: str) -> list[str]:
    errors: list[str] = []
    if entry.artifact_type not in VALID_ARTIFACT_TYPES:
        errors.append(f"entry {entry.name} has unsupported artifactType {entry.artifact_type}")
    if entry.sha256 is None and entry.oci_digest is None:
        errors.append(f"entry {entry.name} must include sha256 or ociDigest")
    if entry.sha256 is not None and SHA256_RE.fullmatch(entry.sha256) is None:
        errors.append(f"entry {entry.name} has invalid sha256")
    if entry.oci_digest is not None and OCI_DIGEST_RE.fullmatch(entry.oci_digest) is None:
        errors.append(f"entry {entry.name} has invalid ociDigest")
    if not entry.license.strip() or not entry.provenance.strip():
        errors.append(f"entry {entry.name} must include license and provenance notes")

    if entry.is_image:
        if entry.oci_digest is None:
            errors.append(f"image entry {entry.name} must include ociDigest")
        if "@sha256:" not in entry.canonical_source:
            errors.append(f"image entry {entry.name} source must be digest pinned")
        if "@sha256:" not in entry.destination_name:
            errors.append(f"image entry {entry.name} destination must be digest pinned")
        if _image_has_mutable_tag(entry.canonical_source):
            errors.append(f"image entry {entry.name} source uses a mutable tag")
        if _image_has_mutable_tag(entry.destination_name):
            errors.append(f"image entry {entry.name} destination uses a mutable tag")
        expected_prefix = private_registry.rstrip("/") + "/"
        if not entry.destination_name.startswith(expected_prefix):
            errors.append(f"image entry {entry.name} destination must use {private_registry}")
    else:
        destination = Path(entry.destination_name)
        if destination.is_absolute() or ".." in destination.parts:
            errors.append(f"entry {entry.name} destination must stay inside the bundle")
    return errors


def _payload_descriptor(entry: LockEntry) -> JsonDict:
    return {
        "artifact": entry.to_inventory(),
        "bundleArtifactVersion": 1,
        "payloadMode": "descriptor",
        "verification": {
            "networkRequired": False,
            "sourceHash": entry.content_hash,
        },
    }


def _logical_inventory(lock: SourceLock, compatibility_set: str) -> JsonDict:
    entries = [entry.to_inventory() for entry in lock.entries_for(compatibility_set)]
    artifact_types: dict[str, int] = {}
    for entry in lock.entries_for(compatibility_set):
        artifact_types[entry.artifact_type] = artifact_types.get(entry.artifact_type, 0) + 1
    return {
        "artifactTypes": dict(sorted(artifact_types.items())),
        "compatibilitySet": compatibility_set,
        "entries": entries,
        "entryCount": len(entries),
        "lockName": lock.name,
    }


def _image_map_from_lock(
    lock: SourceLock,
    compatibility_set: str,
    private_registry: str,
) -> dict[str, str]:
    images: dict[str, str] = {}
    for entry in lock.entries_for(compatibility_set):
        if entry.is_image:
            images[entry.name] = _destination_for_registry(
                entry.destination_name,
                private_registry,
            )
    return images


def _destination_for_registry(destination: str, private_registry: str) -> str:
    if destination.startswith(private_registry.rstrip("/") + "/"):
        return destination
    remainder = destination.split("/", 1)[1] if "/" in destination else destination
    return f"{private_registry.rstrip('/')}/{remainder}"


def _write_checksum_file(
    bundle_dir: Path,
    inventory_path: Path,
    artifacts: Sequence[JsonDict],
) -> Path:
    checksum_path = bundle_dir / "checksums.sha256"
    lines = [f"{_sha256_file(inventory_path)}  inventory.json\n"]
    for artifact in sorted(artifacts, key=lambda item: str(item["path"])):
        relative = str(artifact["path"])
        lines.append(f"{artifact['sha256']}  {relative}\n")
    checksum_path.write_text("".join(lines), encoding="utf-8")
    return checksum_path


def _clear_previous_bundle_files(bundle_dir: Path) -> None:
    for relative in (
        "bundle.blob",
        "build-report.json",
        "checksums.sha256",
        "inventory.json",
        "verification.json",
    ):
        path = bundle_dir / relative
        if path.exists() and path.is_file():
            path.unlink()
    for directory in (bundle_dir / "payloads", bundle_dir / "parts"):
        if not directory.exists():
            continue
        for child in directory.iterdir():
            if child.is_file():
                child.unlink()


def _verify_checksum_file(path: Path, expectations: dict[str, str]) -> list[str]:
    if not path.exists():
        return ["checksums.sha256 is missing"]
    actual: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            return [f"invalid checksum line: {line}"]
        actual[parts[1].strip()] = parts[0]
    errors: list[str] = []
    for relative, checksum in expectations.items():
        if actual.get(relative) != checksum:
            errors.append(f"checksum file mismatch for {relative}")
    for relative in sorted(set(actual) - set(expectations)):
        errors.append(f"checksum file contains unexpected path {relative}")
    return errors


def _write_bundle_blob(path: Path, bundle_dir: Path, artifacts: Sequence[JsonDict]) -> None:
    with path.open("wb") as handle:
        for artifact in sorted(artifacts, key=lambda item: str(item["path"])):
            relative = str(artifact["path"])
            payload = (bundle_dir / relative).read_bytes()
            handle.write(f"\n--- {relative} ---\n".encode())
            handle.write(payload)


def _verify_parts(parts_path: Path, bundle_root: Path) -> list[str]:
    metadata = _load_json(parts_path)
    errors: list[str] = []
    for item in _json_sequence(metadata.get("parts"), "parts"):
        part = _json_mapping(item, "parts[]")
        relative = _json_string(part.get("path"), "part.path")
        part_path = _safe_relative_path(bundle_root, relative)
        if not part_path.exists():
            errors.append(f"split part missing: {relative}")
            continue
        actual = _sha256_file(part_path)
        if actual != part.get("sha256"):
            errors.append(f"split part checksum mismatch: {relative}")
    complete_path = _json_string(metadata.get("completePath"), "completePath")
    complete = _safe_relative_path(bundle_root, complete_path)
    if complete.exists() and _sha256_file(complete) != metadata.get("completeSha256"):
        errors.append("complete bundle checksum mismatch")
    return errors


def _safe_relative_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        msg = f"path escapes bundle directory: {relative}"
        raise BundleError(msg) from exc
    return path


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: Path) -> JsonDict:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        msg = f"{path} must contain a JSON object"
        raise BundleError(msg)
    return cast(JsonDict, loaded)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(data: object) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _safe_file_name(name: str) -> str:
    return SAFE_NAME_RE.sub("-", name).strip("-")


def _image_has_mutable_tag(reference: str) -> bool:
    image_without_digest = reference.split("@", 1)[0]
    last_segment = image_without_digest.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return False
    tag = last_segment.rsplit(":", 1)[1]
    return tag in MUTABLE_TAGS


def _duplicate_errors(label: str, values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for value in values:
        if value in seen:
            repeated.add(value)
        seen.add(value)
    if not repeated:
        return []
    return [f"duplicate {label}: {', '.join(sorted(repeated))}"]


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"{label} must be a mapping"
        raise BundleError(msg)
    return cast(dict[str, Any], value)


def _sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"{label} must be a list"
        raise BundleError(msg)
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"{label} must be a non-empty string"
        raise BundleError(msg)
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _json_mapping(value: object, label: str) -> JsonDict:
    if not isinstance(value, dict):
        msg = f"{label} must be a JSON object"
        raise BundleError(msg)
    return cast(JsonDict, value)


def _json_sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"{label} must be a JSON list"
        raise BundleError(msg)
    return value


def _json_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        msg = f"{label} must be a JSON string"
        raise BundleError(msg)
    return value
