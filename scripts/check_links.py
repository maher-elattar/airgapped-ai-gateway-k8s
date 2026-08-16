#!/usr/bin/env python
"""Check Markdown local links and optional external references."""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
HTML_SRC_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--check-external", action="store_true")
    args = parser.parse_args()

    markdown_files = _collect_markdown_files(args.paths)
    failures: list[str] = []
    for markdown in markdown_files:
        text = markdown.read_text(encoding="utf-8")
        for target in _extract_targets(text):
            if _is_ignored(target):
                continue
            if _is_external(target):
                if args.check_external:
                    failures.extend(_check_external(markdown, target))
                continue
            failures.extend(_check_local(markdown, target))

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    mode = "local and external" if args.check_external else "local"
    print(f"checked {mode} links in {len(markdown_files)} Markdown files")
    return 0


def _collect_markdown_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(item for item in path.rglob("*.md") if item.is_file()))
        elif path.suffix == ".md":
            files.append(path)
    return sorted(dict.fromkeys(files))


def _extract_targets(text: str) -> list[str]:
    targets = [match.group(1).strip() for match in MARKDOWN_LINK_RE.finditer(text)]
    targets.extend(match.group(1).strip() for match in HTML_SRC_RE.finditer(text))
    return targets


def _is_ignored(target: str) -> bool:
    return (
        not target
        or target.startswith("#")
        or target.startswith("mailto:")
        or target.startswith("tel:")
    )


def _is_external(target: str) -> bool:
    return target.startswith(("http://", "https://"))


def _check_local(markdown: Path, target: str) -> list[str]:
    clean_target = target.split("#", 1)[0]
    if not clean_target:
        return []
    candidate = (markdown.parent / clean_target).resolve()
    repo_root = Path.cwd().resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        return [f"{markdown}: local link escapes repository: {target}"]
    if not candidate.exists():
        return [f"{markdown}: missing local link target: {target}"]
    return []


def _check_external(markdown: Path, target: str) -> list[str]:
    request = urllib.request.Request(
        target,
        method="GET",
        headers={"User-Agent": "airgap-ai-gateway-link-checker/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except urllib.error.URLError as exc:
        return [f"{markdown}: external link failed: {target} ({exc.reason})"]
    if status < 400 or status in {401, 403}:
        return []
    return [f"{markdown}: external link returned {status}: {target}"]


if __name__ == "__main__":
    raise SystemExit(main())
