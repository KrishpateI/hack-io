"""build_graph tests (Req 2.2, 5.6, 6.4, 7.1, 7.8, 12) on tiny fixtures; no network."""
from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH / "scripts"))
sys.path.insert(0, str(SAFEPATH / "backend"))

import build_graph as bg  # noqa: E402
import import_elevation as ie  # noqa: E402
from app.models.graph import Graph  # noqa: E402
from app.routing.graph import Graph as RuntimeGraph  # noqa: E402

BUILT = datetime(2026, 1, 2, tzinfo=timezone.utc)
LON0, LAT0 = -87.64, 41.88


def ll(dx: float, dy: float) -> tuple[float, float]:
    return (round(LON0 + dx / 82_800.0, 6), round(LAT0 + dy / 111_200.0, 6))


def wkt(*pts: tuple[float, float]) -> str:
    return "LINESTRING(" + ", ".join(f"{lon} {lat}" for lon, lat in (ll(*p) for p in pts)) + ")"


def seg(sid, pts, *, oneway="no", modes=("driving",), dist=None, **kw):
    d = {
        "segment_id": sid, "geometry": wkt(*pts), "distance_meters": dist or 100.0, "oneway": oneway,
        "travel_mode": list(modes), "road_type": "residential", "bridge_flag": "Unknown",
        "tunnel_flag": "Unknown", "infrastructure_level": "Unknown", "pedestrian_access": "Unknown",
        "mobility_accessibility": "Unknown", "sources": ["osm_streets"], "source_ids": {"osm_streets": sid},
    }
    d.update(kw)
    return d


def build(segments, elevations=None):
    return bg.build_network(segments, elevations or {}, BUILT)


def by_id(edges):
    return {e.edge_id: e for e in edges}


# -- one-way / modes ---------------------------------------------------------------------------
def test_oneway_forward_has_single_driving_direction():
    _, edges, _ = build([seg("a", [(0, 0), (100, 0)], oneway="forward")])
    assert [e.edge_id for e in edges] == ["a:f"]


def test_oneway_reverse_runs_against_digitised_direction():
    nodes, edges, _ = build([seg("a", [(0, 0), (100, 0)], oneway="reverse")])
    (e,) = edges
    assert e.edge_id == "a:r" and e.from_node == bg.node_id_for(*ll(100, 0))


def test_oneway_driving_only_applies_to_driving_not_walking():
    _, edges, _ = build([seg("a", [(0, 0), (100, 0)], oneway="forward",
                             modes=("driving", "walking", "wheelchair"), pedestrian_access=True)])
    e = by_id(edges)
    assert set(e) == {"a:f", "a:r"}
    assert e["a:f"].travel_mode == ["driving", "walking", "wheelchair"]
    assert e["a:r"].travel_mode == ["walking", "wheelchair"]


def test_unknown_oneway_creates_both_driving_directions_flagged():
    _, edges, _ = build([seg("a", [(0, 0), (100, 0)], oneway="Unknown")])
    assert {e.edge_id for e in edges} == {"a:f", "a:r"}
    assert all("oneway_unknown" in e.hazard_flags for e in edges)


def test_unknown_oneway_walking_only_not_flagged():
    _, edges, _ = build([seg("p", [(0, 0), (50, 0)], oneway="Unknown", modes=("walking", "wheelchair"))])
    assert len(edges) == 2 and all(e.hazard_flags == [] for e in edges)


def test_travel_time_walking_baseline_and_driving_road_class():
    _, edges, _ = build([seg("w", [(0, 0), (140, 0)], modes=("walking",), dist=140.0),
                         seg("d", [(0, 50), (100, 50)], dist=89.0, road_type="residential")])
    e = by_id(edges)
    assert e["w:f"].estimated_travel_time_seconds == pytest.approx(100.0)
    assert e["d:f"].estimated_travel_time_seconds == pytest.approx(10.0)


# -- attributes --------------------------------------------------------------------------------
def test_unknown_attributes_preserved_and_static_classification():
    _, edges, _ = build([seg("a", [(0, 0), (100, 0)])])
    e = edges[0]
    assert e.bridge_flag == e.tunnel_flag == e.infrastructure_level == "Unknown"
    assert e.pedestrian_access == e.mobility_accessibility == e.current_closure_status == "Unknown"
    assert e.elevation_start == e.elevation_end == e.elevation_confidence == "Unknown"
    assert e.classification == "Static" and e.edge_kind == "street" and e.data_timestamp == BUILT


