#!/usr/bin/env python3
"""Scan the working tree and Git history before publishing."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_SCAN_LIMIT_BYTES = 2 * 1024 * 1024
ARCHIVE_SUFFIXES = (
    ".7z",
    ".docx",
    ".gz",
    ".pdf",
    ".rar",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
)


@dataclass(frozen=True, slots=True)
class Rule:
    """One redacted content rule."""

    name: str
    pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class Finding:
    """One scan finding with no secret value."""

    rule: str
    location: str
    detail: str


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run Git in the repository."""

    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )


def decode_path_list(output: str) -> list[Path]:
    """Decode a Git NUL-delimited path list."""

    return [Path(item) for item in output.split("\0") if item]


def load_denylist(paths: Iterable[Path]) -> list[Rule]:
    """Load private environment denylist patterns without exposing their values."""

    rules: list[Rule] = []
    for path in paths:
        if not path.exists():
            continue
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            pattern = line.strip()
            if not pattern or pattern.startswith("#"):
                continue
            rules.append(
                Rule(
                    name=f"private-environment-denylist:{path.name}:{index}",
                    pattern=re.compile(pattern, re.IGNORECASE | re.MULTILINE),
                )
            )
    return rules


def content_rules(extra_denylist: Iterable[Path]) -> list[Rule]:
    """Return content rules used for working-tree and history scans."""

    kubeconfig_markers = [
        r"^\s*" + "client" + "-" + "key" + "-" + "data:",
        r"^\s*" + "certificate" + "-" + "authority" + "-" + "data:",
        r"^\s*" + "current" + "-" + "context:",
    ]
    rules = [
        Rule(
            "api-key-like-token",
            re.compile(r"(^|[^A-Za-z0-9_])sk-[A-Za-z0-9][A-Za-z0-9_-]{16,}"),
        ),
        Rule(
            "private-key-block",
            re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
        ),
        Rule(
            "kubeconfig-material",
            re.compile(r"(?m)^(clusters|contexts|users):\s*$|" + "|".join(kubeconfig_markers)),
        ),
        Rule(
            "kubernetes-secret-data",
            re.compile(r"(?ms)^kind:\s*Secret\s*$.*^\s*(data|stringData):\s*$"),
        ),
    ]
    default_denylist = REPO_ROOT / ".secret-scan-denylist"
    rules.extend(load_denylist([default_denylist, *extra_denylist]))
    return rules


def is_binary(data: bytes) -> bool:
    """Return whether data looks binary."""

    return b"\0" in data[:4096]


def scan_text(location: str, data: bytes, rules: Iterable[Rule]) -> list[Finding]:
    """Scan one text blob without printing matched values."""

    if is_binary(data):
        return []
    text = data.decode("utf-8", errors="ignore")
    findings: list[Finding] = []
    for rule in rules:
        if rule.pattern.search(text):
            findings.append(Finding(rule=rule.name, location=location, detail="content match"))
    return findings


def path_findings(location: str, size: int, max_file_bytes: int) -> list[Finding]:
    """Return path and size findings."""

    path = Path(location)
    path_text = location.replace("\\", "/")
    findings: list[Finding] = []

    if "/.git/objects/" in f"/{path_text}" or path_text.endswith("/.git"):
        findings.append(
            Finding(rule="old-git-object-files", location=location, detail="nested Git data")
        )
    if path.suffix.lower() in ARCHIVE_SUFFIXES:
        findings.append(
            Finding(
                rule="large-binary-or-archive-artifact", location=location, detail="archive path"
            )
        )
    if size > max_file_bytes:
        findings.append(
            Finding(
                rule="large-binary-or-archive-artifact",
                location=location,
                detail=f"{size} bytes exceeds {max_file_bytes} bytes",
            )
        )
    return findings


def tracked_and_untracked_files() -> list[Path]:
    """Return tracked and untracked, non-ignored files."""

    completed = run_git(["ls-files", "-co", "--exclude-standard", "-z"])
    return decode_path_list(completed.stdout)


