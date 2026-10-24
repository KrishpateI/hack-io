"""Data_Pipeline framework tests (Req 7.2-7.7, 3.7). No network: the fetcher is injected."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH))
sys.path.insert(0, str(SAFEPATH / "backend"))

from scripts.pipeline import (  # noqa: E402
    ACQ_FRESH,
    ACQ_MISSING,
    ACQ_REUSED,
    STAGES,
    DatasetSpec,
    PipelineRunner,
    osm_usage_restrictions,
)


class FakeFetcher:
    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float) -> bytes:
        self.calls.append((url, timeout))
        r = self.responses[url]
        if isinstance(r, Exception):
            raise r
        return r  # type: ignore[return-value]


def _clock():
    ticks = iter(range(1, 10_000))
    return lambda: datetime(2025, 1, 1, 0, 0, next(ticks) % 60, tzinfo=timezone.utc)


def _json_features(data: bytes):
    return json.loads(data)


def _cdp(**kw) -> DatasetSpec:
    return DatasetSpec(
        name="cdp", url="https://cdp", organization="City of Chicago", title="Centerlines",
        is_street_source=True, is_official_source=True,
        handlers={"reproject": lambda d, c: _json_features(d)}, **kw,
    )


def _osm(**kw) -> DatasetSpec:
    return DatasetSpec(
        name="osm", url="https://osm", organization="OpenStreetMap contributors", title="OSM streets",
        is_street_source=True, usage_restrictions=osm_usage_restrictions(),
        handlers={"reproject": lambda d, c: _json_features(d)}, **kw,
    )


def _runner(tmp_path, specs, fetcher, **kw):
    return PipelineRunner(
        specs, raw_dir=tmp_path / "raw", processed_dir=tmp_path / "processed",
        fetcher=fetcher, clock=_clock(), **kw,
    )


def _entry(result, name):
    return next(e for e in result.report["datasets"] if e["dataset"] == name)


def test_stage_order_is_fixed():
    assert STAGES == (
        "check_availability", "save_raw", "reproject", "clip",
        "clean_geometry", "normalize", "build_nodes_edges", "save_outputs",
    )


def test_fresh_run_report_and_source_records(tmp_path):
    f = FakeFetcher({"https://cdp": b"[1,2,3]", "https://osm": b"[1,2]"})
    result = _runner(tmp_path, [_cdp(), _osm()], f).run()
    assert result.ok and result.exit_code == 0
    report = json.loads(result.report_path.read_text())
    cdp = _entry(result, "cdp")
    assert cdp["acquisition_status"] == ACQ_FRESH
    assert cdp["feature_count"] == 3 and cdp["source_url"] == "https://cdp"
    assert cdp["retrieved_at"] and cdp["warnings"] == []
    assert report["datasets"][1]["feature_count"] == 2
    assert (tmp_path / "processed" / "cdp" / "data.json").exists()
    records = {r["source_id"]: r for r in json.loads((tmp_path / "processed" / "source_records.json").read_text())}
    assert set(records) == {"src-cdp", "src-osm"}
    restrictions = " ".join(records["src-osm"]["usage_restrictions"])
    assert "ODbL" in restrictions and "Attribution" in restrictions
    assert all(c[1] == 60 for c in f.calls)


def test_three_attempts_then_reuse_previous_copy_with_timestamp_warning(tmp_path):
    ok = FakeFetcher({"https://cdp": b"[1,2,3]"})
    first = _runner(tmp_path, [_cdp()], ok).run()
    stamp = _entry(first, "cdp")["retrieved_at"]

    bad = FakeFetcher({"https://cdp": TimeoutError("slow")})
    result = _runner(tmp_path, [_cdp()], bad).run()
    assert len(bad.calls) == 3
    e = _entry(result, "cdp")
    assert e["acquisition_status"] == ACQ_REUSED
    assert e["retrieved_at"] == stamp
    assert any(stamp in w for w in e["warnings"])
    assert e["feature_count"] == 3 and result.ok


def test_missing_source_recorded_and_others_complete(tmp_path):
    f = FakeFetcher({"https://cdp": OSError("down"), "https://osm": b"[1]"})
    result = _runner(tmp_path, [_cdp(), _osm()], f).run()
    assert result.ok
    cdp = _entry(result, "cdp")
    assert cdp["acquisition_status"] == ACQ_MISSING and cdp["warnings"]
    assert _entry(result, "osm")["processed"] is True
    assert not (tmp_path / "processed" / "cdp").exists()
    ids = [r["source_id"] for r in json.loads((tmp_path / "processed" / "source_records.json").read_text())]
    assert ids == ["src-osm"]


def test_stage_failure_is_isolated_and_partial_outputs_removed(tmp_path):
    def boom(d, c):
        raise RuntimeError("bad geometry")

    other = DatasetSpec(
        name="stations", url="https://gtfs", organization="CTA", title="GTFS",
        handlers={"reproject": lambda d, c: _json_features(d), "clean_geometry": boom},
    )
    # Pre-existing output from an earlier run must not survive as a partial result.
    (tmp_path / "processed" / "stations").mkdir(parents=True)
    (tmp_path / "processed" / "stations" / "data.json").write_text("old")
    f = FakeFetcher({"https://cdp": b"[1]", "https://gtfs": b"[1,2]"})
    result = _runner(tmp_path, [_cdp(), other], f).run()
    assert result.ok
    e = _entry(result, "stations")
    assert e["failed_stage"] == "clean_geometry"
    assert e["errors"][0]["dataset"] == "stations" and e["errors"][0]["stage"] == "clean_geometry"
    assert not (tmp_path / "processed" / "stations").exists()
    assert _entry(result, "cdp")["processed"] is True


def test_fatal_when_no_street_source_and_graph_unchanged(tmp_path):
    graph = tmp_path / "processed" / "graph"
    graph.mkdir(parents=True)
    latest = graph / "latest.json"
    latest.write_text('{"version": "v1"}')
    f = FakeFetcher({"https://cdp": OSError("x"), "https://osm": OSError("y"), "https://gtfs": b"[1]"})
    gtfs = DatasetSpec(name="gtfs", url="https://gtfs", organization="CTA", title="GTFS")
    result = _runner(tmp_path, [_cdp(), _osm(), gtfs], f).run()
    assert not result.ok and result.exit_code == 1
    assert result.report["status"] == "failed"
    assert latest.read_text() == '{"version": "v1"}'
    assert not (tmp_path / "processed" / "gtfs").exists()
    assert result.report_path.exists()


def test_one_street_source_suffices(tmp_path):
    f = FakeFetcher({"https://cdp": OSError("x"), "https://osm": b"[1]"})
    assert _runner(tmp_path, [_cdp(), _osm()], f).run().exit_code == 0


def test_invalid_handler_stage_rejected():
    with pytest.raises(ValueError):
        DatasetSpec(name="a", url="u", organization="o", title="t", handlers={"nope": lambda d, c: d})
