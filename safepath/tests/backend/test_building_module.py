"""Task 11.2: Building_Emergency_Module end-to-end through POST /api/routes/calculate (Req 21)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import MemoryRepository, Role
from app.auth.audit import AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.config import Settings
from app.disasters.base import IncidentContext, get_module
from app.disasters.building import BuildingEmergencyModule, is_stairwell_report
from app.main import create_app
from app.models.domain import Incident, IncidentType
from app.models.routing import LatLon
from app.routing import indoor
from app.routing.graph import Graph
from app.services.overlays import REPORTS_TABLE, RepositoryContextProvider
from app.services.wiring import get_overlay_service

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
START_F3 = "sim-indoor-L3-cor-W"  # next to stairwell A
DEST = {"latitude": 41.8835, "longitude": -87.6350}
ANCHOR = {"latitude": 41.8830, "longitude": -87.6345}


@pytest.fixture(scope="module")
def merged_graph() -> Graph:
    return indoor.merge_indoor_runtime(Graph.load(FIXTURE_ROOT))


@pytest.fixture()
def env(merged_graph):
    repo = MemoryRepository()
    auth = DemoTokenAuthProvider("t-secret", roles={"mod": Role.MODERATOR})
    app = create_app(Settings())
    app.state.repository = repo
    app.state.auth = auth
    app.state.audit = AuditWriter(repo)
    app.state.event_bus = None
    app.state.routing_context_provider = RepositoryContextProvider(repo)
    app.state.routing_service = None
    app.state.graph = merged_graph
    client = TestClient(app, raise_server_exceptions=False)
    mod = {"Authorization": f"Bearer {auth.issue_token('mod')[0]}"}
    r = client.post("/api/incidents", headers=mod, json={
        "incident_type": "building", "source": "test", "simulation_flag": True})
    assert r.status_code == 201, r.text
    return client, app, repo, r.json()["incident_id"]


def route(client, iid, start=START_F3):
    r = client.post("/api/routes/calculate", json={
        "incident_id": iid, "origin": ANCHOR, "destination": DEST, "travel_mode": "walking",
        "mobility_profile": "standard", "alternatives": 1, "origin_indoor_node_id": start})
    assert r.status_code == 200, r.text
    return r.json()


def edge_ids(body):
    return [p["edge_id"] for p in body["routes"][0]["edge_penalties"]]


def stairs_used(body):
    return {e.split("-")[4] for e in edge_ids(body) if e.startswith("sim-indoor-t-stair-")}


def close_stair(app, iid, sid, rid=None):
    ids = [f"sim-indoor-t-stair-{sid}-L3-L2", f"sim-indoor-t-stair-{sid}-L2-L1"]
    get_overlay_service(app).add_closure(
        iid, edge_ids=ids, kind="stairwell_closure", simulated=True,
        source_record_id="SIM-MODERATOR", restriction_id=rid or f"ovl-{sid}")
    app.state.routing_context_provider.invalidate()


def add_report(app, repo, iid, sid, status="Unverified", rid=None):
    now = datetime.now(timezone.utc)
    rid = rid or f"rep-{sid}"
    repo.put(REPORTS_TABLE, {
        "pk": iid, "sk": rid, "report_id": rid, "incident_id": iid, "category": "stairwell_blocked",
        "latitude": 41.883, "longitude": -87.6345, "verification_status": status,
        "affected_edge_ids": [f"sim-indoor-t-stair-{sid}-L3-L2", f"sim-indoor-t-stair-{sid}-L2-L1"],
        "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(),
        "simulated": True})
    app.state.routing_context_provider.invalidate()


# ------------------------------------------------------------------------- tests
def test_module_registered():
    assert isinstance(get_module(IncidentType.BUILDING), BuildingEmergencyModule)
    assert is_stairwell_report("stairwell_blocked") and not is_stairwell_report("smoke")


def test_baseline_route_uses_stairwell_to_exit(env):
    client, _, _, iid = env
    body = route(client, iid)
    assert body["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE"
    ids = edge_ids(body)
    assert stairs_used(body) == {"A"}  # nearest stairwell
    assert any(e.startswith("sim-indoor-t-exit") for e in ids)  # leaves through an exit
    text = str(body).lower()
    assert "safe route" not in text and "guaranteed safe" not in text


def test_confirmed_closure_removes_stairwell_a(env):
    client, app, _, iid = env
    close_stair(app, iid, "A")
    body = route(client, iid)
    assert body["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE"
    assert "A" not in stairs_used(body) and stairs_used(body)
    assert body["routes"][0]["excluded_edges_count"] >= 10  # transitions + landings of stairwell A
    assert not any("stair-A" in e for e in edge_ids(body))


def test_unconfirmed_report_is_penalty_not_restriction(env):
    client, app, repo, iid = env
    base = route(client, iid)
    for sid in "ABC":  # report all three: still routable, penalized and flagged
        add_report(app, repo, iid, sid)
    body = route(client, iid)
    assert body["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE"
    r0 = body["routes"][0]
    assert r0["excluded_edges_count"] == 0
    assert "reported_blocked_stairwell" in r0["hazard_flags"]
    assert r0["estimated_duration_seconds"] == base["routes"][0]["estimated_duration_seconds"]
    pen = sum(sum(p["hazard"].values()) for p in r0["edge_penalties"])
    assert pen >= 200  # two reported-blocked stair legs at w_reported_blocked_stair
    assert "unverified" in " ".join(r0["confidence_limitations"]).lower()


def test_single_report_penalizes_and_reroutes_without_restricting(env):
    client, app, repo, iid = env
    add_report(app, repo, iid, "A")
    body = route(client, iid)
    r0 = body["routes"][0]
    assert r0["excluded_edges_count"] == 0
    if "A" in stairs_used(body):
        assert "reported_blocked_stairwell" in r0["hazard_flags"]
    else:
        assert "reported_blocked_stairwell" not in r0["hazard_flags"]


def test_all_stairwells_closed_yields_no_verified_route(env):
    client, app, _, iid = env
    for sid in "ABC":
        close_stair(app, iid, sid)
    body = route(client, iid)
    assert body["policy_outcome"] == "NO_VERIFIED_ROUTE_AVAILABLE"
    assert body["route_count"] == 0
    # floor 1 origins are not affected by stairwell closures
    assert route(client, iid, "sim-indoor-L1-cor-W")["policy_outcome"] == "ROUTE_OPTIONS_AVAILABLE"


def test_removing_closure_restores_route(env):
    client, app, _, iid = env
    close_stair(app, iid, "A")
    get_overlay_service(app).remove_closure(iid, "ovl-A")
    app.state.routing_context_provider.invalidate()
    assert stairs_used(route(client, iid)) == {"A"}


def test_advisories_are_reports_not_safety_claims(merged_graph):
    now = datetime.now(timezone.utc)
    inc = Incident(incident_id="i", incident_type=IncidentType.BUILDING, source="s",
                   created_at=now, updated_at=now, simulation_flag=True)
    ctx = IncidentContext(incident=inc, now=now, origin=LatLon(latitude=41.883, longitude=-87.6345))
    texts = " ".join(a.text for a in get_module(IncidentType.BUILDING).advisories(ctx)).lower()
    assert "unverified" in texts and "official instructions" in texts
    assert "safe" not in texts
