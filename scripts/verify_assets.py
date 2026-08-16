#!/usr/bin/env python
"""Verify local visual assets against the recorded source registry."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import yaml


def main() -> int:
    sources = Path("docs/assets/sources.yaml")
    data = yaml.safe_load(sources.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("docs/assets/sources.yaml must contain a mapping")
    assets = cast(list[dict[str, Any]], data.get("assets", []))
    for asset in assets:
        path = Path(str(asset["path"]))
        expected = str(asset["sha256"])
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"hash mismatch: {path}")
    print(f"assets verified: {len(assets)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
