#!/usr/bin/env python
"""Validate Argo CD GitOps source and managed overlays."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from airgap_ai_gateway.gitops import GITOPS_ENVIRONMENTS, validate_gitops  # noqa: E402


def main() -> int:
    """Run GitOps validation."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=GITOPS_ENVIRONMENTS, action="append")
    args = parser.parse_args()

    environments = tuple(args.environment or GITOPS_ENVIRONMENTS)
    failures = [
        f"{environment}: {error}"
        for environment in environments
        for error in validate_gitops(environment)
    ]
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    print(f"gitops validation passed: {len(environments)} environment(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
