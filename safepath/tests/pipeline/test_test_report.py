"""Unit tests for scripts/test_report.py (test-run recorder helper)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import test_report as tr  # noqa: E402

JUNIT = """<testsuites><testsuite name="s">
<testcase classname="a.b" name="ok"/>
<testcase classname="a.b" name="bad"><failure message="x"/></testcase>
<testcase classname="a.b" name="err"><error message="x"/></testcase>
<testcase classname="a.b" name="skip"><skipped/></testcase>
</testsuite></testsuites>"""


def test_parse_junit_counts(tmp_path):
    f = tmp_path / "r.xml"
    f.write_text(JUNIT, encoding="utf-8")
    r = tr.parse_junit(f)
    assert (r["passed"], r["failed"], r["skipped"]) == (1, 2, 1)
    assert r["failures"] == ["a.b::bad", "a.b::err"]


def test_report_statuses_and_markdown(tmp_path):
    f = tmp_path / "r.xml"
    f.write_text(JUNIT, encoding="utf-8")
    manifest = {
        "generated_at": "t",
        "settings": {"Hypothesis profile": "ci (100 examples)"},
        "steps": [
            {"component": "Backend", "name": "pytest", "command": "pytest", "status": "FAILED",
             "junit": str(f), "duration_s": 2},
            {"component": "Frontend", "name": "lint", "command": "npm run lint", "status": "PASSED"},
            {"component": "Infra", "name": "synth", "command": "cdk synth", "status": "SKIPPED",
             "reason": "npx not found"},
        ],
    }
    rep = tr.build_report(manifest)
    assert rep["totals"]["failed"] == 2
    assert rep["totals"]["passed"] == 2  # 1 junit + 1 lint
    assert rep["totals"]["skipped"] == 2  # 1 junit skip + 1 skipped step
    md = tr.render_markdown(rep)
    assert "a.b::bad" in md and "SKIPPED (npx not found)" in md and "ci (100 examples)" in md


def test_parse_jest_json(tmp_path):
    f = tmp_path / "j.json"
    f.write_text(
        '{"testResults":[{"name":"a.test.ts","assertionResults":['
        '{"status":"passed","title":"p"},{"status":"failed","fullName":"x fails"},'
        '{"status":"pending","title":"s"}]}]}',
        encoding="utf-8",
    )
    r = tr.parse_jest_json(f)
    assert (r["passed"], r["failed"], r["skipped"]) == (1, 1, 1)
    assert r["failures"] == ["x fails"]


def test_nonzero_exit_without_junit_failures_is_failed():
    s = tr.summarize_step({"name": "build", "status": "FAILED"})
    assert s["failed"] == 1
