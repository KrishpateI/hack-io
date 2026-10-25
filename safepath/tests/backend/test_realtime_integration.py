"""Realtime integration tests (Req 25.3, 44.10, 44.11, 46.4).

End-to-end through the HTTP API: a report submitted by one client is persisted and pushed over
``/ws/incidents/{id}`` to other subscribed clients within 5 seconds (two clients and 50 clients),
and stored reports/events survive a SqliteRepository reload.
"""
from __future__ import annotations

import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import InProcessPublisher, MemoryRepository, SqliteRepository
from app.api.ratelimit import InMemoryRateLimiter
from app.api.routers.events import get_event_bus
from app.auth.deps import install_auth
from app.auth.provider import DemoTokenAuthProvider
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph
from app.routing.indoor import merge_indoor_runtime

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
INC = "inc_rt"
POINT = {"latitude": 41.8708, "longitude": -87.6505}  # fixture node fx-uic
GRAPH = merge_indoor_runtime(Graph.load(FIXTURE_ROOT))
DELIVERY_BUDGET_S = 5.0


@pytest.fixture(autouse=True)
def _alias_secret(monkeypatch):
    monkeypatch.setenv("SAFEPATH_ALIAS_SECRET", "test-alias-secret")


def build_app(repo):
    auth = DemoTokenAuthProvider("test-secret")
    app = create_app(Settings())
    install_auth(app, auth=auth, repository=repo)
    app.state.publisher = InProcessPublisher()
    app.state.graph = GRAPH
    app.state.rate_limiter = InMemoryRateLimiter()
    get_event_bus(app)
    if repo.get("incidents", {"pk": "all", "sk": INC}) is None:
        repo.put("incidents", {"pk": "all", "sk": INC, "incident_id": INC, "simulation_flag": True})
    return app, auth


def hdr(auth, subject="usr_a"):
    tok, _ = auth.issue_token(subject)
    return {"Authorization": f"Bearer {tok}"}


def report_body(**kw):
    return {"incident_id": INC, "category": "blocked road", "description": "tree down", **POINT, **kw}


def submit_and_collect(client, auth, sockets):
    """POST a report, then read one frame per socket; returns (report, frames, elapsed seconds)."""
    t0 = time.monotonic()
    r = client.post("/api/reports", json=report_body(), headers=hdr(auth))
    assert r.status_code == 201, r.text
    frames = [ws.receive_json() for ws in sockets]
    return r.json(), frames, time.monotonic() - t0


def test_two_clients_receive_same_report_event_within_5s():
    app, auth = build_app(MemoryRepository())
    client = TestClient(app)
    with client.websocket_connect(f"/ws/incidents/{INC}") as a, \
            client.websocket_connect(f"/ws/incidents/{INC}") as b:
        report, (ea, eb), elapsed = submit_and_collect(client, auth, [a, b])
    assert elapsed < DELIVERY_BUDGET_S
    assert ea == eb, "both browsers must see the identical event (same event_id)"
    assert ea["type"] == "report_created" and ea["incident_id"] == INC
    assert ea["payload"]["report_id"] == report["report_id"]
    assert "reporter_id" not in ea["payload"]
    # The pushed event is the stored one: catch-up returns the same id.
    stored = client.get("/api/events", params={"incident_id": INC}).json()["events"]
    assert [e["event_id"] for e in stored] == [ea["event_id"]]


def test_fifty_clients_all_receive_event_within_5s():
    app, auth = build_app(MemoryRepository())
    client = TestClient(app)
    with ExitStack() as stack:
        sockets = [stack.enter_context(client.websocket_connect(f"/ws/incidents/{INC}")) for _ in range(50)]
        report, frames, elapsed = submit_and_collect(client, auth, sockets)
    assert elapsed < DELIVERY_BUDGET_S, f"delivery to 50 clients took {elapsed:.2f}s"
    assert len(frames) == 50
    assert {f["event_id"] for f in frames} == {frames[0]["event_id"]}
    assert all(f["payload"]["report_id"] == report["report_id"] for f in frames)


def test_other_incident_subscribers_do_not_receive_event():
    app, auth = build_app(MemoryRepository())
    client = TestClient(app)
    with client.websocket_connect("/ws/incidents/inc_other") as other, \
            client.websocket_connect(f"/ws/incidents/{INC}") as mine:
        report, (ev,), _ = submit_and_collect(client, auth, [mine])
        # A marker on the other incident proves its socket only sees its own stream.
        marker = get_event_bus(app).commit([], "inc_other", "marker")
        assert other.receive_json()["event_id"] == marker.event_id
    assert ev["payload"]["report_id"] == report["report_id"]


def test_reports_and_events_persist_across_repository_reload(tmp_path):
    path = str(tmp_path / "safepath.sqlite")
    repo = SqliteRepository(path)
    app, auth = build_app(repo)
    client = TestClient(app)
    created = client.post("/api/reports", json=report_body(), headers=hdr(auth)).json()
    events_before = client.get("/api/events", params={"incident_id": INC}).json()["events"]
    repo.close()

    reloaded = SqliteRepository(path)
    try:
        app2, _ = build_app(reloaded)
        c2 = TestClient(app2)
        listed = c2.get("/api/reports", params={"incident_id": INC}).json()["reports"]
        assert [r["report_id"] for r in listed] == [created["report_id"]]
        assert listed[0]["verification_status"] == "Unverified"
        assert listed[0]["description"] == created["description"]
        row = reloaded.get("reports", {"pk": INC, "sk": created["report_id"]})
        assert row["reporter_id"] == "usr_a"
        events_after = c2.get("/api/events", params={"incident_id": INC}).json()["events"]
        assert [e["event_id"] for e in events_after] == [e["event_id"] for e in events_before]
        assert events_after[0]["type"] == "report_created"
        # A reconnecting client that already saw the event gets nothing new.
        since = events_after[-1]["event_id"]
        assert c2.get("/api/events", params={"incident_id": INC, "since": since}).json()["events"] == []
    finally:
        reloaded.close()
