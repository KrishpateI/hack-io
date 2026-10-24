"""Task 3.14: SIMULATED fixture graph, fallbacks.json merge and default-graph fallback."""
from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import geo
from app.config import Settings
from app.main import create_app
from app.routing import runtime
from app.routing.graph import FALLBACK, INSUFFICIENT_DATA, OK, Graph
from app.services.graph_artifact import read_artifact

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH))

from scripts import make_fixture_graph as mfg  # noqa: E402

NEIGHBORHOOD_IDS = set(mfg.NEIGHBORHOOD_LABELS)


@pytest.fixture(scope="module")
def fixture():
    return read_artifact(mfg.FIXTURE_ROOT)  # (graph, manifest, layers), checksum verified


def test_committed_fixture_matches_generator(fixture, tmp_path):
    _, manifest, _ = fixture
    assert mfg.make_fixture(tmp_path, None) == manifest.version  # deterministic
    assert manifest.source_record_ids == [mfg.FIXTURE_SOURCE]
    assert "SIMULATED" in manifest.source_record_ids[0]


def test_everything_is_labeled_simulated(fixture):
    g, _, layers = fixture
    assert all(e.classification == "Simulated" and "SIMULATED" in e.source for e in g.edges)
    assert all(n.node_id.startswith("fx-") for n in g.nodes)
    assert all(e.edge_id.startswith("fx-sim-") for e in g.edges)
    for fc in layers.values():
        assert fc["properties"]["simulated"] is True
        assert all(f["properties"]["simulated"] is True for f in fc["features"])


def test_nodes_inside_extraction_area_and_all_neighborhoods(fixture):
    g, _, _ = fixture
    ids = {n.node_id for n in g.nodes}
    assert NEIGHBORHOOD_IDS <= ids and len(NEIGHBORHOOD_IDS) == 8
    for n in g.nodes:
        assert geo.classify_coordinate(n.lat, n.lon) == geo.CoordinateStatus.INSIDE


def test_river_only_crossable_on_flagged_bridges(fixture):
    g, _, _ = fixture
    nodes = {n.node_id: n for n in g.nodes}
    river_lat = (mfg.ROWS[mfg.RIVER_ROW] + mfg.ROWS[mfg.RIVER_ROW + 1]) / 2
    crossing = [e for e in g.edges
                if (nodes[e.from_node].lat - river_lat) * (nodes[e.to_node].lat - river_lat) < 0
                and e.edge_id and not e.to_node == mfg.DEST_NODE and not e.from_node == mfg.DEST_NODE]
    assert crossing, "bridges must exist"
    assert all(e.bridge_flag is True and e.infrastructure_level == "bridge" for e in crossing)
    assert len({frozenset((e.from_node, e.to_node)) for e in crossing}) in (2, 3)
    assert all(e.elevation_confidence in ("Low", "Unknown") for e in crossing)


def test_special_edges_present(fixture):
    g, _, _ = fixture
    tunnels = [e for e in g.edges if e.tunnel_flag is True]
    assert tunnels and all(e.elevation_confidence in ("Low", "Unknown") for e in tunnels)
    assert any(e.infrastructure_level == "lower-level" and e.road_type == "lower-level" for e in g.edges)
    # one-way for vehicles: a driving edge whose reverse lacks driving
    drive = {(e.from_node, e.to_node) for e in g.edges if "driving" in e.travel_mode}
    assert any((b, a) not in drive for a, b in drive)


def test_mixed_known_and_unknown_attributes(fixture):
    g, _, _ = fixture
    assert {n.elevation_m == "Unknown" for n in g.nodes} == {True, False}
    assert {e.road_type == "Unknown" for e in g.edges} == {True, False}
    assert {e.pedestrian_access == "Unknown" for e in g.edges} == {True, False}
    assert any(e.elevation_start == "Unknown" for e in g.edges)


