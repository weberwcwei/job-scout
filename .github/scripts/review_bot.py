#!/usr/bin/env python3
"""Deterministic PR review bot.

Runs a set of rule-based checks over the diff between BASE_SHA and HEAD_SHA
and posts a single PR comment summarising findings. No LLM, no secrets beyond
the default GITHUB_TOKEN.

Checks:
  - secrets / credentials in the diff
  - debug leftovers (print/console.log/breakpoint/pdb)
  - large files added (> 1 MB)
  - TODO/FIXME markers in changed code
  - missing tests for new Python modules
  - accidental data/export artifacts committed
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE_SHA = os.environ.get("BASE_SHA", "")
HEAD_SHA = os.environ.get("HEAD_SHA", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
PR_NUMBER = os.environ.get("PR_NUMBER", "")

#: Patterns that look like a credential or secret.
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|app[_-]?password)\s*[:=]\s*['\"][A-Za-z0-9_\-\.]{12,}['\"]"),
    re.compile(r"(?i)\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9]{32}\b"),
    re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

#: Debug leftovers. `console.print` is this CLI's normal output mechanism, so
#: it is excluded; only bare print/console.log/breakpoint/pdb are flagged.
DEBUG_PATTERNS = [
    re.compile(r"(?<!console\.)\bprint\s*\("),
    re.compile(r"\bconsole\.log\s*\("),
    re.compile(r"\bbreakpoint\s*\("),
    re.compile(r"\bpdb\.set_trace\s*\("),
    re.compile(r"\bdebugger\s*;"),
]

#: Markers.
TODO_PATTERNS = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"\bXXX\b"),
]

#: Files that look like local run outputs rather than source.
ARTIFACT_PATTERNS = [
    re.compile(r"(?i)\.(csv|log|db|sqlite)$"),
    re.compile(r"(?i)\.venv/"),
    re.compile(r"(?i)__pycache__/"),
]

#: Source extensions we care about for the missing-test check.
SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}

#: Skip noise (lockfiles, generated, vendored).
SKIP_PATHS = {
    "uv.lock",
    "requirements.txt",
    "LICENSE",
    "assets/",
    ".github/",
}


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def changed_files() -> list[str]:
    out = run(["git", "diff", "--name-only", f"{BASE_SHA}...{HEAD_SHA}"])
    return [f for f in out.splitlines() if f]


def diff_for(path: str) -> str:
    return run(["git", "diff", f"{BASE_SHA}...{HEAD_SHA}", "--", path])


def is_skip(path: str) -> bool:
    return any(path == s or path.startswith(s) for s in SKIP_PATHS)


def check_secrets(path: str, diff: str) -> list[str]:
    hits = []
    for line in diff.splitlines():
        if not line.startswith("+"):
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(line):
                hits.append(line.strip()[:120])
                break
    return hits


def check_debug(path: str, diff: str) -> list[str]:
    hits = []
    for line in diff.splitlines():
        if not line.startswith("+"):
            continue
        for pat in DEBUG_PATTERNS:
            if pat.search(line):
                hits.append(line.strip()[:120])
                break
    return hits


def check_todos(path: str, diff: str) -> list[str]:
    hits = []
    for line in diff.splitlines():
        if not line.startswith("+"):
            continue
        for pat in TODO_PATTERNS:
            if pat.search(line):
                hits.append(line.strip()[:120])
                break
    return hits


def check_large_files() -> list[str]:
    hits = []
    out = run(["git", "diff", "--name-only", "--diff-filter=A", f"{BASE_SHA}...{HEAD_SHA}"])
    for path in out.splitlines():
        if is_skip(path):
            continue
        try:
            size = Path(path).stat().st_size
        except OSError:
            continue
        if size > 1_000_000:
            hits.append(f"{path} ({size // 1024} KB)")
    return hits


def check_artifacts(path: str) -> bool:
    return any(p.search(path) for p in ARTIFACT_PATTERNS)


def check_missing_tests(changed: list[str]) -> list[str]:
    """For each new/changed Python module, ensure a test file exercises it.

    Accepts either a filename match (tests/test_<module>.py) or any test file
    that imports/references the module (grouped test files like
    test_ats_adapters.py cover several modules).
    """
    missing = []
    test_files = sorted(Path("tests").glob("test_*.py")) if Path("tests").exists() else []
    for path in changed:
        if not path.startswith("src/") or not path.endswith(".py"):
            continue
        if path.endswith("__init__.py"):
            continue
        module = path[len("src/") :]  # job_scout/ats/ashby.py
        stem = Path(module).stem
        if any(Path(f).name in (f"test_{stem}.py", f"test_cli_{stem}.py") for f in test_files):
            continue
        # Grouped coverage: any test file that mentions the module path.
        dotted = module.replace("/", ".")[: -len(".py")]  # job_scout.ats.ashby
        if any(dotted in f.read_text() for f in test_files):
            continue
        missing.append(path)
    return missing


def post_comment(body: str) -> None:
    if not (TOKEN and REPO and PR_NUMBER):
        print("Skipping comment: missing PR context (local run)")
        print(body)
        return
    url = f"https://api.github.com/repos/{REPO}/issues/{PR_NUMBER}/comments"
    req = urllib.request.Request(
        url,
        data=json.dumps({"body": body}).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - api.github.com
        resp.read()


def main() -> int:
    changed = changed_files()
    findings: dict[str, list[str]] = {
        "secrets": [],
        "debug": [],
        "todos": [],
        "large_files": [],
        "artifacts": [],
        "missing_tests": [],
    }

    for path in changed:
        if is_skip(path):
            continue
        diff = diff_for(path)
        findings["secrets"].extend(check_secrets(path, diff))
        findings["debug"].extend(check_debug(path, diff))
        findings["todos"].extend(check_todos(path, diff))
        if check_artifacts(path):
            findings["artifacts"].append(path)

    findings["large_files"].extend(check_large_files())
    findings["missing_tests"].extend(check_missing_tests(changed))

    total = sum(len(v) for v in findings.values())
    lines = [
        "### 🤖 Review bot",
        "",
        f"Checked {len(changed)} changed file(s) between `{BASE_SHA[:7]}` and `{HEAD_SHA[:7]}` — **{total} finding(s)**.",
        "",
    ]

    labels = {
        "secrets": "🔒 Possible secrets/credentials",
        "debug": "🐛 Debug leftovers",
        "todos": "📝 TODO/FIXME markers",
        "large_files": "📦 Large files added",
        "artifacts": "🗂️ Data/export artifacts committed",
        "missing_tests": "🧪 New modules without tests",
    }
    for key, label in labels.items():
        items = findings[key]
        if not items:
            continue
        lines.append(f"**{label}** ({len(items)})")
        for item in items[:10]:
            lines.append(f"- `{item}`")
        if len(items) > 10:
            lines.append(f"- … and {len(items) - 10} more")
        lines.append("")

    if total == 0:
        lines.append("No issues found. ✅")
    else:
        lines.append("_These are heuristic checks — please confirm each finding before acting on it._")

    post_comment("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
