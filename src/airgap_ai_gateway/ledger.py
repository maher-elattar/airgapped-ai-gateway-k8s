"""State ledger and pre-change snapshot models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Self, cast

from airgap_ai_gateway.errors import ExecutionError
from airgap_ai_gateway.manifest import CLUSTER_SCOPED_KINDS, Manifest


class LedgerState(StrEnum):
    """How a resource relates to one approved run."""

    CREATED = "created"
    UPDATED = "updated"
    PRE_EXISTING = "pre-existing"


@dataclass(frozen=True, order=True, slots=True)
class ResourceRef:
    """Stable Kubernetes resource identity."""

    api_version: str
    kind: str
    namespace: str
    name: str

    @property
    def identity(self) -> str:
        """Return a stable string form suitable for ledgers and reports."""

        namespace = self.namespace or "_cluster"
        return f"{self.api_version}/{self.kind}/{namespace}/{self.name}"

    @classmethod
    def from_manifest(cls, manifest: Manifest) -> Self:
        """Build a resource reference from a rendered Kubernetes document."""

        metadata = manifest.get("metadata")
        if not isinstance(metadata, dict):
            msg = "manifest missing metadata"
            raise ExecutionError(msg)
        api_version = manifest.get("apiVersion")
        kind = manifest.get("kind")
        name = metadata.get("name")
        if not isinstance(api_version, str) or not api_version:
            msg = "manifest missing apiVersion"
            raise ExecutionError(msg)
        if not isinstance(kind, str) or not kind:
            msg = "manifest missing kind"
            raise ExecutionError(msg)
        if not isinstance(name, str) or not name:
            msg = "manifest missing apiVersion, kind, or metadata.name"
            raise ExecutionError(msg)
        namespace = ""
        if kind not in CLUSTER_SCOPED_KINDS:
            namespace_value = metadata.get("namespace", "default")
            if not isinstance(namespace_value, str) or not namespace_value:
                msg = f"{kind}/{name} has invalid metadata.namespace"
                raise ExecutionError(msg)
            namespace = namespace_value
        return cls(
            api_version=api_version,
            kind=kind,
            namespace=namespace,
            name=name,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Self:
        """Build a resource reference from JSON data."""

        api_version = _required_string(payload, "apiVersion")
        kind = _required_string(payload, "kind")
        namespace = _string(payload.get("namespace", ""))
        name = _required_string(payload, "name")
        return cls(api_version=api_version, kind=kind, namespace=namespace, name=name)

    def to_dict(self) -> dict[str, str]:
        """Convert the reference to a deterministic JSON object."""

        return {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "name": self.name,
            "namespace": self.namespace,
        }


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One resource recorded by an approved run."""

    ref: ResourceRef
    state: LedgerState
    run_id: str
    before: Manifest | None = None
    after: Manifest | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Self:
        """Build a ledger entry from JSON data."""

        ref_payload = payload.get("ref")
        if not isinstance(ref_payload, dict):
            msg = "ledger entry ref must be an object"
            raise ExecutionError(msg)
        before = payload.get("before")
        after = payload.get("after")
        return cls(
            ref=ResourceRef.from_dict(cast(dict[str, object], ref_payload)),
            state=LedgerState(_required_string(payload, "state")),
            run_id=_required_string(payload, "run_id"),
            before=cast(Manifest, before) if isinstance(before, dict) else None,
            after=cast(Manifest, after) if isinstance(after, dict) else None,
        )

    def to_dict(self) -> dict[str, object]:
        """Convert the entry to a deterministic JSON object."""

        payload: dict[str, object] = {
            "after": self.after,
            "before": self.before,
            "ref": self.ref.to_dict(),
            "run_id": self.run_id,
            "state": self.state.value,
        }
        return payload


