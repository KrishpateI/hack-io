from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.graph import Edge, Graph as GraphModel, Node
from app.routing.graph import FALLBACK, INSUFFICIENT_DATA, OK, Graph, decode_geometry
from app.routing.runtime import GRAPH_ROOT_ENV
from app.services.graph_artifact import write_artifact

TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)


def _model(extra: float = 0.0) -> GraphModel:
    # a(-87.65,41.88) -- b(-87.64,41.88) -- c(-87.64,41.89); one-way a->b, b<->c
    nodes = [
        Node(node_id="a", lat=41.88, lon=-87.65, kind="intersection"),
        Node(node_id="b", lat=41.88, lon=-87.64, kind="intersection"),
        Node(node_id="c", lat=41.89, lon=-87.64, kind="intersection"),
    ]

    def e(i, f, t, geom, d):
        return Edge(
            edge_id=i, from_node=f, to_node=t, geometry=geom, distance_meters=d + extra,
            estimated_travel_time_seconds=10.0, travel_mode=["walking"], source="osm",
            data_timestamp=TS, classification="Static",
        )

    return GraphModel.build(nodes, [
        e("e1", "a", "b", "LINESTRING(-87.65 41.88, -87.64 41.88)", 830),
        e("e2", "b", "c", "LINESTRING(-87.64 41.88, -87.64 41.89)", 1110),
        e("e3", "c", "b", "undecodable", 1110),  # falls back to endpoint segment
    ])


def _write(tmp_path, extra=0.0, day=15):
    return write_artifact(tmp_path, _model(extra), built_at=datetime(2026, 3, day, tzinfo=timezone.utc))


def test_load_ok_and_adjacency(tmp_path):
    v = _write(tmp_path)
    g = Graph.load(tmp_path)
    assert g.status == OK and g.version == v and g.num_nodes == 3 and g.num_edges == 3
    a, b = g.node_index["a"], g.node_index["b"]
    assert [g.edge_ids[i] for i in g.out_edges(a)] == ["e1"]
    assert sorted(g.edge_ids[i] for i in g.out_edges(b)) == ["e2"]
    assert g._tree is None  # lazy


def test_snap_nearest_and_mask(tmp_path):
    _write(tmp_path)
    g = Graph.load(tmp_path)
    r = g.snap(41.8801, -87.6451)
    assert r.edge_id == "e1" and r.node_id == "a" and r.distance_m < 20
    assert g._tree is not None
    mask = np.array([False, True, True])
    r2 = g.snap(41.8801, -87.6451, mask)
    assert r2.edge_id in {"e2", "e3"}
    assert g.snap(41.88, -87.65, np.zeros(3, dtype=bool)) is None


def test_decode_geometry():
    assert decode_geometry("LINESTRING(-87.6 41.8, -87.5 41.9)") == [(-87.6, 41.8), (-87.5, 41.9)]
    assert decode_geometry("[[-87.6,41.8],[-87.5,41.9]]") == [(-87.6, 41.8), (-87.5, 41.9)]
    assert decode_geometry("abc") is None and decode_geometry("") is None


def test_checksum_failure_falls_back_to_previous(tmp_path):
    v1 = _write(tmp_path, 0.0, day=14)
    v2 = _write(tmp_path, 5.0, day=15)
    (tmp_path / "graphs" / v2 / "edges.jsonl.gz").write_bytes(b"corrupt")
    g = Graph.load(tmp_path)
    assert g.status == FALLBACK and g.version == v1
    assert v2 in g.failed_versions and any("checksum" in w for w in g.warnings)


def test_insufficient_data_when_none_loads(tmp_path):
    assert Graph.load(tmp_path).status == INSUFFICIENT_DATA
    v = _write(tmp_path)
    (tmp_path / "graphs" / v / "nodes.jsonl.gz").write_bytes(b"x")
    g = Graph.load(tmp_path)
    assert g.status == INSUFFICIENT_DATA and not g.loaded and g.snap(41.88, -87.65) is None


def test_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv(GRAPH_ROOT_ENV, str(tmp_path))
    v = _write(tmp_path)
    c = TestClient(create_app(Settings()))
    assert c.get("/api/regions").json()["regions"][0]["id"] == "chicago"
    meta = c.get("/api/regions/chicago/metadata").json()
    assert meta["graph_version"] == v and meta["crs"] == "EPSG:4326"
    assert meta["bounds"]["north"] == 41.91 and len(meta["neighborhoods"]) >= 8
    st = c.get("/api/graph/status").json()
    # 3 outdoor nodes plus the merged SIMULATED indoor building (Req 21)
    from app.routing.indoor import build_indoor_parts, load_spec

    n_indoor = len(build_indoor_parts(load_spec(), Graph.load(tmp_path).model.nodes)[0])
    assert st["status"] == OK and st["node_count"] == 3 + n_indoor
    assert c.get("/api/health").json()["graph_version"] == v


def test_endpoints_without_graph(tmp_path, monkeypatch):
    monkeypatch.setenv(GRAPH_ROOT_ENV, str(tmp_path))
    c = TestClient(create_app(Settings()))
    assert c.get("/api/graph/status").json()["status"] == INSUFFICIENT_DATA
    assert c.get("/api/health").json()["graph_version"] == "unbuilt"
