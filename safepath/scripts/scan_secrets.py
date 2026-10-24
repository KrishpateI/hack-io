#!/usr/bin/env python3
"""Repo secret scanner (task 14.10, Req 31.2/31.3). Pure stdlib, no network.

Scans every file that would be committed (tracked + untracked-not-ignored via git,
falling back to a directory walk), plus ``frontend/dist`` if built. Exits 1 on
findings. Findings print ``file:line [rule] masked-excerpt`` and never the full secret.

Allowlist: ``scripts/secret_scan_allowlist.json``::

    {"entries": [{"path": "glob", "rule": "optional-rule-id",
                  "match": "optional regex on the matched text",
                  "justification": "required"}]}

Usage: python scripts/scan_secrets.py [--root DIR] [--allowlist FILE] [--no-dist]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {"node_modules", ".venv", "venv", "cdk.out", "__pycache__", ".git",
             ".hypothesis", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", ".vite"}
SKIP_PREFIXES = ("data/raw/",)
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock"}
MAX_BYTES = 2_000_000
FRONTEND_PREFIXES = ("frontend/src/", "frontend/public/", "frontend/dist/")
ENV_SAFE_SUFFIXES = (".example", ".sample", ".template")
BAD_VITE = re.compile(r"SECRET|KEY|TOKEN|PASSWORD|PASSWD|CREDENTIAL", re.I)

ASSIGN_NAMES = r"(?:secret|token|password|passwd|api[_-]?key|apikey|private[_-]?key|client[_-]?secret)"


@dataclass(frozen=True)
class Rule:
    id: str
    regex: re.Pattern
    min_entropy: float = 0.0  # applied to group(1) if present else whole match


RULES = [
    Rule("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Rule("aws-secret-access-key", re.compile(
        r"aws_?secret\w*[\"']?\s*[=:]\s*[\"']?([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])", re.I), 3.5),
    Rule("private-key-block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
    Rule("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    Rule("bearer-token", re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{24,})"), 3.5),
    Rule("appsync-api-key", re.compile(r"\bda2-[a-z0-9]{26}\b")),
    Rule("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    Rule("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    Rule("generic-secret-assignment", re.compile(
        r"(?i)\b\w*" + ASSIGN_NAMES + r"\w*[\"']?\s*[=:]\s*[\"']([^\"'\s]{8,})[\"']"), 3.0),
]
FRONTEND_GENERIC = Rule("frontend-hardcoded-secret", RULES[-1].regex, 2.5)
FRONTEND_LONG_LITERAL = Rule("frontend-high-entropy-literal",
                             re.compile(r"[\"'`]([A-Za-z0-9+/_=-]{32,})[\"'`]"), 4.5)

PLACEHOLDER = re.compile(
    r"^(?:\$\{?|<|\{\{|%|process\.env|import\.meta|os\.environ|os\.getenv|your[_-]|changeme|xxx|\*+$|example|placeholder)",
    re.I)


def entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum((s.count(c) / n) * math.log2(s.count(c) / n) for c in set(s))


def mask(s: str) -> str:
    s = s.strip()
    if len(s) <= 8:
        return s[:1] + "*" * (len(s) - 1)
    return f"{s[:4]}{'*' * 6}{s[-2:]}"


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    secret: str  # raw, never printed
    excerpt: str

    def render(self) -> str:
        return f"{self.path}:{self.line} [{self.rule}] {self.excerpt}"


def masked_excerpt(line: str, secret: str) -> str:
    text = line.strip()
    if secret:
        text = text.replace(secret, mask(secret))
    return text[:160]


def load_allowlist(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    for i, e in enumerate(entries):
        if not str(e.get("justification", "")).strip():
            raise SystemExit(f"allowlist entry {i} ({e.get('path')}) lacks a justification")
        if not e.get("path"):
            raise SystemExit(f"allowlist entry {i} lacks a path")
    return entries


def is_allowed(f: Finding, entries: list[dict]) -> bool:
    for e in entries:
        if not fnmatch.fnmatch(f.path, e["path"]):
            continue
        if e.get("rule") and e["rule"] != f.rule:
            continue
        if e.get("match") and not re.search(e["match"], f.secret):
            continue
        return True
    return False


def scan_text(relpath: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    frontend = relpath.startswith(FRONTEND_PREFIXES)
    rules = list(RULES)
    if frontend:
        rules[-1] = FRONTEND_GENERIC  # stricter entropy threshold replaces the generic rule
        if not relpath.startswith("frontend/dist/"):
            rules.append(FRONTEND_LONG_LITERAL)
    for lineno, line in enumerate(text.splitlines(), 1):
        if len(line) > 20000:
            line = line[:20000]
        for rule in rules:
            for m in rule.regex.finditer(line):
                secret = m.group(1) if m.groups() else m.group(0)
                if PLACEHOLDER.match(secret):
                    continue
                if rule.min_entropy and entropy(secret) < rule.min_entropy:
                    continue
                findings.append(Finding(relpath, lineno, rule.id, secret, masked_excerpt(line, secret)))
    # public-identifier check for Vite env usage
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in re.finditer(r"\b(VITE_[A-Z0-9_]+)", line):
            if BAD_VITE.search(m.group(1)) and (
                    "import.meta.env" in line or relpath.endswith((".example", ".env"))
                    or Path(relpath).name.startswith(".env")):
                findings.append(Finding(relpath, lineno, "vite-secret-name", m.group(1),
                                        f"secret-looking public env name: {m.group(1)}"))
    return dedupe(findings)


def dedupe(fs: list[Finding]) -> list[Finding]:
    out, seen = [], set()
    for f in fs:
        k = (f.path, f.line, f.secret)
        if f.rule == "vite-secret-name":
            k = (f.path, f.line, f.secret, "vite")
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def env_file_finding(relpath: str) -> Finding | None:
    name = Path(relpath).name
    if (name == ".env" or name.startswith(".env.") or name.endswith(".env")) \
            and not name.endswith(ENV_SAFE_SUFFIXES):
        return Finding(relpath, 1, "env-file", "", "env file must not be committed (only .env.example)")
    return None


def skipped(relpath: str) -> bool:
    parts = relpath.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    if relpath.startswith(SKIP_PREFIXES) or parts[-1] in SKIP_NAMES:
        return True
    return False


def git_files(root: Path) -> list[str] | None:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted({p for p in out.decode("utf-8", "replace").split("\0") if p})


def walk_files(root: Path) -> list[str]:
    res = []
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if not skipped(rel):
                res.append(rel)
    return sorted(res)


def collect(root: Path, include_dist: bool) -> list[str]:
    files = git_files(root)
    if files is None:
        files = walk_files(root)
    files = [f for f in files if (root / f).is_file()]
    if not include_dist:
        files = [f for f in files if not f.startswith("frontend/dist/")]
    else:
        dist = root / "frontend" / "dist"
        if dist.is_dir():
            files += [p.relative_to(root).as_posix() for p in dist.rglob("*") if p.is_file()]
    return sorted({f for f in files if not skipped(f)})


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "replace")


def scan(root: Path, allowlist: list[dict], include_dist: bool = True) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    files = collect(root, include_dist)
    for rel in files:
        ef = env_file_finding(rel)
        if ef:
            findings.append(ef)
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        findings.extend(scan_text(rel, text))
    return [f for f in findings if not is_allowed(f, allowlist)], len(files)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--allowlist", type=Path, default=None)
    ap.add_argument("--no-dist", action="store_true")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    allow = args.allowlist or (root / "scripts" / "secret_scan_allowlist.json")
    findings, n = scan(root, load_allowlist(allow), include_dist=not args.no_dist)
    for f in findings:
        print(f.render())
    if findings:
        print(f"\n{len(findings)} potential secret(s) in {n} files scanned. "
              "Remove them, or add a justified entry to scripts/secret_scan_allowlist.json.")
        return 1
    print(f"OK: no secrets found in {n} files scanned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
