"""Sweeper tests (task 9.4; Req 24.5, 27.5) plus report photo_url / photo_key (task 9.7 carry-over)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.adapters import InProcessPublisher, MemoryRepository, Role
from app.adapters.blob import LocalBlobStore
from app.api.ratelimit import InMemoryRateLimiter
from app.api.routers.photos import user_prefix
from app.auth.audit import AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.config import Settings
from app.lambda_sweeper import sweeper_handler
from app.main import create_app
from app.services.overlays import RepositoryContextProvider
from app.services.sweeper import run_sweep, sweeper_interval
from app.services.wiring import get_overlay_service

POINT = {"latitude": 41.8708, "longitude": -87.6505}
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100


@pytest.fixture()
def env(monkeypatch, tmp_path):
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
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.blob_store = LocalBlobStore(tmp_path, "s", base_url="/api/photos/upload")
    client = TestClient(app, raise_server_exceptions=False)
    return client, app, repo, auth


def h(auth, sub):
    return {"Authorization": f"Bearer {auth.issue_token(sub)[0]}"}


def make_incident(client, auth):
    r = client.post("/api/incidents", json={"incident_type": "flood", "source": "t",
                                            "simulation_flag": True}, headers=h(auth, "mod"))
    assert r.status_code == 201, r.text
    return r.json()["incident_id"]


def make_report(client, auth, iid, sub="rep", **extra):
    r = client.post("/api/reports", json={"incident_id": iid, "category": "blocked road",
                                          "description": "x", **POINT, **extra}, headers=h(auth, sub))
    assert r.status_code == 201, r.text
    return r.json()["report_id"]


def row_of(repo, iid, rid):
    return repo.get("reports", {"pk": iid, "sk": rid})


def verified_overdue_report(client, repo, auth):
    """A verified report holding a closure overlay whose expires_at is in the past."""
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    repo.put("reports", {**row_of(repo, iid, rid), "affected_edge_ids": ["e1"]})
    r = client.post(f"/api/reports/{rid}/moderate", json={"action": "verify"}, headers=h(auth, "mod"))
    assert r.status_code == 200, r.text
    row = row_of(repo, iid, rid)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    repo.put("reports", {**row, "expires_at": past})
    return iid, rid


def event_types(repo, iid):
    return [e["type"] for e in repo.query("events", {"pk": iid}, limit=1000)]


# ------------------------------------------------------------------ expiry
def test_sweep_expires_report_removes_closure_and_publishes(env):
    client, app, repo, auth = env
    iid, rid = verified_overdue_report(client, repo, auth)
    version_before = get_overlay_service(app).overlay_version(iid)
    seen: list[str] = []
    app.state.publisher.publish = lambda incident_id, ev: seen.append(ev.type)

    summary = run_sweep(app=app)

    assert summary["reports_expired"] == 1 and summary["errors"] == []
    row = row_of(repo, iid, rid)
    assert row["verification_status"] == "Expired"
    assert row["status_history"][-1]["status"] == "Expired"
    assert row["status_history"][-1]["previous_status"] == "Officially_Verified"
    assert repo.query("overlays", {"pk": iid})[0]["status"] == "removed"
    assert get_overlay_service(app).overlay_version(iid) == version_before + 1
    assert {"restriction_removed", "report_status_changed"} <= set(seen)
    assert {"restriction_removed", "report_status_changed"} <= set(event_types(repo, iid))


def test_second_sweep_is_a_no_op(env):
    client, app, repo, auth = env
    iid, rid = verified_overdue_report(client, repo, auth)
    run_sweep(app=app)
    snapshot = row_of(repo, iid, rid)
    version = get_overlay_service(app).overlay_version(iid)
    n_events = len(event_types(repo, iid))
    again = run_sweep(app=app)
    assert again["reports_expired"] == 0 and again["locations_scrubbed"] == 0
    assert row_of(repo, iid, rid) == snapshot
    assert get_overlay_service(app).overlay_version(iid) == version
    assert len(event_types(repo, iid)) == n_events


def test_resolved_report_is_not_touched(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    client.post(f"/api/reports/{rid}/moderate", json={"action": "resolve"}, headers=h(auth, "mod"))
    row = row_of(repo, iid, rid)
    repo.put("reports", {**row, "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})
    assert run_sweep(app=app)["reports_expired"] == 0
    assert row_of(repo, iid, rid)["verification_status"] == "Resolved"


def test_concurrent_sweeps_expire_exactly_once(env):
    client, app, repo, auth = env
    iid, rid = verified_overdue_report(client, repo, auth)
    version = get_overlay_service(app).overlay_version(iid)
    results: list[dict] = []
    barrier = threading.Barrier(4)

    def work() -> None:
        barrier.wait()
        results.append(run_sweep(app=app))

    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 4 and all(r["errors"] == [] for r in results)
    assert sum(r["reports_expired"] for r in results) == 1
    assert row_of(repo, iid, rid)["verification_status"] == "Expired"
    assert [x["status"] for x in row_of(repo, iid, rid)["status_history"]].count("Expired") == 1
    assert get_overlay_service(app).overlay_version(iid) == version + 1
    assert event_types(repo, iid).count("restriction_removed") == 1


# ------------------------------------------------------------------ location scrub
def test_sweep_scrubs_only_locations(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    rid = make_report(client, auth, iid)
    hd = h(auth, "citizen")
    assert client.put("/api/auth/me/privacy", json={"share_location": True}, headers=hd).status_code == 200
    r = client.post("/api/checkins", headers=hd, json={
        "incident_id": iid, "status": "safe", "visibility": "public", "share_location": True,
        "location": {"latitude": 41.8812, "longitude": -87.6312}})
    assert r.status_code in (200, 201), r.text
    far = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    repo.put("reports", {**row_of(repo, iid, rid), "expires_at": far})
    report_before = row_of(repo, iid, rid)
    now = datetime.now(timezone.utc)

    assert run_sweep(now + timedelta(hours=71), app=app)["locations_scrubbed"] == 0
    summary = run_sweep(now + timedelta(hours=73), app=app)
    assert summary["locations_scrubbed"] == 1
    mine = client.get(f"/api/checkins?incident_id={iid}", headers=hd).json()["mine"]
    assert len(mine["history"]) == 1 and mine["current"]["location"] is None
    assert row_of(repo, iid, rid) == report_before
    assert run_sweep(now + timedelta(hours=73), app=app)["locations_scrubbed"] == 0


# ------------------------------------------------------------------ ttl trimming
def test_sweep_trims_rate_limit_and_event_rows_past_ttl(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    now = datetime.now(timezone.utc)
    repo.put("ratelimits", {"pk": "u#report", "sk": "", "hits": [1.0], "ttl": int(now.timestamp()) - 10})
    repo.put("ratelimits", {"pk": "v#report", "sk": "", "hits": [1.0], "ttl": int(now.timestamp()) + 600})
    repo.put("events", {"pk": iid, "sk": "event_old", "ttl": int(now.timestamp()) - 10})
    summary = run_sweep(now, app=app)
    assert summary["rate_limit_rows_deleted"] == 1 and summary["event_rows_deleted"] == 1
    assert repo.get("ratelimits", {"pk": "u#report", "sk": ""}) is None
    assert repo.get("ratelimits", {"pk": "v#report", "sk": ""}) is not None
    assert repo.get("events", {"pk": iid, "sk": "event_old"}) is None


# ------------------------------------------------------------------ entry points
def test_lambda_handler_returns_summary(env, monkeypatch):
    _, app, _, _ = env
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "app", app)
    out = sweeper_handler({"source": "aws.events"}, None)
    assert {"reports_expired", "locations_scrubbed", "swept_at", "errors"} <= set(out)


def test_admin_sweep_endpoint_is_moderator_only(env):
    client, app, repo, auth = env
    iid, rid = verified_overdue_report(client, repo, auth)
    assert client.post("/api/admin/sweep").status_code == 401
    assert client.post("/api/admin/sweep", headers=h(auth, "plain")).status_code == 403
    r = client.post("/api/admin/sweep", headers=h(auth, "mod"))
    assert r.status_code == 200 and r.json()["reports_expired"] == 1
    assert row_of(repo, iid, rid)["verification_status"] == "Expired"


def test_interval_config_and_lifespan_do_not_break_testclient(env, monkeypatch):
    assert sweeper_interval({"PYTEST_CURRENT_TEST": "x"}) is None
    assert sweeper_interval({}) == 60.0
    assert sweeper_interval({"SAFEPATH_SWEEPER_INTERVAL_SECONDS": "0"}) is None
    assert sweeper_interval({"SAFEPATH_SWEEPER_INTERVAL_SECONDS": "5"}) == 5.0
    assert sweeper_interval({"SAFEPATH_SWEEPER_INTERVAL_SECONDS": "bogus"}) is None
    client, app, repo, auth = env
    iid, rid = verified_overdue_report(client, repo, auth)
    monkeypatch.setenv("SAFEPATH_SWEEPER_INTERVAL_SECONDS", "0.05")
    with TestClient(app) as c:  # runs startup/shutdown with the background task enabled
        for _ in range(100):
            if row_of(repo, iid, rid)["verification_status"] == "Expired":
                break
            threading.Event().wait(0.05)
        assert c.get("/api/health").status_code in (200, 503)
    assert row_of(repo, iid, rid)["verification_status"] == "Expired"


# ------------------------------------------------------------------ photos on reports
def upload(client, auth, sub):
    hd = h(auth, sub)
    up = client.post("/api/photos/presign", json={"content_type": "image/jpeg", "size": 104},
                     headers=hd).json()
    assert client.put(up["upload_url"], content=JPEG, headers={"Content-Type": "image/jpeg"}).status_code == 204
    return up["key"]


def test_report_photo_url_only_when_uploaded_and_never_the_key(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    key = upload(client, auth, "rep")
    rid = make_report(client, auth, iid, photo_key=key)
    missing = make_report(client, auth, iid, sub="rep2", photo_key=user_prefix("rep2") + "nope.jpg")
    plain = make_report(client, auth, iid, sub="rep3")

    reports = {r["report_id"]: r for r in client.get("/api/reports", params={"incident_id": iid}).json()["reports"]}
    assert "photo_url" in reports[rid] and "sig=" in reports[rid]["photo_url"]
    assert "photo_url" not in reports[missing] and "photo_url" not in reports[plain]
    assert all("photo_key" not in r for r in reports.values())
    assert client.get(reports[rid]["photo_url"]).status_code == 200
    detail = client.get(f"/api/reports/{rid}").json()
    assert "photo_url" in detail and "photo_key" not in detail


def test_create_report_rejects_foreign_photo_key(env):
    client, app, repo, auth = env
    iid = make_incident(client, auth)
    foreign = user_prefix("someone-else") + "a.jpg"
    for bad in (foreign, "photos/x.jpg", user_prefix("rep") + "../escape.jpg"):
        r = client.post("/api/reports", headers=h(auth, "rep"), json={
            "incident_id": iid, "category": "flooding", **POINT, "photo_key": bad})
        assert r.status_code == 400, bad
        assert any(f["field"] == "photo_key" for f in r.json()["error"]["fields"])
