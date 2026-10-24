"""import_elevation tests (Req 4.1-4.6) with a stubbed fetcher; no network."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH / "scripts"))

import import_elevation as ie  # noqa: E402


def fake_fetcher(calls: list, nodata_idx: set[int] = frozenset(), res: float = 1):
    def fetch(url: str, timeout: float) -> bytes:
        pts = json.loads(parse_qs(urlparse(url).query)["geometry"][0])["points"]
        calls.append(len(pts))
        samples = [{"locationId": i, "value": f"{180 + p[1] - 41:.3f}", "resolution": res}
                   for i, p in enumerate(pts) if i not in nodata_idx]
        return json.dumps({"samples": samples}).encode()
    return fetch


def failing(url: str, timeout: float) -> bytes:
    raise OSError("unreachable")


def test_batches_and_values_in_meters_navd88(tmp_path):
    calls: list = []
    pts = [(-87.6 - i * 1e-4, 41.88) for i in range(25)]
    out = ie.sample_elevations(pts, fetcher=fake_fetcher(calls), batch_size=10, pause=0)
    assert calls == [10, 10, 5]
    s = out[ie.key_for(*pts[0])]
    assert s.known and abs(s.meters - 180.88) < 1e-6 and s.datum == "NAVD88" and s.resolution_m == 1


def test_nodata_point_is_unknown_not_zero():
    out = ie.sample_elevations([(-87.6, 41.88), (-87.61, 41.88)], fetcher=fake_fetcher([], {1}), pause=0)
    assert out[ie.key_for(-87.61, 41.88)].meters == "Unknown"
    assert out[ie.key_for(-87.6, 41.88)].known


def test_sentinel_values_are_unknown():
    body = json.dumps({"samples": [{"locationId": 0, "value": "-9999", "resolution": 1},
                                   {"locationId": 1, "value": "NoData"}]}).encode()
    assert ie.parse_samples(body, 2) == [(None, None), (None, None)]


def test_cache_prevents_refetch(tmp_path):
    cache = tmp_path / "c.json"
    calls: list = []
    pts = [(-87.6, 41.88), (-87.61, 41.89)]
    ie.sample_elevations(pts, fetcher=fake_fetcher(calls), cache_path=cache, pause=0)
    stats: dict = {}
    ie.sample_elevations(pts, fetcher=fake_fetcher(calls), cache_path=cache, pause=0, stats=stats)
    assert calls == [2] and stats["cache_hits"] == 2 and stats["requests"] == 0


def test_unreachable_api_gives_unknown_uncached_and_aborts(tmp_path):
    cache = tmp_path / "c.json"
    pts = [(-87.6 - i * 1e-3, 41.88) for i in range(10)]
    stats: dict = {}
    out = ie.sample_elevations(pts, fetcher=failing, cache_path=cache, batch_size=2, pause=0, stats=stats)
    assert all(not s.known for s in out.values()) and len(out) == 10
    assert stats["aborted"] and stats["requests"] == ie.MAX_CONSECUTIVE_FAILURES
    assert ie.load_cache(cache) == {}


def known(m, res=1):
    return ie.ElevationSample(m, resolution_m=res)


def test_edge_confidence_rules():
    surf = {"infrastructure_level": "surface"}
    a = ie.assign_edge_elevations(surf, known(180.0), known(181.0))
    assert a == {"elevation_start": 180.0, "elevation_end": 181.0, "elevation_confidence": "High"}
    assert ie.assign_edge_elevations({}, known(1.0), known(2.0))["elevation_confidence"] == "Medium"
    assert ie.assign_edge_elevations(surf, known(1.0, 10), known(2.0, 10))["elevation_confidence"] == "Medium"
    for seg in ({"bridge_flag": True}, {"tunnel_flag": True}, {"layer": -1},
                {"infrastructure_level": "elevated"}, {"infrastructure_level": "lower-level"}):
        assert ie.assign_edge_elevations(seg, known(1.0), known(2.0))["elevation_confidence"] == "Low"
    u = ie.assign_edge_elevations(surf, known(1.0), ie.UNKNOWN_SAMPLE)
    assert u["elevation_end"] == "Unknown" and u["elevation_confidence"] == "Unknown"


def test_percentiles():
    p = ie.percentiles([known(float(v)) for v in range(0, 101)] + [ie.UNKNOWN_SAMPLE])
    assert p == {"p5": 5.0, "p95": 95.0}
    assert ie.percentiles([ie.UNKNOWN_SAMPLE]) == {"p5": "Unknown", "p95": "Unknown"}


def test_segment_endpoints():
    seg = {"geometry": "LINESTRING (-87.6353104 41.8673088, -87.6352 41.8668, -87.6351633 41.8660834)"}
    assert ie.segment_endpoints(seg) == ((-87.63531, 41.867309), (-87.635163, 41.866083))
    assert ie.segment_endpoints({"geometry": "Unknown"}) is None


def _write_segments(path: Path):
    segs = [{"geometry": "LINESTRING (-87.6 41.88, -87.61 41.88)", "infrastructure_level": "surface"},
            {"geometry": "LINESTRING (-87.61 41.88, -87.62 41.88)", "bridge_flag": True}]
    path.parent.mkdir(parents=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for s in segs:
            fh.write(json.dumps(s) + "\n")


def test_run_merges_summary_and_fallbacks_without_clobbering(tmp_path):
    seg = tmp_path / "streets" / "segments.jsonl.gz"
    _write_segments(seg)
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "summary_report.json").write_text(json.dumps({"keep": 1}))
    (proc / "fallbacks.json").write_text(json.dumps({"download:x": {"a": 1}, "elevation:old": {}}))
    s = ie.run(segments_path=seg, processed_dir=proc, cache_path=None,
               fetcher=fake_fetcher([], {1}), pause=0)
    assert s["node_count"] == 3 and s["unknown_count"] == 1
    rep = json.loads((proc / "summary_report.json").read_text())
    assert rep["keep"] == 1 and rep["elevation"]["unknown_count"] == 1
    assert rep["elevation"]["elevation_percentiles_m"]["p5"] != "Unknown"
    fb = json.loads((proc / "fallbacks.json").read_text())
    assert fb["download:x"] == {"a": 1} and "elevation:old" not in fb
    assert "elevation:partial_coverage" in fb and "elevation:limitation" in fb
    rows = [json.loads(l) for l in gzip.open(proc / "elevation" / "node_elevations.jsonl.gz", "rt")]
    assert len(rows) == 3 and sum(r["elevation_m"] == "Unknown" for r in rows) == 1


def test_run_with_unreachable_api_records_fallback(tmp_path):
    seg = tmp_path / "streets" / "segments.jsonl.gz"
    _write_segments(seg)
    s = ie.run(segments_path=seg, processed_dir=tmp_path / "proc", cache_path=None, fetcher=failing, pause=0)
    assert s["known_count"] == 0 and s["edge_confidence_counts"] == {"Unknown": 2}
    fb = json.loads((tmp_path / "proc" / "fallbacks.json").read_text())
    assert "elevation:3dep_unreachable" in fb