@dataclass(frozen=True, slots=True)
class PreChangeSnapshot:
    """Read-only resource state captured before a mutating operation."""

    status: str
    resources: dict[str, Manifest] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def empty_ok(cls) -> Self:
        """Return an explicit successful snapshot with no existing resources."""

        return cls(status="ok", resources={})

    @classmethod
    def from_file(cls, path: Path) -> Self:
        """Load a snapshot from JSON."""

        if not path.exists():
            msg = f"pre-change snapshot does not exist: {path}"
            raise ExecutionError(msg)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            msg = "pre-change snapshot must be a JSON object"
            raise ExecutionError(msg)
        return cls.from_dict(cast(dict[str, object], payload))

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Self:
        """Build a snapshot from JSON data."""

        resources_payload = payload.get("resources", {})
        if not isinstance(resources_payload, dict):
            msg = "snapshot resources must be an object keyed by resource identity"
            raise ExecutionError(msg)
        resources: dict[str, Manifest] = {}
        for key, value in resources_payload.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                msg = "snapshot resource entries must map identity strings to objects"
                raise ExecutionError(msg)
            resources[key] = cast(Manifest, value)
        error = payload.get("error")
        return cls(
            status=_required_string(payload, "status"),
            resources=resources,
            error=_string(error) if error is not None else None,
        )

    def require_ok(self) -> None:
        """Block state-changing operations when the backup did not succeed."""

        if self.status != "ok":
            reason = f": {self.error}" if self.error else ""
            msg = f"pre-change snapshot is not usable{reason}"
            raise ExecutionError(msg)

    def to_dict(self) -> dict[str, object]:
        """Convert the snapshot to deterministic JSON data."""

        return {
            "error": self.error,
            "resources": dict(sorted(self.resources.items())),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class StateLedger:
    """Resource state ledger for one or more approved runs."""

    entries: tuple[LedgerEntry, ...] = ()

    @classmethod
    def from_file(cls, path: Path) -> Self:
        """Load a state ledger from JSON."""

        if not path.exists():
            msg = f"state ledger does not exist: {path}"
            raise ExecutionError(msg)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            msg = "state ledger must be a JSON object"
            raise ExecutionError(msg)
        return cls.from_dict(cast(dict[str, object], payload))

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Self:
        """Build a ledger from JSON data."""

        entries_payload = payload.get("entries", [])
        if not isinstance(entries_payload, list):
            msg = "ledger entries must be a list"
            raise ExecutionError(msg)
        entries: list[LedgerEntry] = []
        for item in entries_payload:
            if not isinstance(item, dict):
                msg = "ledger entries must be objects"
                raise ExecutionError(msg)
            entries.append(LedgerEntry.from_dict(cast(dict[str, object], item)))
        return cls(entries=tuple(entries))

    def for_run(self, run_id: str) -> StateLedger:
        """Return entries created by a specific run."""

        return StateLedger(tuple(entry for entry in self.entries if entry.run_id == run_id))

    def to_dict(self) -> dict[str, object]:
        """Convert the ledger to deterministic JSON data."""

        entries = sorted(self.entries, key=lambda entry: entry.ref.identity)
        return {"entries": [entry.to_dict() for entry in entries]}

    def write(self, path: Path) -> None:
        """Write the ledger to JSON."""

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")


def ledger_from_resources(
    *,
    resources: tuple[ResourceRef, ...],
    snapshot: PreChangeSnapshot,
    run_id: str,
) -> StateLedger:
    """Create ledger entries by comparing planned resources with a snapshot."""

    snapshot.require_ok()
    entries: list[LedgerEntry] = []
    for ref in sorted(resources):
        before = snapshot.resources.get(ref.identity)
        state = LedgerState.UPDATED if before is not None else LedgerState.CREATED
        entries.append(
            LedgerEntry(
                ref=ref,
                state=state,
                run_id=run_id,
                before=before,
            )
        )
    return StateLedger(tuple(entries))


def _required_string(payload: dict[str, object], key: str) -> str:
    return _string(payload.get(key))


def _string(value: object) -> str:
    if not isinstance(value, str):
        msg = "expected string value"
        raise ExecutionError(msg)
    return value
