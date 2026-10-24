"""Example tests for RoutingService and POST /api/routes/calculate (task 6.12).

Uses the SIMULATED fixture graph (tests/fixtures/graph).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.domain import Incident, IncidentType, OfficialNotice, Restriction
from app.routing.cost import WeightsState
from app.routing.graph import Graph
from app.routing.service import InMemoryContextProvider

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
NOW = datetime.now(timezone.utc)
INC = "inc-1"

ORIGIN = {"latitude": 41.8708, "longitude": -87.6505}  # fx-uic
DEST = {"latitude": 41.892, "longitude": -87.6278}  # fx-sim-destination
BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[-87.67, 41.86], [-87.61, 41.86], [-87.61, 41.90], [-87.67, 41.90],
                     [-87.67, 41.86]]],
}


def make_incident(kind=IncidentType.FLOOD, boundary=None, iid=INC) -> Incident:
    return Incident(incident_id=iid, incident_type=kind, source="test", created_at=NOW,
                    updated_at=NOW, simulation_flag=True, geographic_boundary=boundary)


def make_notice(ntype, nid="n1", **kw) -> OfficialNotice:
    base = dict(notice_id=nid, incident_id=INC, notice_type=ntype, text="official text",
                source_organization="OEMC", source_url="https://example.test",
                source_record_id="src-" + nid, effective_at=NOW - timedelta(hours=1),
                published_at=NOW, retrieved_at=NOW, verification_status="Officially_Verified",
                simulation_flag=True)
    base.update(kw)
    return OfficialNotice(**base)


def body(**kw):
    b = {"incident_id": INC, "origin": ORIGIN, "destination": DEST, "travel_mode": "walking",
         "mobility_profile": "standard", "alternatives": 1}
    b.update(kw)
    return b


@pytest.fixture(scope="module")
def graph() -> Graph:
    g = Graph.load(FIXTURE_ROOT)
    assert g.loaded
    return g


@pytest.fixture()
def env(graph):
    app = create_app(Settings.from_env())
    app.state.graph = graph
    provider = InMemoryContextProvider()
    provider.add_incident(make_incident())
    app.state.routing_context_provider = provider
    app.state.routing_service = None
    return TestClient(app, raise_server_exceptions=False), provider, app


def post(client, **kw):
    return client.post("/api/routes/calculate", json=body(**kw))


# ------------------------------------------------------------------------- happy path
def test_route_options_available_contract(env, graph):
    client, _, _ = env
    r = post(client)
    assert r.status_code == 200
    data = r.json()
    assert data["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE"
    assert data["route_count"] == 1 and data["graph_version"] == graph.version
    assert data["data_timestamp"] and data["calculated_at"]
    route = data["routes"][0]
    for key in ("route_id", "geometry", "distance_meters", "estimated_duration_seconds",
                "hazard_flags", "excluded_edges_count", "uncertainty_level", "policy_outcome",
                "sources", "calculated_at", "status", "restrictions_considered"):
        assert key in route
    assert route["status"] == "ok" and route["label"] == "algorithm-generated"
    geom = route["geometry"]
    assert geom["type"] == "LineString" and len(geom["coordinates"]) >= 2
    # geometry is built from graph edge geometries: it starts and ends at graph nodes
    node_pts = {(round(n.lon, 6), round(n.lat, 6)) for n in graph.model.nodes}
    assert tuple(round(c, 6) for c in geom["coordinates"][0]) in node_pts
    assert tuple(round(c, 6) for c in geom["coordinates"][-1]) in node_pts
    assert route["distance_meters"] > 0 and route["estimated_duration_seconds"] > 0


def test_alternatives_are_distinct(env):
    client, _, _ = env
    data = post(client, alternatives=3).json()
    assert 2 <= data["route_count"] <= 3
    sigs = {tuple(p["edge_id"] for p in r["edge_penalties"]) for r in data["routes"]}
    assert len(sigs) == len(data["routes"]) == data["route_count"]


def test_closure_removes_edge_and_changes_route(env):
    client, provider, _ = env
    base = post(client).json()["routes"][0]
    ids = [p["edge_id"] for p in base["edge_penalties"]]
    victim = ids[len(ids) // 2]
    provider.add_overlay(Restriction(restriction_id="cl-1", incident_id=INC, kind="closure",
                                     edge_ids=[victim], source_record_id="src-cl", simulated=True))
    after = post(client).json()
    route = after["routes"][0]
    assert after["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE"
    new_ids = [p["edge_id"] for p in route["edge_penalties"]]
    assert victim not in new_ids and new_ids != ids
    assert route["excluded_edges_count"] >= 1
    assert any(c["id"] == "cl-1" for c in route["restrictions_considered"])
    assert "src-cl" in route["sources"]


# ----------------------------------------------------------------------- policy outcomes
def test_tornado_shelter_in_place_has_no_geometry(env):
    client, provider, _ = env
    provider.add_incident(make_incident(IncidentType.TORNADO, BOUNDARY, iid="tor-1"))
    r = post(client, incident_id="tor-1")
    assert r.status_code == 200
    data = r.json()
    assert data["policy_outcome"] == "SHELTER_IN_PLACE"
    assert data["route_count"] == 0 and data["explanation"]
    assert all(rt["geometry"]["coordinates"] == [] for rt in data["routes"])
    assert data["routes"][0]["distance_meters"] == 0


def test_official_shelter_notice_overrides_route(env):
    client, provider, _ = env
    provider.add_notice(make_notice("shelter_in_place", text="Stay inside"))
    data = post(client).json()
    assert data["policy_outcome"] == "SHELTER_IN_PLACE"
    assert data["notices"][0]["notice_id"] == "n1"
    assert "src-n1" in data["sources"]
    assert data["routes"][0]["geometry"]["coordinates"] == []


def test_follow_official_instructions(env):
    client, provider, _ = env
    provider.add_notice(make_notice("follow_instructions", text="Await responders"))
    data = post(client).json()
    assert data["policy_outcome"] == "FOLLOW_OFFICIAL_INSTRUCTIONS"
    assert data["route_count"] == 0 and "src-n1" in data["sources"]


def test_unreachable_destination_is_no_verified_route(env, graph):
    client, provider, _ = env
    dest_node = graph.node_index["fx-sim-destination"]
    touching = {graph.edge_ids[i] for i in range(graph.num_edges)
                if int(graph.edge_from[i]) == dest_node or int(graph.edge_to[i]) == dest_node}
    provider.add_overlay(Restriction(restriction_id="cl-d", incident_id=INC, kind="closure",
                                     edge_ids=sorted(touching), source_record_id="src-d",
                                     simulated=True))
    r = post(client)
    assert r.status_code == 200
    data = r.json()
    assert data["policy_outcome"] == "NO_VERIFIED_ROUTE_AVAILABLE"
    assert data["route_count"] == 0 and data["explanation"]
    assert data["routes"][0]["geometry"]["coordinates"] == []
    assert data["routes"][0]["excluded_edges_count"] >= len(touching)


def test_insufficient_data_when_graph_missing(env):
    client, _, app = env
    app.state.graph = Graph()  # nothing loaded
    data = post(client).json()
    assert data["policy_outcome"] == "INSUFFICIENT_DATA"
    assert "graph" in data["missing_data"]


def test_unknown_indoor_origin_is_insufficient_data(env):
    client, _, _ = env
    data = post(client, origin_indoor_node_id="no-such-node").json()
    assert data["policy_outcome"] == "INSUFFICIENT_DATA"
    assert "indoor_graph" in data["missing_data"]


# ------------------------------------------------------------------------------ errors
def test_outside_region_is_400(env):
    client, _, _ = env
    r = post(client, origin={"latitude": 40.0, "longitude": -87.6})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "outside_region"


def test_unknown_incident_is_404(env):
    client, _, _ = env
    r = post(client, incident_id="nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_bad_alternatives_is_400(env):
    client, _, _ = env
    assert post(client, alternatives=4).status_code == 400


def test_invalid_weights_is_503(env, tmp_path):
    client, _, app = env
    app.state.routing_weights = WeightsState(path=tmp_path / "missing.yaml").reload()
    r = post(client)
    assert r.status_code == 503
    assert "routing weights invalid" in r.json()["error"]["message"]


def test_overlay_read_failure_fails_closed(env):
    client, provider, _ = env
    provider.fail_overlays = True
    r = post(client)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "service_unavailable"
    assert "routes" not in r.json()


# --------------------------------------------------------------------------- performance
def test_performance_smoke_p95_under_5s(env):
    client, _, _ = env
    times = []
    for _ in range(100):
        t = time.perf_counter()
        r = post(client, alternatives=2)
        times.append(time.perf_counter() - t)
        assert r.status_code == 200
    times.sort()
    assert times[94] < 5.0
