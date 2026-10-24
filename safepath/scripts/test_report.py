"""Parse JUnit XML and render docs/test-results.md / .json for the test-run recorder.

Used by scripts/run_all_tests.ps1. The PowerShell script runs each suite and writes a
steps manifest (JSON); this module turns manifest + JUnit files into the report.

Manifest format::

    {"generated_at": "...", "tool_versions": {"python": "3.12"}, "settings": {...},
     "steps": [{"component": "Backend", "name": "pytest", "command": "...",
                "status": "PASSED|FAILED|SKIPPED", "reason": "", "exit_code": 0,
                "duration_s": 1.2, "timestamp": "...", "junit": "path or null",
                "output_tail": "..."}]}
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def parse_junit(path: str | Path) -> dict[str, Any]:
    """Return {passed, failed, skipped, failures: [names]} for a JUnit XML file.

    Handles both <testsuites> and a bare <testsuite> root. Errors count as failed.
    """
    root = ET.parse(str(path)).getroot()
    passed = failed = skipped = 0
    failures: list[str] = []
    for case in root.iter("testcase"):
        name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
        if case.find("failure") is not None or case.find("error") is not None:
            failed += 1
            failures.append(name)
        elif case.find("skipped") is not None:
            skipped += 1
        else:
            passed += 1
    return {"passed": passed, "failed": failed, "skipped": skipped, "failures": failures}


def parse_jest_json(path: str | Path) -> dict[str, Any]:
    """Return counts from `jest --json` output (pending/todo count as skipped)."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    passed = failed = skipped = 0
    failures: list[str] = []
    for suite in data.get("testResults", []):
        for t in suite.get("assertionResults", []):
            st = t.get("status")
            if st == "passed":
                passed += 1
            elif st == "failed":
                failed += 1
                failures.append(t.get("fullName") or t.get("title", "?"))
            else:
                skipped += 1
        if not suite.get("assertionResults") and suite.get("status") == "failed":
            failed += 1
            failures.append(Path(suite.get("name", "suite")).name + " (suite failed to run)")
    return {"passed": passed, "failed": failed, "skipped": skipped, "failures": failures}


def summarize_step(step: dict[str, Any]) -> dict[str, Any]:
    """Fill in counts and failing names for one manifest step."""
    out = dict(step)
    status = step.get("status", "SKIPPED")
    counts = {"passed": 0, "failed": 0, "skipped": 0, "failures": []}
    junit = step.get("junit")
    if status != "SKIPPED" and junit and Path(junit).is_file():
        try:
            counts = parse_jest_json(junit) if str(junit).endswith(".json") else parse_junit(junit)
        except (ET.ParseError, ValueError):
            counts["failures"] = ["<unparseable JUnit XML>"]
            if status == "PASSED":
                out["status"] = status = "FAILED"
    elif status == "PASSED":
        counts["passed"] = 1  # single pass/fail check (lint, build, synth)
    elif status == "FAILED":
        counts["failed"] = 1
        counts["failures"] = [step.get("name", "check")]
    elif status == "SKIPPED":
        counts["skipped"] = 1
    # A non-zero exit with all-green JUnit (e.g. collection error) is still a failure.
    if status == "FAILED" and counts["failed"] == 0:
        counts["failed"] = 1
        counts["failures"] = counts["failures"] or [step.get("name", "check") + " (non-zero exit)"]
    out.update(counts)
    return out


def build_report(manifest: dict[str, Any]) -> dict[str, Any]:
    steps = [summarize_step(s) for s in manifest.get("steps", [])]
    totals = {
        "passed": sum(s["passed"] for s in steps if s["status"] != "SKIPPED"),
        "failed": sum(s["failed"] for s in steps),
        "skipped": sum(s["skipped"] for s in steps),
        "duration_s": round(sum(float(s.get("duration_s") or 0) for s in steps), 1),
    }
    return {
        "generated_at": manifest.get("generated_at"),
        "tool_versions": manifest.get("tool_versions", {}),
        "settings": manifest.get("settings", {}),
        "totals": totals,
        "steps": steps,
    }


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    t = report["totals"]
    lines = [
        "# SafePath test results",
        "",
        f"Generated: {report.get('generated_at')}",
        "",
        f"**Totals:** {t['passed']} passed, {t['failed']} failed, {t['skipped']} skipped "
        f"({t['duration_s']} s)",
        "",
        "## Results",
        "",
        "| Component | Step | Command | Status | Passed | Failed | Skipped | Duration (s) | Timestamp |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report["steps"]:
        status = s["status"] + (f" ({s['reason']})" if s.get("reason") else "")
        lines.append(
            f"| {_cell(s['component'])} | {_cell(s['name'])} | `{_cell(s['command'])}` | "
            f"{_cell(status)} | {s['passed']} | {s['failed']} | {s['skipped']} | "
            f"{s.get('duration_s', 0)} | {s.get('timestamp', '')} |"
        )
    lines += ["", "## Failing tests", ""]
    failing = [s for s in report["steps"] if s["failures"]]
    if not failing:
        lines.append("None.")
    for s in failing:
        lines.append(f"### {s['component']} / {s['name']}")
        lines += [f"- `{f}`" for f in s["failures"]]
        tail = s.get("output_tail")
        if tail:
            lines += ["", "```text", tail.strip(), "```"]
        lines.append("")
    lines += ["", "## Property-based test settings", ""]
    for k, v in report.get("settings", {}).items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Tool versions", ""]
    for k, v in report.get("tool_versions", {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True)
    p.add_argument("--out-md", required=True)
    p.add_argument("--out-json", required=True)
    args = p.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
    report = build_report(manifest)
    Path(args.out_md).write_text(render_markdown(report), encoding="utf-8")
    Path(args.out_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    t = report["totals"]
    print(f"passed={t['passed']} failed={t['failed']} skipped={t['skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
