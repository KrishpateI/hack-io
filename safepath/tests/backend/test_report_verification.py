"""Report verification lifecycle and moderation (task 9.3; Req 24, 28.6, 16.3, 14.3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import InProcessPublisher, MemoryRepository, Role
from app.adapters.ports import Conflict
from app.api.ratelimit import InMemoryRateLimiter
from app.auth.audit import AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph
from app.routing.indoor import merge_indoor_runtime
from app.services.overlays import RepositoryContextProvider
from app.services.wiring import get_overlay_service

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
POINT = {"latitude": 41.8708, "longitude": -87.6505}
DEST = {"latitude": 41.892, "longitude": -87.6278}
GRAPH = merge_indoor_runtime(Graph.load(FIXTURE_ROOT))
CORROBORATED_TEXT = "Corroborated by community \u2014 not officially verified"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("SAFEPATH_ALIAS_SECRET", "test-alias-secret")
    repo = MemoryRepository()
    auth = DemoTokenAuthProvider("t-secret", roles={
        "mod": Role.MODERATOR, "off": Role.AUTHORIZED_OFFICIAL})
    app = create_app(Settings())
    app.state.repository = repo
    app.state.auth = auth
    app.state.audit = AuditWriter(repo)
    app.state.event_bus = None
    app.state.publisher = InProcessPublisher()
    app.state.routing_context_provider = RepositoryContextProvider(repo)
    app.state.routing_service = None
    app.state.graph = GRAPH
    app.state.rate_limiter = InMemoryRateLimiter()
    client = TestClient(app, raise_server_exceptions=False)
    return client, app, repo, auth


def h(auth, sub):
    return {"Authorization": f"Bearer {auth.issue_token(sub)[0]}"}


def make_incident(client, auth, simulated=True):
    r = client.post("/api/incidents", json={"incident_type": "flood", "source": "t",
                                            "simulation_flag": simulated}, headers=h(auth, "mod"))
    assert r.status_code == 201, r.text
    return r.json()["incident_id"]


def make_report(client, auth, iid, sub="rep"):
    r = client.post("/api/reports", json={"incident_id": iid, "category": "blocked road",
                                          "description": "x", **POINT}, headers=h(auth, sub))
    assert r.status_code == 201, r.text
    return r.json()["report_id"]


def route(client, iid):
    return client.post("/api/routes/calculate", json={
        "incident_id": iid, "origin": POINT, "destination": DEST, "travel_mode": "walking",
        "mobility_profile": "standard", "alternatives": 1})


def vote(client, auth, rid, sub, kind="corroborate"):
    return client.post(f"/api/reports/{rid}/{kind}", headers=h(auth, sub))


def moderate(client, auth, rid, sub, action, **kw):
    return client.post(f"/api/reports/{rid}/moderate", json={"action": action, **kw}, headers=h(auth, sub))


def row_of(repo, iid, rid):
    return repo.get("reports", {"pk": iid, "sk": rid})


# ------------------------------------------------------------- integration bug fix
def test_report_flows_through_real_incident_to_list_and_routing_context(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    assert repo.get("reports", {"pk": iid, "sk": rid}) is not None
    listed = client.get("/api/reports", params={"incident_id": iid}).json()["reports"]
    assert [r["report_id"] for r in listed] == [rid]
    ctx = app.state.routing_context_provider.load(iid, datetime.now(timezone.utc))
    assert [r.report_id for r in ctx.reports] == [rid]
    # unknown incident is still rejected
    r = client.post("/api/reports", json={"incident_id": "nope", "category": "flooding", **POINT},
                    headers=h(auth, "rep"))
    assert r.status_code == 404


# ------------------------------------------------------------------ voting
def test_corroboration_threshold_and_never_officially_verified(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    a = vote(client, auth, rid, "v1")
    assert a.status_code == 200 and a.json()["verification_status"] == "Unverified"
    assert a.json()["confirmations"] == 1
    b = vote(client, auth, rid, "v2").json()
    assert b["verification_status"] == "Corroborated" and b["status_label"] == CORROBORATED_TEXT
    assert b["source_label"] == "Community report"
    for sub in ("v3", "v4", "v5"):
        assert vote(client, auth, rid, sub).json()["verification_status"] == "Corroborated"
    assert vote(client, auth, rid, "v6").json()["confirmations"] == 6
    hist = b["status_history"]
    assert [x["status"] for x in hist] == ["Unverified", "Corroborated"]
    assert hist[1]["previous_status"] == "Unverified" and hist[1]["actor_alias"]
    assert "v2" not in str(b)
    assert not repo.query("overlays", {"pk": iid})  # no closure from corroboration
    assert get_overlay_service(app).overlay_version(iid) == 0


def test_guest_can_vote(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    g = {"Authorization": f"Bearer {auth.issue_guest().token}"}
    assert client.post(f"/api/reports/{rid}/dispute", headers=g).status_code == 200


def test_own_report_vote_is_403_and_duplicate_is_409(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    assert vote(client, auth, rid, "rep").status_code == 403
    assert vote(client, auth, rid, "rep", "dispute").status_code == 403
    assert vote(client, auth, rid, "v1").status_code == 200
    assert vote(client, auth, rid, "v1").status_code == 409
    assert vote(client, auth, rid, "v1", "dispute").status_code == 409  # one vote per user
    assert row_of(repo, iid, rid)["confirmations"] == 1 and row_of(repo, iid, rid)["disputes"] == 0
    assert vote(client, auth, "missing", "v9").status_code == 404
    assert client.post(f"/api/reports/{rid}/corroborate").status_code == 401


def test_dispute_path_and_corroborated_to_disputed(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    r1 = make_report(client, auth, iid, "rep1")
    assert vote(client, auth, r1, "d1", "dispute").json()["verification_status"] == "Unverified"
    d = vote(client, auth, r1, "d2", "dispute").json()
    assert d["verification_status"] == "Disputed" and d["disputes"] == 2
    r2 = make_report(client, auth, iid, "rep2")
    vote(client, auth, r2, "v1"); vote(client, auth, r2, "v2")
    assert row_of(repo, iid, r2)["verification_status"] == "Corroborated"
    vote(client, auth, r2, "d1", "dispute")
    out = vote(client, auth, r2, "d2", "dispute").json()
    assert out["verification_status"] == "Disputed"
    assert [x["status"] for x in out["status_history"]] == ["Unverified", "Corroborated", "Disputed"]


def test_vote_rate_limit_30_per_10_minutes_returns_429(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    ids = [make_report(client, auth, iid, f"rep{i}") for i in range(31)]
    for rid in ids[:30]:
        assert vote(client, auth, rid, "busy").status_code == 200
    r = vote(client, auth, ids[30], "busy")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


# --------------------------------------------------------------- moderation
def test_moderate_authz_matrix(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    guest = {"Authorization": f"Bearer {auth.issue_guest().token}"}
    assert client.post(f"/api/reports/{rid}/moderate", json={"action": "verify"}).status_code == 401
    assert client.post(f"/api/reports/{rid}/moderate", json={"action": "verify"},
                       headers=guest).status_code == 403
    assert moderate(client, auth, rid, "usr", "verify").status_code == 403
    assert moderate(client, auth, rid, "usr", "resolve").status_code == 403
    assert row_of(repo, iid, rid)["verification_status"] == "Unverified"
    assert moderate(client, auth, rid, "mod", "bogus").status_code == 400
    assert moderate(client, auth, "missing", "mod", "verify").status_code == 404


def test_demo_moderator_verify_creates_simulated_closure_and_changes_routing(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth, simulated=True)
    base = route(client, iid).json()
    ids = [p["edge_id"] for p in base["routes"][0]["edge_penalties"]]
    victim = ids[len(ids) // 2]
    rid = make_report(client, auth, iid)
    row = row_of(repo, iid, rid)
    repo.put("reports", {**row, "affected_edge_ids": [victim]})
    # unverified reports never close anything
    assert route(client, iid).json()["routes"][0]["excluded_edges_count"] == 0
    assert get_overlay_service(app).overlay_version(iid) == 0

    r = moderate(client, auth, rid, "mod", "verify", reason="seen on camera")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["verification_status"] == "Officially_Verified"
    assert j["source_label"] == "Simulated moderator verification"
    assert j["overlay_version"] == 1
    last = j["status_history"][-1]
    assert last["actor_role"] == "moderator" and last["reason"] == "seen on camera"
    assert last["previous_status"] == "Unverified" and last["actor_alias"] and last["at"]
    ovl = repo.query("overlays", {"pk": iid})
    assert len(ovl) == 1 and ovl[0]["edge_ids"] == [victim] and ovl[0]["simulated"] is True
    assert ovl[0]["source_report_id"] == rid and ovl[0]["status"] == "active"
    after = route(client, iid).json()
    assert victim not in [p["edge_id"] for p in after["routes"][0]["edge_penalties"]]
    assert after["routes"][0]["excluded_edges_count"] >= 1
    # audit row
    day = datetime.now(timezone.utc).date().isoformat()
    assert any(e.action == "moderation:verify" and e.target_id == rid and e.actor_role == "moderator"
               for e in AuditWriter(repo).entries(day))
    # verifying again is an invalid transition
    assert moderate(client, auth, rid, "mod", "verify").status_code == 409

    # resolve removes the closure and bumps the version again
    res = moderate(client, auth, rid, "off", "resolve")
    assert res.status_code == 200 and res.json()["verification_status"] == "Resolved"
    assert res.json()["overlay_version"] == 2
    assert repo.query("overlays", {"pk": iid})[0]["status"] == "removed"
    restored = route(client, iid).json()
    assert [p["edge_id"] for p in restored["routes"][0]["edge_penalties"]] == ids
    assert moderate(client, auth, rid, "mod", "resolve").status_code == 409
    assert vote(client, auth, rid, "v1").status_code == 409  # resolved reports take no votes

    types = [e.type for e in app.state.event_bus.since(iid)]
    assert types.count("restriction_added") == 1 and types.count("restriction_removed") == 1
    changes = [e.payload for e in app.state.event_bus.since(iid) if e.type == "report_status_changed"]
    assert [c["verification_status"] for c in changes] == ["Officially_Verified", "Resolved"]


def test_real_incident_only_official_may_verify(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth, simulated=False)
    rid = make_report(client, auth, iid)
    assert moderate(client, auth, rid, "mod", "verify").status_code == 403
    assert row_of(repo, iid, rid)["verification_status"] == "Unverified"
    ok = moderate(client, auth, rid, "off", "verify")
    assert ok.status_code == 200 and ok.json()["verification_status"] == "Officially_Verified"
    assert ok.json()["source_label"] == "Authorized official verification"
    # moderators may resolve
    assert moderate(client, auth, rid, "mod", "resolve").json()["verification_status"] == "Resolved"


def test_verify_after_corroboration_and_dispute_keeps_history(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    vote(client, auth, rid, "d1", "dispute"); vote(client, auth, rid, "d2", "dispute")
    out = moderate(client, auth, rid, "mod", "verify").json()
    assert [x["status"] for x in out["status_history"]] == ["Unverified", "Disputed", "Officially_Verified"]
    assert out["status_history"][-1]["previous_status"] == "Disputed"


def test_expired_report_cannot_be_moderated_and_expiry_removes_closure(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    repo.put("reports", {**row_of(repo, iid, rid), "affected_edge_ids": ["e1"]})
    assert moderate(client, auth, rid, "mod", "verify").status_code == 200
    row = row_of(repo, iid, rid)
    repo.put("reports", {**row, "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()})
    assert moderate(client, auth, rid, "mod", "resolve").status_code == 409
    client.get("/api/reports", params={"incident_id": iid})  # sweep
    assert row_of(repo, iid, rid)["verification_status"] == "Expired"
    assert repo.query("overlays", {"pk": iid})[0]["status"] == "removed"
    assert get_overlay_service(app).overlay_version(iid) == 2
    assert client.get(f"/api/reports/{rid}").json()["verification_status"] == "Expired"


# -------------------------------------------------------------- concurrency
def test_concurrent_update_is_retried_and_applied_sequentially(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    client.post(f"/api/reports/{rid}/corroborate", headers=h(auth, "warm"))  # builds services
    real = repo.transact
    state = {"raced": False}

    def racing(ops):
        if not state["raced"]:
            state["raced"] = True  # a competing writer commits first
            row = row_of(repo, iid, rid)
            repo.put("report_votes", {"pk": rid, "sk": "rival", "vote": "confirm"})
            repo.update_cas("reports", {"pk": iid, "sk": rid}, row["version"],
                            {"confirmations": row["confirmations"] + 1})
        return real(ops)

    repo.transact = racing
    out = vote(client, auth, rid, "v1").json()
    repo.transact = real
    assert state["raced"]
    assert out["confirmations"] == 3 and out["verification_status"] == "Corroborated"


def test_persistent_conflict_and_stale_expected_version_return_409(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    stale = row_of(repo, iid, rid)["version"]
    assert moderate(client, auth, rid, "mod", "verify", expected_version=stale + 5).status_code == 409
    real = repo.transact

    def always_conflict(ops):
        raise Conflict("forced")

    repo.transact = always_conflict
    assert vote(client, auth, rid, "v1").status_code == 409
    assert moderate(client, auth, rid, "mod", "verify").status_code == 409
    repo.transact = real
    row = row_of(repo, iid, rid)
    assert row["verification_status"] == "Unverified" and row["confirmations"] == 0
    assert moderate(client, auth, rid, "mod", "verify", expected_version=stale).status_code == 200


def test_events_published_for_status_change(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    seen = []
    app.state.publisher.subscribe(iid, seen.append)
    vote(client, auth, rid, "v1"); vote(client, auth, rid, "v2")
    assert [e.type for e in seen] == ["report_voted", "report_status_changed"]
    assert seen[1].payload["verification_status"] == "Corroborated"
    assert seen[1].payload["previous_status"] == "Unverified"