def test_buildings_layer_has_height_class_mixture(fixture):
    _, _, layers = fixture
    classes = {f["properties"]["Building_Height_Class"] for f in layers["buildings"]["features"]}
    assert geo.UNKNOWN in classes and len(classes) >= 3
    for f in layers["buildings"]["features"]:
        if f["properties"]["Building_Height_Class"] == geo.UNKNOWN:
            assert "height_m" not in f["properties"]


def test_start_reaches_destination_only_via_bridge(fixture):
    g, _, _ = fixture
    adj: dict[str, list] = {}
    for e in g.edges:
        adj.setdefault(e.from_node, []).append(e)

    def reach(skip_bridges: bool) -> bool:
        seen, q = {mfg.START_NODE}, deque([mfg.START_NODE])
        while q:
            for e in adj.get(q.popleft(), []):
                if "walking" not in e.travel_mode or (skip_bridges and e.bridge_flag is True):
                    continue
                if e.to_node not in seen:
                    seen.add(e.to_node)
                    q.append(e.to_node)
        return mfg.DEST_NODE in seen

    assert reach(False) and not reach(True)
    places = {f["properties"]["role"]: f["properties"]["name"]
              for f in read_artifact(mfg.FIXTURE_ROOT)[2]["fixture_places"]["features"]
              if f["properties"]["role"] in ("start", "destination")}
    assert "SIMULATED" in places["start"] and "SIMULATED" in places["destination"]


def test_runtime_loader_loads_fixture():
    g = Graph.load(mfg.FIXTURE_ROOT)
    assert g.status == OK and g.num_nodes > 50
    assert g.snap(41.8819, -87.6278).distance_m < 5


def test_fallbacks_merge_preserves_other_entries(tmp_path):
    p = tmp_path / "fallbacks.json"
    p.write_text(json.dumps({"cdp_centerlines": {"status": "missing"}}), encoding="utf-8")
    mfg.update_fallbacks(p, "v1", tmp_path)
    mfg.update_fallbacks(p, "v2", tmp_path)  # idempotent re-merge
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["cdp_centerlines"] == {"status": "missing"}
    entry = data[mfg.FALLBACK_KEY]
    assert entry["graph_version"] == "v2" and entry["classification"] == "Simulated"
    assert "SIMULATED" in entry["limitation"]
    p.write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError):
        mfg.update_fallbacks(p, "v3", tmp_path)
    assert p.read_text(encoding="utf-8") == "[1]"


def test_committed_fallbacks_documents_fixture():
    data = json.loads(mfg.FALLBACKS_PATH.read_text(encoding="utf-8"))
    assert data[mfg.FALLBACK_KEY]["classification"] == "Simulated"


def test_default_graph_falls_back_to_fixture_with_clear_report(tmp_path, monkeypatch):
    monkeypatch.delenv(runtime.GRAPH_ROOT_ENV, raising=False)
    monkeypatch.setattr(runtime, "DEFAULT_GRAPH_ROOT", tmp_path)  # no real artifact
    c = TestClient(create_app(Settings()))
    h = c.get("/api/health").json()
    assert h["graph_status"] == FALLBACK and h["graph_fallback"]["simulated"] is True
    st = c.get("/api/graph/status").json()
    assert st["status"] == FALLBACK and "SIMULATED" in st["warnings"][0]
    assert st["fallback"]["classification"] == "Simulated"


def test_no_fixture_when_env_root_set(tmp_path, monkeypatch):
    monkeypatch.setenv(runtime.GRAPH_ROOT_ENV, str(tmp_path))
    c = TestClient(create_app(Settings()))
    st = c.get("/api/graph/status").json()
    assert st["status"] == INSUFFICIENT_DATA and st["fallback"] is None


def test_real_default_artifact_beats_fixture(tmp_path, monkeypatch):
    monkeypatch.delenv(runtime.GRAPH_ROOT_ENV, raising=False)
    mfg.make_fixture(tmp_path, None)  # stands in for a real artifact at the default root
    monkeypatch.setattr(runtime, "DEFAULT_GRAPH_ROOT", tmp_path)
    c = TestClient(create_app(Settings()))
    h = c.get("/api/health").json()
    assert h["graph_status"] == OK and h["graph_fallback"] is None
