"""DemoService and /api/demo admin endpoints (task 11.4; Req 40, 39, 37.4, 37.5, 38.3, 22, 28)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import InProcessPublisher, MemoryRepository, Role
from app.api.ratelimit import InMemoryRateLimiter
from app.auth.audit import AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph
from app.routing.indoor import merge_indoor_runtime
from app.services import demo_data as dd
from app.services.overlays import RepositoryContextProvider

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
GRAPH = merge_indoor_runtime(Graph.load(FIXTURE_ROOT))
STAIR = "blocked_stairwell"


def build(demo_mode=True, profile="local"):
    repo = MemoryRepository()
    auth = DemoTokenAuthProvider("t-secret", roles={"mod": Role.MODERATOR, "off": Role.AUTHORIZED_OFFICIAL})
    app = create_app(Settings(demo_mode=demo_mode, demo_autoseed=False, profile=profile))
    app.state.repository = repo
    app.state.auth = auth
    app.state.accounts = None
    app.state.audit = AuditWriter(repo)
    app.state.event_bus = None
    app.state.publisher = InProcessPublisher()
    app.state.routing_context_provider = RepositoryContextProvider(repo)
    app.state.routing_service = None
    app.state.graph = GRAPH
    app.state.rate_limiter = InMemoryRateLimiter()
    from app.auth.accounts import AccountService
    app.state.accounts = AccountService(repo)
    if demo_mode:
        from app.services.demo import DemoService
        app.state.demo_service = DemoService(app)
    return TestClient(app, raise_server_exceptions=False), app, repo, auth


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("SAFEPATH_ALIAS_SECRET", "test-alias-secret")
    return build()


def hdr(auth, sub):
    return {"Authorization": f"Bearer {auth.issue_token(sub)[0]}"}


def guest_hdr(auth):
    return {"Authorization": f"Bearer {auth.issue_guest().token}"}


MOD = "mod"


# --------------------------------------------------------------------------- authorization
def test_authz_matrix(env):
    client, app, repo, auth = env
    paths = [("get", "/api/demo/scenarios"), ("post", "/api/demo/scenarios/flood_loop/start"),
             ("post", "/api/demo/scenarios/flood_loop/steps/closure/apply"),
             ("post", "/api/demo/scenarios/flood_loop/advance"),
             ("post", "/api/demo/scenarios/flood_loop/reset"), ("post", "/api/demo/reset-all")]
    for method, path in paths:
        call = getattr(client, method)
        assert call(path).status_code == 401, path
        assert call(path, headers=guest_hdr(auth)).status_code == 403, path
        assert call(path, headers=hdr(auth, "plain-user")).status_code == 403, path
    r = client.post("/api/demo/scenarios/flood_loop/act", json={"actor": "moderator", "action": "confirm"},
                    headers=hdr(auth, "plain-user"))
    assert r.status_code == 403
    assert client.get("/api/demo/scenarios", headers=hdr(auth, MOD)).status_code == 200
    assert client.get("/api/demo/scenarios", headers=hdr(auth, "off")).status_code == 200


def test_demo_mode_off_is_inert():
    client, app, repo, auth = build(demo_mode=False)
    st = client.get("/api/demo/status")
    assert st.status_code == 200 and st.json()["demo_mode"] is False
    for method, path in [("get", "/api/demo/scenarios"), ("post", "/api/demo/reset-all"),
                         ("post", "/api/demo/sessions"), ("post", "/api/demo/scenarios/flood_loop/start")]:
        r = getattr(client, method)(path, headers=hdr(auth, MOD))
        assert r.status_code == 404, path
    assert repo.query("incidents", {"pk": "all"}) == []


# ------------------------------------------------------------------------------- sessions
def test_sessions_roles_are_server_chosen(env):
    client, app, repo, auth = env
    g = client.post("/api/demo/sessions", json={"kind": "guest"}).json()
    assert g["role"] == "guest" and g["simulation_flag"] is True and "SIMULATED" in g["notice"]
    u = client.post("/api/demo/sessions", json={"kind": "user"}).json()
    assert u["role"] == "user"
    m = client.post("/api/demo/sessions", json={"kind": "moderator"})
    assert m.status_code == 200 and m.json()["role"] == "moderator"
    assert "production" in m.json()["warning"]
    tok = {"Authorization": f"Bearer {m.json()['token']}"}
    assert client.get("/api/demo/scenarios", headers=tok).status_code == 200
    # a client-supplied role is rejected, not honored
    assert client.post("/api/demo/sessions", json={"kind": "user", "role": "moderator"}).status_code == 400
    assert client.post("/api/demo/sessions", json={"kind": "admin"}).status_code == 400


def test_moderator_session_needs_local_profile(monkeypatch):
    monkeypatch.setenv("SAFEPATH_ALIAS_SECRET", "x")
    client, *_ = build(profile="aws")
    assert client.post("/api/demo/sessions", json={"kind": "moderator"}).status_code == 403
    assert client.post("/api/demo/sessions", json={"kind": "guest"}).status_code == 200


# ----------------------------------------------------------------- start / apply / reset cycle
def test_status_and_list(env):
    client, app, repo, auth = env
    st = client.get("/api/demo/status").json()
    assert st["demo_mode"] is True and st["scenarios_seeded"] is False
    r = client.get("/api/demo/scenarios", headers=hdr(auth, MOD)).json()
    assert r["simulated"] is True and len(r["scenarios"]) == 5
    flood = next(s for s in r["scenarios"] if s["scenario_id"] == "flood_loop")
    assert flood["pristine"] is True and flood["steps"][0]["status"] == "pending" and flood["label"] == "SIMULATED"
    st = client.get("/api/demo/status").json()
    assert st["scenarios_seeded"] is True and st["graph_used"] == "simulated_fixture"


def _snapshot(repo, sid):
    iid = dd.get_scenario(repo, sid)["incident_id"]
    out = {t: sorted(r["sk"] for r in repo.query(t, {"pk": iid}, limit=10_000))
           for t in ("overlays", "reports", "messages")}
    out["notices"] = sorted(r["text"] for r in repo.query("notices", {"pk": iid}, limit=10_000))
    return out


def test_start_apply_reset_cycle_restores_pristine_state(env):
    client, app, repo, auth = env
    m = hdr(auth, MOD)
    s = client.post("/api/demo/scenarios/flood_loop/start", headers=m).json()
    assert s["activated"] is True and s["simulation_flag"] is True
    before = _snapshot(repo, "flood_loop")
    iid = s["incident_id"]
    # client-driven step returns what the console must do
    c = client.post("/api/demo/scenarios/flood_loop/steps/route/apply", headers=m).json()
    assert c["applied"] is False and c["client_action"]["endpoint"] == "POST /api/routes/calculate"
    # server-side step changes overlays and is visible to routing
    a = client.post("/api/demo/scenarios/flood_loop/steps/closure/apply", headers=m).json()
    assert a["applied"] is True and a["simulated"] is True
    assert _snapshot(repo, "flood_loop") != before
    assert dd.is_seeded_state(repo, "flood_loop") is False
    assert client.post("/api/demo/scenarios/flood_loop/steps/nope/apply", headers=m).status_code == 404
    listed = client.get("/api/demo/scenarios", headers=m).json()["scenarios"]
    flood = next(x for x in listed if x["scenario_id"] == "flood_loop")
    assert flood["pristine"] is False and "closure" in flood["applied_steps"]
    # reset restores within the limit, drops demo reports/messages, publishes an event
    other_before = _snapshot(repo, "fire_downtown")
    r = client.post("/api/demo/scenarios/flood_loop/reset", headers=m).json()
    assert r["changed"] is True and r["within_limit"] is True and r["event"] == "scenario_reset"
    assert _snapshot(repo, "flood_loop") == before
    assert dd.is_seeded_state(repo, "flood_loop")
    assert _snapshot(repo, "fire_downtown") == other_before
    ev = client.get("/api/events", params={"incident_id": iid}).json()
    assert "scenario_reset" in [e["type"] for e in ev["events"]]


def test_reset_when_pristine_is_a_noop(env):
    client, app, repo, auth = env
    m = hdr(auth, MOD)
    client.post("/api/demo/scenarios/flood_loop/start", headers=m)
    before = _snapshot(repo, "flood_loop")
    r = client.post("/api/demo/scenarios/flood_loop/reset", headers=m)
    assert r.status_code == 200 and r.json()["changed"] is False and r.json()["event"] is None
    assert _snapshot(repo, "flood_loop") == before
    iid = dd.get_scenario(repo, "flood_loop")["incident_id"]
    assert "scenario_reset" not in [e["type"] for e in
                                    client.get("/api/events", params={"incident_id": iid}).json()["events"]]
    client.get("/api/demo/scenarios", headers=m)  # seeds the remaining scenarios
    assert client.post("/api/demo/reset-all", headers=m).json()["changed"] is False


def test_reset_publishes_event_to_subscribers(env):
    client, app, repo, auth = env
    m = hdr(auth, MOD)
    s = client.post("/api/demo/scenarios/earthquake_debris/start", headers=m).json()
    client.post("/api/demo/scenarios/earthquake_debris/steps/debris/apply", headers=m)
    got = []
    app.state.publisher.subscribe(s["incident_id"], got.append)
    client.post("/api/demo/scenarios/earthquake_debris/reset", headers=m)
    assert got[-1].type == "scenario_reset" and got[-1].payload["simulated"] is True


def test_reset_all_and_advance(env):
    client, app, repo, auth = env
    m = hdr(auth, MOD)
    client.post("/api/demo/scenarios/flood_loop/start", headers=m)
    n = 0
    while True:
        a = client.post("/api/demo/scenarios/flood_loop/advance", headers=m).json()
        if a["done"]:
            break
        n += 1
    assert n == 5
    r = client.post("/api/demo/reset-all", headers=m).json()
    assert r["changed"] is True and r["within_limit"] is True and "flood_loop" in r["scenarios"]
    assert all(dd.is_seeded_state(repo, s) for s in dd.scenario_ids())
    assert client.post("/api/demo/scenarios/flood_loop/advance", headers=m).json()["done"] is False


def test_adhoc_closures_only_on_simulated_incidents(env):
    client, app, repo, auth = env
    m = hdr(auth, MOD)
    s = client.post("/api/demo/scenarios/flood_loop/start", headers=m).json()
    edge = dd.get_scenario(repo, "flood_loop")["resolved"]["route_before_edge_ids"][0]
    r = client.post("/api/demo/closures", json={"incident_id": s["incident_id"], "edge_ids": [edge]}, headers=m)
    assert r.status_code == 201 and r.json()["simulation_flag"] is True
    rid = r.json()["restriction_id"]
    assert client.post("/api/demo/closures", json={"incident_id": s["incident_id"], "edge_ids": ["zz"]},
                       headers=m).status_code == 400
    assert client.delete(f"/api/demo/closures/{rid}", params={"incident_id": s["incident_id"]},
                         headers=m).json()["removed"] is True
    real = client.post("/api/incidents", json={"incident_type": "flood", "source": "t"}, headers=m)
    if real.status_code == 201:
        assert client.post("/api/demo/closures", json={"incident_id": real.json()["incident_id"],
                           "edge_ids": [edge]}, headers=m).status_code == 403


# ----------------------------------------------------------- blocked stairwell end to end
def route_as(client, row, actor):
    u = next(x for x in row["resolved"]["users"] if x["key"] == actor)
    dest = row["destination"] or {"latitude": 41.8835, "longitude": -87.6350}
    r = client.post("/api/routes/calculate", json={
        "incident_id": row["incident_id"], "origin": {"latitude": u["latitude"], "longitude": u["longitude"]},
        "destination": {"latitude": dest["latitude"], "longitude": dest["longitude"]},
        "travel_mode": "walking", "mobility_profile": "standard", "alternatives": 1,
        "origin_indoor_node_id": u["indoor_node_id"]})
    assert r.status_code == 200, r.text
    return r.json()


def edge_ids_of(resp):
    return [p["edge_id"] for p in resp["routes"][0]["edge_penalties"]] if resp.get("routes") else []


def test_stairwell_scenario_end_to_end(env):
    client, app, repo, auth = env
    m = hdr(auth, MOD)
    client.post(f"/api/demo/scenarios/{STAIR}/start", headers=m)
    row = dd.get_scenario(repo, STAIR)
    iid = row["incident_id"]
    act = lambda actor, action, **kw: client.post(  # noqa: E731
        f"/api/demo/scenarios/{STAIR}/act", json={"actor": actor, "action": action, **kw}, headers=m)
    closure_edges = set(row["resolved"]["closure"]["edge_ids"])

    # nothing to corroborate before A reports
    assert act("B", "corroborate").status_code == 409
    before = route_as(client, row, "A")

    ra = act("A", "report")
    assert ra.status_code == 200, ra.text
    rep = ra.json()["report"]
    assert rep["verification_status"] == "Unverified" and rep["simulated"] is True
    assert "reporter_id" not in rep
    assert act("A", "corroborate").status_code == 403  # own report
    # B sees the report through the public API without any reload
    assert [r["report_id"] for r in client.get("/api/reports", params={"incident_id": iid}).json()["reports"]] \
        == [rep["report_id"]]

    rb = act("B", "corroborate").json()["report"]
    assert rb["verification_status"] == "Unverified" and rb["confirmations"] == 1
    rc = act("C", "corroborate").json()["report"]
    assert rc["verification_status"] == "Corroborated"
    assert rc["status_label"].endswith("not officially verified")
    assert act("C", "corroborate").status_code == 409  # duplicate vote
    assert repo.query("overlays", {"pk": iid}) == []  # corroboration alone never closes anything

    # moderator confirms: SIMULATED closure, indoor routes change
    rm = act("moderator", "confirm")
    assert rm.status_code == 200, rm.text
    body = rm.json()
    assert body["report"]["verification_status"] == "Officially_Verified"
    assert body["report"]["source_label"] == "Simulated moderator verification"
    assert body["overlay_version"] is not None
    after = route_as(client, row, "A")
    closed = {e for o in repo.query("overlays", {"pk": iid}) for e in o["edge_ids"]}
    assert closed
    used_before, used_after = set(edge_ids_of(before)), set(edge_ids_of(after))
    assert used_before & closed, "the pre-closure indoor route should use Stairwell A"
    assert not (used_after & closed), "after the moderator confirms, the route avoids Stairwell A"

    # B asks for help through the real messaging service
    msg = act("B", "message")
    assert msg.status_code == 200 and msg.json()["message_type"] == "assistance_request"
    assert len(client.get("/api/messages", params={"incident_id": iid}).json()["messages"]) == 1

    # reset removes every demo report, message and overlay and restores the pristine state
    r = client.post(f"/api/demo/scenarios/{STAIR}/reset", headers=m).json()
    assert r["changed"] is True
    assert repo.query("reports", {"pk": iid}) == [] and repo.query("messages", {"pk": iid}) == []
    assert dd.is_seeded_state(repo, STAIR)
    assert edge_ids_of(route_as(client, dd.get_scenario(repo, STAIR), "A")) == edge_ids_of(before)


def test_act_requires_moderator_for_confirm_and_valid_actor(env):
    client, app, repo, auth = env
    m = hdr(auth, MOD)
    client.post(f"/api/demo/scenarios/{STAIR}/start", headers=m)
    url = f"/api/demo/scenarios/{STAIR}/act"
    assert client.post(url, json={"actor": "A", "action": "confirm"}, headers=m).status_code == 400
    assert client.post(url, json={"actor": "Z", "action": "report"}, headers=m).status_code == 400
    assert client.post("/api/demo/scenarios/flood_loop/act", json={"actor": "A", "action": "report"},
                       headers=m).status_code == 400


def test_env_flag_enables_demo_mode_and_background_seed(monkeypatch):
    monkeypatch.setenv("SAFEPATH_DEMO_MODE", "true")
    assert Settings.from_env().demo_mode is True
    monkeypatch.delenv("SAFEPATH_DEMO_MODE")
    assert Settings.from_env().demo_mode is False
    client, app, repo, auth = build()
    app.state.demo_service.start_background_seed()
    app.state.demo_service._thread.join(timeout=60)
    assert app.state.demo_service.seed_state == "seeded"
    assert len(dd.list_scenarios(repo)) == 5
    assert client.get("/api/demo/status").json()["scenarios_seeded"] is True
