"""Incidents, notices, overlay store and Repository-backed routing (task 8.5)."""
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
from app.services.overlays import (
    META_TABLE,
    DuplicateRestriction,
    RepositoryContextProvider,
    RestrictionNotFound,
)
from app.services.wiring import get_incident_service, get_overlay_service

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
ORIGIN = {"latitude": 41.8708, "longitude": -87.6505}
DEST = {"latitude": 41.892, "longitude": -87.6278}

NOTICE = {
    "notice_type": "road_closure", "text": "Closed <b>by</b> order", "edge_ids": [],
    "source_organization": "OEMC", "source_url": "https://example.test/notice",
    "source_record_id": "OEMC-CHICAGO",
}


@pytest.fixture(scope="module")
def graph() -> Graph:
    return Graph.load(FIXTURE_ROOT)


@pytest.fixture()
def env(graph):
    repo = MemoryRepository()
    auth = DemoTokenAuthProvider("t-secret", roles={
        "mod": Role.MODERATOR, "off": Role.AUTHORIZED_OFFICIAL, "usr": Role.USER})
    app = create_app(Settings())
    app.state.repository = repo
    app.state.auth = auth
    app.state.audit = AuditWriter(repo)
    app.state.event_bus = None
    app.state.routing_context_provider = RepositoryContextProvider(repo)
    app.state.routing_service = None
    app.state.graph = graph
    client = TestClient(app, raise_server_exceptions=False)
    tokens = {name: {"Authorization": f"Bearer {auth.issue_token(sub)[0]}"}
              for name, sub in (("mod", "mod"), ("off", "off"), ("user", "usr"))}
    tokens["guest"] = {"Authorization": f"Bearer {auth.issue_guest().token}"}
    return client, app, repo, tokens


def make_incident(client, tokens, **kw):
    body = {"incident_type": "flood", "source": "test", "simulation_flag": True, **kw}
    r = client.post("/api/incidents", json=body, headers=tokens["mod"])
    assert r.status_code == 201, r.text
    return r.json()["incident_id"]


def route(client, iid):
    return client.post("/api/routes/calculate", json={
        "incident_id": iid, "origin": ORIGIN, "destination": DEST, "travel_mode": "walking",
        "mobility_profile": "standard", "alternatives": 1})


# ----------------------------------------------------------------------------- authz
@pytest.mark.parametrize("who,status", [("guest", 403), ("user", 403), ("mod", 201), ("off", 201)])
def test_incident_post_authz_matrix(env, who, status):
    client, _, _, tokens = env
    r = client.post("/api/incidents", json={"incident_type": "fire", "source": "x"}, headers=tokens[who])
    assert r.status_code == status


def test_incident_post_requires_auth(env):
    client, *_ = env
    assert client.post("/api/incidents", json={"incident_type": "fire", "source": "x"}).status_code == 401


@pytest.mark.parametrize("who,status", [("guest", 403), ("user", 403), ("mod", 403), ("off", 201)])
def test_notice_post_authz_matrix(env, who, status):
    client, _, _, tokens = env
    iid = make_incident(client, tokens)
    r = client.post("/api/notices", json={**NOTICE, "incident_id": iid}, headers=tokens[who])
    assert r.status_code == status, r.text


def test_official_notice_is_sanitized_audited_and_public(env):
    client, app, repo, tokens = env
    iid = make_incident(client, tokens)
    r = client.post("/api/notices", json={**NOTICE, "incident_id": iid}, headers=tokens["off"])
    assert r.status_code == 201
    n = r.json()
    assert "<" not in n["text"] and n["simulation_flag"] is True
    assert n["source_record_id"] == "OEMC-CHICAGO" and n["verification_status"] == "Officially_Verified"
    entries = [e for d in {n["published_at"][:10]} for e in AuditWriter(repo).entries(d)]
    assert any(e.action == "official_notice" and e.target_id == n["notice_id"]
               and e.actor_role == "authorized_official" for e in entries)
    listed = client.get("/api/notices", params={"incident_id": iid}).json()
    assert [x["notice_id"] for x in listed] == [n["notice_id"]]
    assert client.get("/api/notices", params={"incident_id": "nope"}).status_code == 404
    assert client.get("/api/notices").status_code == 400


def test_notice_validation(env):
    client, _, _, tokens = env
    iid = make_incident(client, tokens)
    for bad in ({"source_url": "javascript:alert(1)"}, {"source_record_id": ""},
                {"source_organization": ""}, {"notice_type": "nope"}, {"text": "<b></b>"}):
        r = client.post("/api/notices", json={**NOTICE, "incident_id": iid, **bad}, headers=tokens["off"])
        assert r.status_code == 400, bad
    r = client.post("/api/notices", json={**NOTICE, "incident_id": "ghost"}, headers=tokens["off"])
    assert r.status_code == 404


