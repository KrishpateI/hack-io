"""extract_pmtiles tests (task 14.3, Req 41.1, 3.8): command builder and merge logic, no network."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH / "scripts"))
import extract_pmtiles as ep  # noqa: E402


def test_command_has_bbox_in_lon_lat_order_and_zoom_range():
    cmd = ep.build_extract_command("pmtiles", "https://x/20260101.pmtiles", "out.pmtiles")
    assert cmd[:4] == ["pmtiles", "extract", "https://x/20260101.pmtiles", "out.pmtiles"]
    assert "--bbox=-87.68,41.85,-87.6,41.91" in cmd
    assert "--minzoom=12" in cmd and "--maxzoom=16" in cmd


def test_invalid_zoom_or_bbox_rejected():
    with pytest.raises(ValueError):
        ep.build_extract_command("p", "s", "o", minzoom=16, maxzoom=12)
    with pytest.raises(ValueError):
        ep.build_extract_command("p", "s", "o", bbox={"north": 1, "south": 2, "west": 0, "east": 1})


def test_build_url_and_date_validation():
    assert ep.build_source_url("20260102") == "https://build.protomaps.com/20260102.pmtiles"
    with pytest.raises(ValueError):
        ep.build_source_url("2026-01-02")


def test_latest_build_date_skips_missing_days():
    today = datetime(2026, 1, 10, tzinfo=timezone.utc)
    ok = {ep.build_source_url("20260108")}
    assert ep.latest_build_date(today, probe=lambda u: u in ok) == "20260108"
    assert ep.latest_build_date(today, probe=lambda u: False) is None


def test_dry_run_prints_command_and_writes_nothing(tmp_path, capsys):
    cat, fb = tmp_path / "catalog.json", tmp_path / "fallbacks.json"
    rc = ep.main(["--dry-run", "--build-date", "20260101", "--tools-dir", str(tmp_path),
                  "--catalog", str(cat), "--fallbacks", str(fb)])
    assert rc == 0 and not cat.exists() and not fb.exists()
    assert "extract https://build.protomaps.com/20260101.pmtiles" in capsys.readouterr().out


def test_catalog_and_fallbacks_read_merge_write(tmp_path):
    cat, fb = tmp_path / "catalog.json", tmp_path / "fallbacks.json"
    cat.write_text(json.dumps({"sources": [{"source_id": "OTHER"}, {"source_id": ep.SOURCE_ID, "old": 1}]}))
    fb.write_text(json.dumps({"elevation:x": 1, "tiles:stale": 2}))
    rec = ep.source_record("20260101", True, tmp_path / "chicago.pmtiles", 10)
    ep.merge_catalog(cat, rec)
    ep.merge_catalog(cat, rec)
    ids = [s["source_id"] for s in json.loads(cat.read_text())["sources"]]
    assert sorted(ids) == sorted(["OTHER", ep.SOURCE_ID])
    assert any("ODbL" in r or "Open Database License" in r for r in rec["usage_restrictions"])
    ep.merge_fallbacks(fb, {ep.FALLBACK_KEY: ep.fallback_entry("unavailable", ["e"], None, None)})
    out = json.loads(fb.read_text())
    assert out["elevation:x"] == 1 and "tiles:stale" not in out and ep.FALLBACK_KEY in out
