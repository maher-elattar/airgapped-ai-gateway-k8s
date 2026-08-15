"""YAML loading and dumping helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml


def load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML mapping from a file."""

    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        msg = f"{path} must contain a YAML mapping"
        raise TypeError(msg)
    return cast(dict[str, Any], loaded)


def dump_yaml(data: object) -> str:
    """Dump YAML deterministically enough for reviewable generated output."""

    return yaml.safe_dump(data, sort_keys=False, explicit_start=True)