def scan_working_tree(rules: Iterable[Rule], max_file_bytes: int) -> list[Finding]:
    """Scan tracked and untracked, non-ignored files in the working tree."""

    findings: list[Finding] = []
    for relative_path in tracked_and_untracked_files():
        path = REPO_ROOT / relative_path
        if not path.is_file():
            continue
        size = path.stat().st_size
        location = str(relative_path)
        findings.extend(path_findings(location, size, max_file_bytes))
        if size <= TEXT_SCAN_LIMIT_BYTES:
            findings.extend(scan_text(location, path.read_bytes(), rules))
    findings.extend(scan_nested_git_directories())
    return findings


def scan_nested_git_directories() -> list[Finding]:
    """Detect nested Git directories under the repository root."""

    findings: list[Finding] = []
    root_git = REPO_ROOT / ".git"
    for path in REPO_ROOT.rglob(".git"):
        if path == root_git:
            continue
        findings.append(
            Finding(
                rule="old-git-object-files",
                location=str(path.relative_to(REPO_ROOT)),
                detail="nested Git directory",
            )
        )
    return findings


def commits() -> list[str]:
    """Return all commits reachable from refs."""

    completed = run_git(["rev-list", "--all"])
    return [line for line in completed.stdout.splitlines() if line]


def tree_entries(commit: str) -> list[tuple[str, int, str]]:
    """Return blob object id, size, and path for one commit."""

    completed = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--long", commit],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    entries: list[tuple[str, int, str]] = []
    for record in completed.stdout.decode("utf-8", errors="ignore").split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        parts = meta.split()
        if len(parts) < 4:
            continue
        entries.append((parts[2], int(parts[3]), path))
    return entries


def read_blob(blob_id: str) -> bytes:
    """Read one Git blob."""

    completed = subprocess.run(
        ["git", "cat-file", "-p", blob_id],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def scan_history(rules: Iterable[Rule], max_file_bytes: int) -> list[Finding]:
    """Scan all reachable Git history without printing matched values."""

    findings: list[Finding] = []
    scanned_blobs: set[str] = set()
    for commit in commits():
        for blob_id, size, path in tree_entries(commit):
            location = f"{commit[:12]}:{path}"
            findings.extend(path_findings(location, size, max_file_bytes))
            if blob_id in scanned_blobs or size > TEXT_SCAN_LIMIT_BYTES:
                continue
            scanned_blobs.add(blob_id)
            findings.extend(scan_text(location, read_blob(blob_id), rules))
    return findings


def parse_args() -> argparse.Namespace:
    """Parse command arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--working-tree",
        action="store_true",
        help="scan tracked and untracked non-ignored working-tree files",
    )
    parser.add_argument("--history", action="store_true", help="scan all reachable Git history")
    parser.add_argument(
        "--denylist",
        action="append",
        type=Path,
        default=[],
        help="additional private regex denylist file; values are not printed",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
        help="maximum allowed file size for publishable tracked content",
    )
    return parser.parse_args()


def main() -> int:
    """Run the pre-publication scan."""

    args = parse_args()
    scan_worktree = args.working_tree or not args.history
    scan_git_history = args.history
    rules = content_rules(args.denylist)

    findings: list[Finding] = []
    if scan_worktree:
        findings.extend(scan_working_tree(rules, args.max_bytes))
    if scan_git_history:
        findings.extend(scan_history(rules, args.max_bytes))

    if findings:
        print("pre-publication scan failed:", file=sys.stderr)
        for finding in findings:
            print(
                f"- {finding.rule}: {finding.location} ({finding.detail})",
                file=sys.stderr,
            )
        return 1

    scopes = []
    if scan_worktree:
        scopes.append("working tree")
    if scan_git_history:
        scopes.append("Git history")
    print(f"pre-publication scan passed for {', '.join(scopes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
