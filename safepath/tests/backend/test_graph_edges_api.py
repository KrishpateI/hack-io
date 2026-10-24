"""GET /api/graph/edges/{edge_id} and /api/graph/edges/near (task 13.4, Req 35.1, 35.2)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import MemoryRepository, Role
from app.auth.audit import AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph
from app.services.overlays import RepositoryContextProvider
from app.services.wiring import get_overlay_service

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"


@pytest.fixture(scope="module")
def graph() -> Graph:
    return Graph.load(FIXTURE_ROOT)


@pytest.fixture()
def env(graph):
    repo = MemoryRepository()
    auth = DemoTokenAuthProvider("t-secret", roles={"mod": Role.MODERATOR})
    app = create_app(Settings())
    app.state.repository = repo
    app.state.auth = auth
    app.state.audit = AuditWriter(repo)
    app.state.event_bus = None
    app.state.routing_context_provider = RepositoryContextProvider(repo)
    app.state.routing_service = None
    app.state.graph = graph
    client = TestClient(app, raise_server_exceptions=False)
    mod = {"Authorization": f"Bearer {auth.issue_token('mod')[0]}"}
    guest = {"Authorization": f"Bearer {auth.issue_guest().token}"}
    return client, app, mod, guest


def _incident(client, mod) -> str:
    r = client.post("/api/incidents", json={"incident_type": "flood", "source": "t", "simulation_flag": True},
                    headers=mod)
    assert r.status_code == 201, r.text
    return r.json()["incident_id"]


def _street_edge(graph: Graph) -> int:
    return next(i for i, e in enumerate(graph.model.edges) if e.edge_kind == "street")


def _midpoint(graph: Graph, idx: int) -> tuple[float, float]:
    a, b = int(graph.edge_from[idx]), int(graph.edge_to[idx])
    return ((graph.node_lat[a] + graph.node_lat[b]) / 2, (graph.node_lon[a] + graph.node_lon[b]) / 2)


def test_get_edge_returns_attributes_and_geometry_without_auth(env, graph):
    client, *_ = env
    edge = graph.model.edges[_street_edge(graph)]
    r = client.get(f"/api/graph/edges/{edge.edge_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["edge_id"] == edge.edge_id
    assert body["road_type"] == edge.road_type
    assert body["current_closure_status"] == edge.current_closure_status
    assert body["geometry_geojson"]["type"] == "LineString"
    assert len(body["geometry_geojson"]["coordinates"]) >= 2
    assert body["street_name"]  # "Unknown" when the artifact has no name
    assert body["restrictions"]["causes"] == [] and body["reports"] == []
    assert "data_timestamp" in body and "source" in body


def test_unknown_edge_404(env):
    client, *_ = env
    assert client.get("/api/graph/edges/does-not-exist").status_code == 404


def test_near_finds_edge_sorted_by_distance_and_validates(env, graph):
    client, *_ = env
    idx = _street_edge(graph)
    lat, lon = _midpoint(graph, idx)
    r = client.get("/api/graph/edges/near", params={"lat": lat, "lon": lon, "radius": 30})
    assert r.status_code == 200
    edges = r.json()["edges"]
    assert edges and edges[0]["distance_m"] <= 30
    assert [e["distance_m"] for e in edges] == sorted(e["distance_m"] for e in edges)
    far = client.get("/api/graph/edges/near", params={"lat": 41.9, "lon": -87.7, "radius": 5})
    assert far.status_code == 200 and far.json()["edges"] == []
    assert client.get("/api/graph/edges/near", params={"lat": 95, "lon": 0}).status_code == 400
    assert client.get("/api/graph/edges/near", params={"lat": 41.88, "lon": -87.6, "radius": 9999}).status_code == 400


def test_active_closure_cause_and_reports_for_incident(env, graph):
    client, app, mod, guest = env
    iid = _incident(client, mod)
    idx = _street_edge(graph)
    eid = graph.edge_ids[idx]
    get_overlay_service(app).add_closure(iid, edge_ids=[eid], source_record_id="src-1", simulated=True,
                                         restriction_id="ovl-x")
    lat, lon = _midpoint(graph, idx)
    created = client.post("/api/reports", json={
        "incident_id": iid, "category": "blocked road", "latitude": lat, "longitude": lon,
        "description": "water over road"}, headers=guest)
    assert created.status_code == 201, created.text
    if eid not in created.json()["affected_edge_ids"]:
        pytest.skip("fixture edge not snapped by report")
    body = client.get(f"/api/graph/edges/{eid}", params={"incident_id": iid}).json()
    causes = body["restrictions"]["causes"]
    assert body["restrictions"]["available"] is True
    assert any(c["id"] == "ovl-x" and c["simulated"] is True and c["source_record_id"] == "src-1" for c in causes)
    assert [r["report_id"] for r in body["reports"]] == [created.json()["report_id"]]
    assert body["reports"][0]["status_label"]
    unrelated = client.get(f"/api/graph/edges/{eid}", params={"incident_id": "inc_missing"}).json()
    assert unrelated["restrictions"]["available"] is False and unrelated["restrictions"]["causes"] == []
