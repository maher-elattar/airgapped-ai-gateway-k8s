from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from airgap_ai_gateway.airgap_bundle import (
    DEFAULT_COMPATIBILITY_SET,
    DEFAULT_PRIVATE_REGISTRY,
    REQUIRED_BASELINE_ENTRIES,
    build_bundle,
    build_registry_promotion_plan,
    load_source_lock,
    reassemble_parts,
    validate_source_lock,
    verify_bundle,
    verify_rendered_manifests_against_lock,
)
from airgap_ai_gateway.cli import main
from airgap_ai_gateway.errors import BundleError

LOCK = Path("airgap/sources.lock.yaml")


def test_source_lock_is_complete_and_immutable() -> None:
    lock = load_source_lock(LOCK)

    validate_source_lock(lock)

    entries = {entry.name: entry for entry in lock.entries_for(DEFAULT_COMPATIBILITY_SET)}
    assert set(entries) >= REQUIRED_BASELINE_ENTRIES
    assert entries["envoy-ratelimit"].version == "837de552"
    assert "master" not in entries["envoy-ratelimit"].canonical_source
    assert "main" not in entries["envoy-ratelimit"].canonical_source
    for entry in entries.values():
        assert entry.license
        assert entry.provenance
        assert entry.sha256 or entry.oci_digest
        if entry.is_image:
            assert entry.destination_name.startswith(f"{DEFAULT_PRIVATE_REGISTRY}/")
            assert "@sha256:" in entry.destination_name
            assert "@sha256:" in entry.canonical_source


def test_bundle_build_verify_offline_and_logically_reproducible(tmp_path: Path) -> None:
    first = build_bundle(lock_path=LOCK, output_dir=tmp_path / "first")
    second = build_bundle(lock_path=LOCK, output_dir=tmp_path / "second")

    assert first["networkRequiredForVerification"] is False
    assert first["logicalInventoryDigest"] == second["logicalInventoryDigest"]

    first_inventory = json.loads(
        (tmp_path / "first" / DEFAULT_COMPATIBILITY_SET / "inventory.json").read_text(
            encoding="utf-8"
        )
    )
    second_inventory = json.loads(
        (tmp_path / "second" / DEFAULT_COMPATIBILITY_SET / "inventory.json").read_text(
            encoding="utf-8"
        )
    )
    assert first_inventory["logicalInventory"] == second_inventory["logicalInventory"]

    report = verify_bundle(
        bundle_dir=tmp_path / "first" / DEFAULT_COMPATIBILITY_SET,
        lock_path=LOCK,
    )
    assert report["status"] == "verified"
    assert report["networkRequests"] == 0


def test_modified_payload_byte_fails_offline_verification(tmp_path: Path) -> None:
    build_bundle(lock_path=LOCK, output_dir=tmp_path)
    bundle_dir = tmp_path / DEFAULT_COMPATIBILITY_SET
    inventory = json.loads((bundle_dir / "inventory.json").read_text(encoding="utf-8"))
    payload = bundle_dir / inventory["artifacts"][0]["path"]
    payload.write_bytes(payload.read_bytes() + b"\n")

    with pytest.raises(BundleError, match="checksum"):
        verify_bundle(bundle_dir=bundle_dir, lock_path=LOCK)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        (
            "destinationName",
            "docker.io/envoyproxy/ratelimit:latest",
            "destination must be digest pinned",
        ),
        (
            "canonicalSource",
            "docker.io/envoyproxy/ratelimit:main",
            "source must be digest pinned",
        ),
    ],
)
def test_public_or_unpinned_production_image_rejected(
    tmp_path: Path,
    field: str,
    value: str,
    expected: str,
) -> None:
    raw = yaml.safe_load(LOCK.read_text(encoding="utf-8"))
    for entry in raw["spec"]["entries"]:
        if entry["name"] == "envoy-ratelimit":
            entry[field] = value
    lock_path = tmp_path / "bad-lock.yaml"
    lock_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(BundleError, match=expected):
        validate_source_lock(load_source_lock(lock_path))


def test_registry_promotion_plan_contains_exact_copy_and_existence_checks() -> None:
    plan = build_registry_promotion_plan(load_source_lock(LOCK))

    assert plan["status"] == "planned"
    assert plan["actionCount"] == 9
    actions_list = cast(list[dict[str, Any]], plan["actions"])
    actions = {str(action["name"]): action for action in actions_list}
    ratelimit = actions["envoy-ratelimit"]
    assert ratelimit["source"] == (
        "docker.io/envoyproxy/ratelimit@sha256:"
        "a8661ef320aaffbf4f10c15b40a5bd47906a7256d5c67bbf20de7fd33e562d7c"
    )
    assert ratelimit["destination"].startswith(f"{DEFAULT_PRIVATE_REGISTRY}/")
    assert ratelimit["copyCommand"][0] == "skopeo"
    assert ratelimit["existenceCheck"][0] == "skopeo"
    assert ratelimit["dockerFallback"][0][0] == "docker"


def test_rendered_manifests_match_promoted_image_map() -> None:
    report = verify_rendered_manifests_against_lock(lock_path=LOCK)

    assert report["status"] == "rendered-manifests-verified"
    assert report["overlay"] == "production-reference"


def test_split_parts_reassemble_to_complete_checksum(tmp_path: Path) -> None:
    build_bundle(lock_path=LOCK, output_dir=tmp_path, split_size_bytes=1024)
    bundle_dir = tmp_path / DEFAULT_COMPATIBILITY_SET
    report = reassemble_parts(
        parts_metadata=bundle_dir / "parts" / "parts.json",
        output_path=tmp_path / "reassembled" / "bundle.blob",
    )

    assert report["status"] == "reassembled"
    assert (
        report["sha256"]
        == json.loads((bundle_dir / "parts" / "parts.json").read_text(encoding="utf-8"))[
            "completeSha256"
        ]
    )


def test_cli_bundle_build_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    build_code = main(
        [
            "bundle",
            "build",
            "--lock-file",
            str(LOCK),
            "--dist-dir",
            str(tmp_path),
            "--metadata-hook",
            "sbom",
        ]
    )
    build_output = json.loads(capsys.readouterr().out)

    verify_code = main(
        [
            "bundle",
            "verify",
            "--lock-file",
            str(LOCK),
            "--bundle-dir",
            str(tmp_path / DEFAULT_COMPATIBILITY_SET),
        ]
    )
    verify_output = json.loads(capsys.readouterr().out)

    assert build_code == 0
    assert build_output["status"] == "built"
    assert verify_code == 0
    assert verify_output["status"] == "verified"