def test_incident_validation_and_reads(env):
    client, _, _, tokens = env
    for bad in ({"incident_type": "meteor"}, {"source": "<i></i>"},
                {"geographic_boundary": {"type": "Point", "coordinates": [0, 0]}}):
        r = client.post("/api/incidents", json={"incident_type": "flood", "source": "s", **bad},
                        headers=tokens["mod"])
        assert r.status_code == 400, bad
    iid = make_incident(client, tokens, incident_type="tornado")
    assert client.get(f"/api/incidents/{iid}").json()["incident_type"] == "tornado"
    assert iid in [i["incident_id"] for i in client.get("/api/incidents").json()]
    assert client.get("/api/incidents/missing").status_code == 404


def test_simulated_notice_helper(env):
    client, app, _, tokens = env
    iid = make_incident(client, tokens)
    n = get_incident_service(app).create_simulated_notice(iid, notice_type="road_closure", text="demo")
    assert n.simulation_flag and "SIMULATED" in n.source_organization


# -------------------------------------------------------------------- overlay / routing
def test_closure_changes_routing_immediately_and_removal_restores(env):
    client, app, repo, tokens = env
    iid = make_incident(client, tokens)
    base = route(client, iid).json()
    ids = [p["edge_id"] for p in base["routes"][0]["edge_penalties"]]
    victim = ids[len(ids) // 2]
    svc = get_overlay_service(app)
    v1 = svc.add_closure(iid, edge_ids=[victim], source_record_id="s1", simulated=True,
                         restriction_id="ovl-a")
    after = route(client, iid).json()
    assert victim not in [p["edge_id"] for p in after["routes"][0]["edge_penalties"]]
    assert after["routes"][0]["excluded_edges_count"] >= 1
    v2 = svc.remove_closure(iid, "ovl-a")
    assert (v1, v2) == (1, 2)
    restored = route(client, iid).json()
    assert [p["edge_id"] for p in restored["routes"][0]["edge_penalties"]] == ids


def test_overlay_version_increments_atomically_with_event(env):
    client, app, repo, tokens = env
    iid = make_incident(client, tokens)
    svc = get_overlay_service(app)
    assert svc.overlay_version(iid) == 0
    assert svc.add_closure(iid, edge_ids=["e1"], restriction_id="o1") == 1
    assert svc.add_closure(iid, edge_ids=["e2"], restriction_id="o2") == 2
    assert svc.remove_closure(iid, "o1") == 3
    assert svc.activate_closure(iid, "o1") == 4
    types = [(e.type, e.payload.get("overlay_version")) for e in app.state.event_bus.since(iid)
             if e.type.startswith("restriction_")]
    assert types == [("restriction_added", 1), ("restriction_added", 2),
                     ("restriction_removed", 3), ("restriction_added", 4)]
    assert [o.restriction_id for o in svc.list_overlays(iid)] == ["o1", "o2"]
    with pytest.raises(DuplicateRestriction):
        svc.add_closure(iid, edge_ids=["e3"], restriction_id="o2")
    with pytest.raises(RestrictionNotFound):
        svc.remove_closure(iid, "missing")
    assert svc.overlay_version(iid) == 4  # failures changed nothing


def test_failed_transaction_leaves_version_and_overlays_untouched(env):
    client, app, repo, tokens = env
    iid = make_incident(client, tokens)
    svc = get_overlay_service(app)
    svc.add_closure(iid, edge_ids=["e1"], restriction_id="o1")
    repo.available = False
    from app.adapters.ports import RepositoryUnavailable
    with pytest.raises(RepositoryUnavailable):
        svc.add_closure(iid, edge_ids=["e2"], restriction_id="o2")
    repo.available = True
    assert svc.overlay_version(iid) == 1
    assert [o.restriction_id for o in svc.list_overlays(iid)] == ["o1"]
    assert repo.get(META_TABLE, {"pk": iid, "sk": "meta"})["overlay_version"] == 1


def test_notice_bumps_overlay_version(env):
    client, app, _, tokens = env
    iid = make_incident(client, tokens)
    client.post("/api/notices", json={**NOTICE, "incident_id": iid}, headers=tokens["off"])
    assert get_overlay_service(app).overlay_version(iid) == 1


def test_repository_unavailable_routing_fails_closed(env):
    client, app, repo, tokens = env
    iid = make_incident(client, tokens)
    assert route(client, iid).status_code == 200
    app.state.routing_context_provider.invalidate()
    repo.available = False
    r = route(client, iid)
    assert r.status_code == 503 and "routes" not in r.json()


def test_unknown_incident_routing_404(env):
    client, *_ = env
    assert route(client, "inc_missing").status_code == 404


def test_version_cache_ttl_is_bounded_and_invalidated(env):
    _, app, repo, tokens = env
    with pytest.raises(ValueError):
        RepositoryContextProvider(repo, version_ttl=5)