def test_elevations_assigned_and_swapped_for_reverse():
    a, b = ie.key_for(*ll(0, 0)), ie.key_for(*ll(100, 0))
    elev = {a: ie.ElevationSample(180.0, resolution_m=1), b: ie.ElevationSample(182.0, resolution_m=1)}
    nodes, edges, _ = build([seg("a", [(0, 0), (100, 0)], infrastructure_level="surface")], elev)
    e = by_id(edges)
    assert (e["a:f"].elevation_start, e["a:f"].elevation_end) == (180.0, 182.0)
    assert (e["a:r"].elevation_start, e["a:r"].elevation_end) == (182.0, 180.0)
    assert e["a:f"].elevation_confidence == "High"
    assert {n.node_id: n.elevation_m for n in nodes}[bg.node_id_for(*b)] == 182.0


def test_missing_elevation_is_unknown_never_zero():
    nodes, _, st = build([seg("a", [(0, 0), (100, 0)])])
    assert all(n.elevation_m == "Unknown" and n.elevation_conf == "Unknown" for n in nodes)
    assert st["counters"]["nodes_without_elevation"] == 2


# -- noding ------------------------------------------------------------------------------------
def test_shared_vertex_splits_both_segments():
    horizontal = seg("h", [(0, 0), (50, 0), (100, 0)], oneway="no")
    vertical = seg("v", [(50, -50), (50, 0), (50, 50)], oneway="no")
    nodes, edges, _ = build([horizontal, vertical])
    ids = {e.edge_id for e in edges}
    assert {"h~1:f", "h~2:f", "v~1:f", "v~2:f"} <= ids
    g = Graph.build(nodes, edges)
    comps, _ = bg.connectivity(g)
    assert comps["all"]["components"] == 1


def test_t_junction_endpoint_snaps_onto_other_segment():
    street = seg("s", [(0, 0), (100, 0)])
    stub = seg("t", [(50, 2), (50, 40)])  # starts 2 m from the street
    nodes, edges, st = build([street, stub])
    assert st["counters"]["t_junction_cuts_inserted"] == 1
    g = Graph.build(nodes, edges)
    assert bg.connectivity(g)[0]["all"]["components"] == 1
    assert sum(e.distance_meters for e in edges if e.edge_id.startswith("s~") and e.edge_id.endswith(":f")) \
        == pytest.approx(100.0, abs=0.01)


def test_far_endpoint_is_not_snapped():
    nodes, edges, _ = build([seg("s", [(0, 0), (100, 0)]), seg("t", [(50, 30), (50, 60)])])
    comps, warns = bg.connectivity(Graph.build(nodes, edges))
    assert comps["all"]["components"] == 2 and comps["all"]["largest"] == 2
    assert warns and "disconnected" in warns[0]


def test_self_loop_segment_skipped_and_counted():
    _, edges, st = build([seg("loop", [(0, 0), (30, 0), (30, 30), (0, 0)])])
    assert edges == [] and st["counters"]["self_loop_pieces_skipped"] == 1


# -- simulated station -------------------------------------------------------------------------
def station_feature():
    return {"geometry": {"type": "Point", "coordinates": list(ll(200, 0))},
            "properties": {"name": "Clark/Lake", "station_id": "40380", "routes": []}}


def test_simulated_station_levels_joined_only_by_level_transitions():
    nodes, edges, _ = build([seg("a", [(0, 0), (100, 0)]), seg("b", [(100, 0), (210, 0)])])
    s_nodes, s_edges, info = bg.build_simulated_station(station_feature(), nodes, BUILT)
    g = Graph.build(nodes + s_nodes, edges + s_edges)  # raises if a level crossing is not a transition
    sim = {n.node_id: n for n in s_nodes}
    assert sim["sim-station-street"].level != sim["sim-station-platform"].level
    crossing = [e for e in s_edges if sim.get(e.from_node) and sim.get(e.to_node)]
    assert crossing and all(e.edge_kind == "level_transition" for e in crossing)
    assert {e.transition_type for e in crossing} == {"platform_stair", "ramp"}
    assert all(e.classification == "Simulated" and e.edge_id.startswith("sim-station-") for e in s_edges)
    assert info["linked_to"] == bg.node_id_for(*ll(210, 0)) and "SIMULATED" in info["layer"]["properties"]["notice"]
    assert any(n.node_id == "sim-station-platform" for n in g.nodes)


def test_find_station_by_name():
    gtfs = {"features": [station_feature()]}
    assert bg.find_station(gtfs) is gtfs["features"][0]
    assert bg.find_station({"features": []}) is None


# -- layers ------------------------------------------------------------------------------------
def way(wid, tags, ring=((0, 0), (0, 1), (1, 1), (0, 0))):
    pts = [{"lat": 41.88 + y * 1e-4, "lon": -87.64 + x * 1e-4} for x, y in ring]
    return {"type": "way", "id": wid, "tags": tags, "geometry": pts}


