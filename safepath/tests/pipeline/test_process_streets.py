"""process_streets tests (Req 3.3-3.6, 7.1) using small recorded fixtures; no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH / "scripts"))
sys.path.insert(0, str(SAFEPATH / "backend"))

import process_streets as ps  # noqa: E402

LON0, LAT0 = -87.64, 41.88


def ll(dx_m: float, dy_m: float) -> tuple[float, float]:
    return (LON0 + dx_m / 82_800.0, LAT0 + dy_m / 111_200.0)


def line(*pts: tuple[float, float]) -> list[list[float]]:
    return [list(ll(*p)) for p in pts]


def cdp_feature(tid: str, pts, name="MAIN", direction="S", typ="ST", cls="4", dir_travel="B"):
    return {
        "type": "Feature",
        "properties": {"trans_id": tid, "street_nam": name, "pre_dir": direction, "street_typ": typ,
                       "class": cls, "dir_travel": dir_travel},
        "geometry": {"type": "MultiLineString", "coordinates": [line(*pts)]},
    }


def osm_way(wid: int, pts, **tags):
    geom = [{"lat": ll(*p)[1], "lon": ll(*p)[0]} for p in pts]
    return {"type": "way", "id": wid, "geometry": geom, "tags": tags}


def write(base: Path, dataset: str, doc) -> None:
    (base / dataset).mkdir(parents=True, exist_ok=True)
    (base / dataset / "data.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def run(tmp_path):
    def _run(cdp=None, streets=None, ped=None):
        proc = tmp_path / "processed"
        if cdp is not None:
            write(proc, "cdp_centerlines", {"type": "FeatureCollection", "features": cdp})
        if streets is not None:
            write(proc, "osm_streets", {"elements": streets})
        if ped is not None:
            write(proc, "osm_pedestrian", {"elements": ped})
        summary = ps.process(proc, tmp_path / "raw")
        segs = {s["segment_id"]: s for s in ps.read_segments(proc / "streets" / "segments.jsonl.gz")} if summary else {}
        return summary, segs, proc

    return _run


def test_projection_matches_known_utm_point():
    # Chicago Loop (41.8781, -87.6298) is about E 447,500, N 4,636,000 in UTM 16N.
    x, y = ps.to_utm16n(-87.6298, 41.8781)
    assert 447_000 < float(x) < 448_500 and 4_635_000 < float(y) < 4_637_000
    x0, _ = ps.to_utm16n(-87.0, 41.0)
    assert float(x0) == pytest.approx(500_000.0, abs=1e-6)


def test_normalize_name_aligns_cdp_and_osm():
    assert ps.normalize_name("South Lumber Street") == ps.normalize_name(ps.cdp_display_name(
        {"street_nam": "LUMBER", "pre_dir": "S", "street_typ": "ST"})) == "LUMBER"
    assert ps.normalize_name(None) is None


def test_matched_segment_uses_cdp_geometry_name_and_osm_attributes(run):
    pts = [(0, 0), (100, 0)]
    summary, segs, _ = run(
        cdp=[cdp_feature("1", pts, dir_travel="B")],
        streets=[osm_way(10, [(0, 2), (100, 2)], highway="primary", name="South Main Street", bridge="yes",
                         layer="1", oneway="no", foot="yes")],
        ped=[],
    )
    seg = segs["seg-cdp-1"]
    assert seg["sources"] == ["cdp_centerlines", "osm_streets"]
    assert seg["name"] == "S MAIN ST" and seg["road_type"] == "primary"
    assert seg["bridge_flag"] is True and seg["infrastructure_level"] == "bridge" and seg["layer"] == 1
    assert seg["tunnel_flag"] == "Unknown" and seg["pedestrian_access"] is True
    assert "walking" in seg["travel_mode"]
    assert seg["attribute_sources"]["geometry"] == "cdp" and seg["conflicts"] == []
    assert summary["segments_per_source"] == {"cdp_centerlines+osm_streets": 1}
    assert summary["matching"]["osm_street_merged_into_cdp"] == 1


def test_oneway_conflict_retains_osm_and_warns(run):
    summary, segs, _ = run(
        cdp=[cdp_feature("1", [(0, 0), (100, 0)], dir_travel="B")],
        streets=[osm_way(10, [(0, 1), (100, 1)], highway="residential", name="Main Street", oneway="yes")],
        ped=[],
    )
    seg = segs["seg-cdp-1"]
    assert seg["oneway"] == "forward"
    assert seg["conflicts"][0]["attribute"] == "oneway" and seg["conflicts"][0]["retained_source"] == "osm_streets"
    assert summary["conflicts_by_attribute"] == {"oneway": 1}
    assert any("oneway conflict" in w for w in summary["warnings"])


def test_name_conflict_retains_cdp_when_geometry_coincides(run):
    summary, segs, _ = run(
        cdp=[cdp_feature("1", [(0, 0), (100, 0)], name="MAIN")],
        streets=[osm_way(10, [(0, 1), (100, 1)], highway="residential", name="Other Avenue")],
        ped=[],
    )
    assert segs["seg-cdp-1"]["name"] == "S MAIN ST"
    assert summary["conflicts_by_attribute"] == {"name": 1}


def test_absent_attributes_are_unknown_never_defaults(run):
    _, segs, _ = run(cdp=[cdp_feature("1", [(0, 0), (100, 0)], dir_travel=None)], streets=[], ped=[])
    seg = segs["seg-cdp-1"]
    for attr in ("oneway", "pedestrian_access", "bridge_flag", "tunnel_flag", "layer", "infrastructure_level",
                 "mobility_accessibility", "river_crossing"):
        assert seg[attr] == "Unknown", attr
    assert seg["road_type"] == "cdp_class_4" and seg["travel_mode"] == ["driving"]


def test_osm_only_and_unmatched_segments_kept_with_single_source(run):
    summary, segs, _ = run(
        cdp=[cdp_feature("1", [(0, 0), (100, 0)])],
        streets=[osm_way(20, [(0, 500), (100, 500)], highway="service", tunnel="yes", layer="-1")],
        ped=[],
    )
    assert segs["seg-cdp-1"]["sources"] == ["cdp_centerlines"]
    osm = segs["seg-osm-20"]
    assert osm["sources"] == ["osm_streets"] and osm["infrastructure_level"] == "tunnel"
    assert summary["segments_per_source"] == {"cdp_centerlines": 1, "osm_streets": 1}


def test_infrastructure_level_only_where_source_identifies_it():
    f = ps._infrastructure_level
    assert f({"bridge": "viaduct"}, True, "Unknown", "Unknown")[0] == "elevated"
    assert f({}, "Unknown", "Unknown", -1)[0] == "lower-level"
    assert f({}, "Unknown", "Unknown", 0)[0] == "surface"
    assert f({}, "Unknown", "Unknown", 2)[0] == "Unknown"
    assert f({}, "Unknown", "Unknown", "Unknown")[0] == "Unknown"
    assert f({}, True, True, "Unknown")[1] is not None


def test_pedestrian_paths_become_walking_segments(run):
    _, segs, _ = run(
        cdp=[], streets=[],
        ped=[
            osm_way(1, [(0, 0), (20, 0)], highway="steps"),
            osm_way(2, [(0, 10), (20, 10)], highway="footway", bridge="yes"),
            osm_way(3, [(0, 20), (20, 20)], highway="path"),
            osm_way(4, [(0, 30), (20, 30)], highway="footway", wheelchair="yes"),
        ],
    )
    steps, foot, path, wc = (segs[f"seg-osmped-{i}"] for i in (1, 2, 3, 4))
    assert steps["mobility_accessibility"] == "inaccessible" and steps["travel_mode"] == ["walking"]
    assert foot["mobility_accessibility"] == "Unknown" and foot["travel_mode"] == ["walking", "wheelchair"]
    assert foot["infrastructure_level"] == "bridge"
    assert path["pedestrian_access"] == "Unknown"
    assert wc["mobility_accessibility"] == "accessible"


def test_river_crossing_flagged_from_cdp_river_lines_only(run):
    summary, segs, _ = run(
        cdp=[
            cdp_feature("river", [(-200, 50), (300, 50)], cls="RIV", name="RIVER"),
            cdp_feature("br", [(50, 0), (50, 100)]),
            cdp_feature("dry", [(500, 0), (500, 30)]),
        ],
        streets=[], ped=[],
    )
    assert "seg-cdp-river" not in segs
    assert segs["seg-cdp-br"]["river_crossing"] is True
    assert segs["seg-cdp-dry"]["river_crossing"] == "Unknown"
    assert summary["river_crossing_true"] == 1


def test_missing_sources_returns_none_and_leaves_output(run, tmp_path):
    summary, segs, proc = run()
    assert summary is None and not (proc / "streets").exists()


def test_summary_merged_into_existing_report(run, tmp_path):
    proc = tmp_path / "processed"
    proc.mkdir()
    (proc / "summary_report.json").write_text(json.dumps({"datasets": [1], "status": "completed"}))
    summary, _, _ = run(cdp=[cdp_feature("1", [(0, 0), (50, 0)])], streets=[], ped=[])
    report = json.loads((proc / "summary_report.json").read_text())
    assert report["datasets"] == [1] and report["street_processing"]["segment_count"] == 1
    assert set(report["street_processing"]["unknown_counts"]) >= {"bridge_flag", "infrastructure_level"}
