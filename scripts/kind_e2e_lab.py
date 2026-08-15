#!/usr/bin/env python
# ruff: noqa: E402, I001
"""Run the disposable kind end-to-end lab."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from airgap_ai_gateway.kind_lab import main


if __name__ == "__main__":
    raise SystemExit(main())
