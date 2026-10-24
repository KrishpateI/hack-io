"""EventBus and local realtime endpoint tests (Req 25.1, 25.3, 25.4, 25.6, 30.9)."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.adapters import InProcessPublisher, MemoryRepository, TxOp
from app.adapters.ports import RepositoryUnavailable
from app.api.routers.events import get_event_bus
from app.auth.deps import install_auth
from app.auth.provider import DemoTokenAuthProvider
from app.communication.events import EventBus, EVENTS_TABLE
from app.config import Settings
from app.main import create_app

INC = "inc_A"


@pytest.fixture
def env():
    repo = MemoryRepository()
    auth = DemoTokenAuthProvider("test-secret")
    app = create_app(Settings())
    install_auth(app, auth=auth, repository=repo)
    publisher = InProcessPublisher()
    app.state.publisher = publisher
    bus = get_event_bus(app)
    assert bus is not None and bus.publisher is publisher
    return app, repo, bus, publisher, auth


def state_op(key="x"):
    return TxOp(kind="put", table="state", item={"pk": key, "sk": "", "v": 1})


def test_commit_writes_row_and_publishes_with_ulid_id(env):
    _, repo, bus, publisher, _ = env
    rec = bus.commit([state_op()], INC, "report_created", {"report_id": "rpt_1", "reporter_id": "usr_secret"})
    assert repo.get("state", {"pk": "x", "sk": ""}) is not None
    assert repo.get(EVENTS_TABLE, {"pk": INC, "sk": rec.event_id}) is not None
    (inc, ev), = publisher.published
    assert inc == INC and ev.id == rec.event_id and ev.id.startswith("evt_") and len(ev.id) == 4 + 26
    assert ev.payload == {"report_id": "rpt_1"}  # private keys scrubbed from the public view


def test_event_ids_are_ordered(env):
    _, _, bus, _, _ = env
    ids = [bus.commit([], INC, "t").event_id for _ in range(20)]
    assert ids == sorted(ids) and len(set(ids)) == 20


def test_publish_failure_does_not_fail_write(env, caplog):
    _, repo, bus, publisher, _ = env

    def boom(*a, **k):
        raise RuntimeError("publisher down")

    publisher.publish = boom
    with caplog.at_level(logging.ERROR):
        rec = bus.commit([state_op("y")], INC, "t")
    assert repo.get("state", {"pk": "y", "sk": ""}) is not None
    assert repo.get(EVENTS_TABLE, {"pk": INC, "sk": rec.event_id}) is not None
    assert bus.publish_failures == 1 and "publish failed" in caplog.text


def test_failed_transaction_publishes_nothing(env):
    _, repo, bus, publisher, _ = env
    repo.available = False
    with pytest.raises(RepositoryUnavailable):
        bus.commit([state_op()], INC, "t")
    assert publisher.published == []


def test_catch_up_returns_missed_events_scoped_by_incident(env):
    app, _, bus, _, _ = env
    client = TestClient(app)
    e1 = bus.commit([], INC, "a", {"n": 1, "user_id": "u"})
    e2 = bus.commit([], INC, "b", {"n": 2})
    bus.commit([], "inc_B", "other")
    e3 = bus.commit([], INC, "c", {"n": 3})

    r = client.get("/api/events", params={"incident_id": INC})
    assert [e["event_id"] for e in r.json()["events"]] == [e1.event_id, e2.event_id, e3.event_id]
    assert r.json()["events"][0]["payload"] == {"n": 1}

    r = client.get("/api/events", params={"incident_id": INC, "since": e1.event_id})
    body = r.json()
    assert [e["event_id"] for e in body["events"]] == [e2.event_id, e3.event_id]
    assert body["last_event_id"] == e3.event_id
    assert all(e["incident_id"] == INC for e in body["events"])
    assert client.get("/api/events", params={"incident_id": INC, "since": e3.event_id}).json()["events"] == []


def test_two_websocket_clients_receive_same_event(env):
    app, _, bus, _, _ = env
    client = TestClient(app)
    with client.websocket_connect(f"/ws/incidents/{INC}") as a, client.websocket_connect(f"/ws/incidents/{INC}") as b:
        rec = bus.commit([], INC, "report_created", {"report_id": "rpt_1"})
        bus.commit([], "inc_B", "other")
        ea, eb = a.receive_json(), b.receive_json()
    assert ea == eb and ea["event_id"] == rec.event_id and ea["type"] == "report_created"


def test_websocket_rejects_invalid_token_and_accepts_valid(env):
    app, _, bus, _, auth = env
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/incidents/{INC}?token=bad"):
            pass
    tok = auth.issue_guest().token
    with client.websocket_connect(f"/ws/incidents/{INC}?token={tok}") as ws:
        rec = bus.commit([], INC, "t")
        assert ws.receive_json()["event_id"] == rec.event_id


def test_sse_rejects_invalid_token(env):
    app, *_ = env
    assert TestClient(app).get(f"/sse/incidents/{INC}?token=bad").status_code == 401


def test_sse_streams_event(env):
    app, _, bus, publisher, _ = env
    import threading
    import time

    import httpx
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        with httpx.stream("GET", f"http://127.0.0.1:{port}/sse/incidents/{INC}", timeout=5) as resp:
            lines = resp.iter_lines()
            assert next(lines) == ": connected"
            assert next(lines) == ""
            rec = bus.commit([], INC, "report_created", {"n": 1})
            got = [next(lines) for _ in range(3)]
        assert got[0] == f"id: {rec.event_id}" and got[1] == "event: report_created"
        assert rec.event_id in got[2]
    finally:
        server.should_exit = True
        t.join(timeout=5)