def test_buildings_layer_height_class_only_from_source_attributes():
    doc = {"elements": [
        way(1, {"building": "yes", "height": "50"}),
        way(2, {"building": "yes", "building:levels": "3"}),
        way(3, {"building": "yes", "height": "30 ft", "name": "X"}),
        way(4, {"building": "yes"}),
        way(5, {"building": "yes"}, ring=((0, 0), (0, 1))),  # not a polygon
    ]}
    layer, st = bg.buildings_layer(doc)
    props = {f["properties"]["id"]: f["properties"] for f in layer["features"]}
    assert props["osm-way-1"]["building_height_class"] == "High-rise" and props["osm-way-1"]["height_source"] == "osm:height"
    assert props["osm-way-2"]["building_height_class"] == "Low-rise" and props["osm-way-2"]["height_m"] == "Unknown"
    assert props["osm-way-3"]["height_m"] == pytest.approx(9.14)
    assert props["osm-way-4"]["building_height_class"] == "Unknown" and props["osm-way-4"]["levels"] == "Unknown"
    assert "osm-way-5" not in props and st["skipped_not_closed"] == 1 and st["entrance_nodes_in_source"] == 0


def test_stations_layer_does_not_infer_wheelchair_or_safety():
    gtfs = {"features": [
        {"geometry": {"type": "Point", "coordinates": [-87.63, 41.88]},
         "properties": {"station_id": "1", "name": "A", "routes": [{"name": "Red Line"}], "wheelchair_boarding": "accessible"}},
        {"geometry": {"type": "Point", "coordinates": [-87.62, 41.88]}, "properties": {"station_id": "2", "name": "B", "routes": []}},
    ]}
    f = bg.stations_layer(gtfs)["features"]
    assert f[0]["properties"]["wheelchair_boarding"] == "accessible" and f[0]["properties"]["routes"] == ["Red Line"]
    assert f[1]["properties"]["wheelchair_boarding"] == "Unknown"
    assert not any("safe" in k for k in f[0]["properties"])


# -- end to end --------------------------------------------------------------------------------
def offline(url, timeout):
    raise OSError("offline")


def test_build_writes_loadable_artifact_and_summary(tmp_path):
    pdir = tmp_path
    segs = [seg("h", [(0, 0), (50, 0), (100, 0)]), seg("v", [(50, -50), (50, 0), (50, 50)], oneway="forward"),
            seg("far", [(500, 500), (600, 500)], modes=("walking", "wheelchair"))]
    (pdir / "streets").mkdir()
    with gzip.open(pdir / "streets" / "segments.jsonl.gz", "wt", encoding="utf-8") as fh:
        for s in segs:
            fh.write(json.dumps(s) + "\n")
    (pdir / "cta_gtfs").mkdir()
    (pdir / "cta_gtfs" / "data.json").write_text(json.dumps({"features": [station_feature()]}), encoding="utf-8")
    (pdir / "osm_buildings").mkdir()
    (pdir / "osm_buildings" / "data.json").write_text(json.dumps({"elements": [way(7, {"building": "yes"})]}), encoding="utf-8")
    (pdir / "summary_report.json").write_text(json.dumps({"elevation": {"elevation_percentiles_m": {"p5": 178.0, "p95": 185.0}},
                                                          "keep": 1}), encoding="utf-8")
    (pdir / "source_records.json").write_text(json.dumps([{"source_id": "src-osm_buildings"}]), encoding="utf-8")

    s = bg.build(pdir, BUILT, fetcher=offline, cache_path=None)
    assert s["nodes"] == 9  # h: 3, v: +2 ends, far: 2, simulated station: 2
    assert s["simulated_station"]["built"] and s["buildings_layer"]["features"] == 1
    assert s["edges_unknown_counts"]["elevation_confidence"] == s["edges"]  # offline: nothing sampled, all Unknown
    assert s["connected_components"]["all"]["components"] >= 2 and any("disconnected" in w for w in s["warnings"])
    assert any("entrances" in x for x in s["limitations"])
    report = json.loads((pdir / "summary_report.json").read_text(encoding="utf-8"))
    assert report["keep"] == 1 and report["graph_build"]["version"] == s["version"]

    g = RuntimeGraph.load(pdir)
    assert g.loaded and g.version == s["version"] and g.num_nodes == s["nodes"] and g.num_edges == s["edges"]
    assert set(g.layers) == {"buildings", "cta_stations", "extraction_boundary", "simulated_station"}
    assert g.manifest.elevation_percentiles == {"p5": 178.0, "p95": 185.0}
    assert g.manifest.source_record_ids == ["src-osm_buildings"]
    assert json.loads((pdir / "graphs" / "latest.json").read_text())["version"] == s["version"]


def test_build_without_segments_returns_none(tmp_path):
    assert bg.build(tmp_path, BUILT) is None
