"""CheckInService tests (Req 27.1-27.8, 28.1/28.2, 31.6, 31.8, 31.9)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.adapters import MemoryRepository
from app.adapters.ports import Event
from app.auth.deps import install_auth
from app.auth.provider import DemoTokenAuthProvider
from app.communication.checkins import EMERGENCY_GUIDANCE, NO_DISPATCH_NOTICE
from app.config import Settings
from app.main import create_app
from app.models.domain import Incident
from app.models.privacy import coarsen

LAT, LON = 41.881234, -87.631234
INC = "inc_test"


class Pub:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, incident_id: str, event: Event) -> None:
        self.events.append(event)


@pytest.fixture()
def env():
    app = create_app(Settings())
    repo = MemoryRepository()
    provider = DemoTokenAuthProvider("test-secret")
    install_auth(app, auth=provider, repository=repo)
    pub = Pub()
    app.state.publisher = pub
    ctx = app.state.routing_context_provider = _Ctx()
    client = TestClient(app, raise_server_exceptions=False)
    return app, client, provider, pub, ctx


class _Ctx:
    def incident_exists(self, incident_id: str) -> bool:
        return incident_id == INC


def guest(provider):
    g = provider.issue_guest()
    return {"Authorization": f"Bearer {g.token}"}, g.principal.subject


def post(client, h, **kw):
    body = {"incident_id": INC, "status": "safe", **kw}
    return client.post("/api/checkins", json=body, headers=h)


def opt_in(client, h):
    assert client.put("/api/auth/me/privacy", json={"share_location": True}, headers=h).status_code == 200


def test_default_private_no_location_no_event(env):
    app, client, provider, pub, _ = env
    h, _ = guest(provider)
    r = post(client, h, location={"latitude": LAT, "longitude": LON})
    assert r.status_code == 201
    c = r.json()["checkin"]
    assert c["visibility"] == "private" and c["location"] is None and c["status"] == "safe"
    assert c["status_label"] == "I'm safe"
    assert pub.events == []
    assert client.get(f"/api/checkins?incident_id={INC}").json()["checkins"] == []


def test_opt_in_requires_preference_and_flag_and_is_coarse(env):
    app, client, provider, pub, _ = env
    h, sub = guest(provider)
    loc = {"latitude": LAT, "longitude": LON}
    # flag without preference: nothing stored
    r = post(client, h, location=loc, share_location=True, visibility="public")
    assert r.json()["checkin"]["location"] is None
    opt_in(client, h)
    # preference without flag: nothing stored
    assert post(client, h, location=loc, visibility="public").json()["checkin"]["location"] is None
    pub.events.clear()
    r = post(client, h, location=loc, share_location=True, visibility="public")
    assert r.json()["location_shared"] is True
    assert tuple(r.json()["checkin"]["location"]) == coarsen(LAT, LON)
    pub_resp = client.get(f"/api/checkins?incident_id={INC}")
    body = pub_resp.text
    assert tuple(pub_resp.json()["checkins"][0]["location"]) == coarsen(LAT, LON)
    blobs = [body, json.dumps([e.payload for e in pub.events]), r.text]
    assert len(pub.events) == 1 and pub.events[0].type == "checkin_created"
    for b in blobs:
        assert str(LAT) not in b and str(LON) not in b and sub not in b.replace(r.json()["checkin"]["alias"], "")
    assert "user_id" not in body and "user_id" not in json.dumps(pub.events[0].payload)
    # nothing in storage is exact either
    for row in app.state.repository.query("checkins", {"pk": INC}):
        if row["optional_location"] is not None:
            assert tuple(row["optional_location"]) == coarsen(LAT, LON)


def test_retention_scrub_keeps_record(env):
    app, client, provider, _, _ = env
    h, _ = guest(provider)
    opt_in(client, h)
    post(client, h, location={"latitude": LAT, "longitude": LON}, share_location=True, visibility="public")
    svc = app.state.checkin_service
    now = datetime.now(timezone.utc)
    assert svc.scrub_expired_locations(now + timedelta(hours=71)) == 0
    assert svc.scrub_expired_locations(now + timedelta(hours=73)) == 1
    assert svc.scrub_expired_locations(now + timedelta(hours=73)) == 0  # idempotent
    mine = client.get(f"/api/checkins?incident_id={INC}", headers=h).json()["mine"]
    assert len(mine["history"]) == 1 and mine["current"]["location"] is None
    assert mine["current"]["location_expires_at"] is None


def test_expired_location_hidden_before_scrub(env):
    app, client, provider, _, _ = env
    h, _ = guest(provider)
    opt_in(client, h)
    post(client, h, location={"latitude": LAT, "longitude": LON}, share_location=True, visibility="public")
    svc = app.state.checkin_service
    svc._clock = lambda: datetime.now(timezone.utc) + timedelta(hours=80)
    item = client.get(f"/api/checkins?incident_id={INC}").json()["checkins"][0]
    assert item["location"] is None


def test_opt_out_removes_location_and_publishes(env):
    app, client, provider, pub, _ = env
    h, _ = guest(provider)
    opt_in(client, h)
    post(client, h, location={"latitude": LAT, "longitude": LON}, share_location=True, visibility="public")
    pub.events.clear()
    assert client.put("/api/auth/me/privacy", json={"share_location": False}, headers=h).status_code == 200
    assert client.get(f"/api/checkins?incident_id={INC}").json()["checkins"][0]["location"] is None
    assert [e.type for e in pub.events] == ["checkin_location_removed"]


def test_assistance_notice_and_guidance(env):
    _, client, provider, pub, _ = env
    h, _ = guest(provider)
    r = post(client, h, status="I need assistance", visibility="public")
    assert r.status_code == 201
    j = r.json()
    assert j["notice"] == NO_DISPATCH_NOTICE and j["emergency_guidance"] == EMERGENCY_GUIDANCE
    assert "911" in j["emergency_guidance"] and "does not dispatch" in j["notice"]
    assert j["checkin"]["status"] == "need_assistance"
    item = client.get(f"/api/checkins?incident_id={INC}").json()["checkins"][0]
    assert item["notice"] == NO_DISPATCH_NOTICE and item["emergency_guidance"] == EMERGENCY_GUIDANCE
    assert pub.events[0].payload["notice"] == NO_DISPATCH_NOTICE


def test_private_not_readable_by_others_but_by_owner_with_history(env):
    _, client, provider, _, _ = env
    h1, _ = guest(provider)
    h2, _ = guest(provider)
    post(client, h1, status="safe")
    post(client, h1, status="route_blocked")
    url = f"/api/checkins?incident_id={INC}"
    assert client.get(url, headers=h2).json()["checkins"] == []
    assert client.get(url, headers=h2).json()["mine"]["history"] == []
    mine = client.get(url, headers=h1).json()["mine"]
    assert mine["current"]["status"] == "route_blocked" and len(mine["history"]) == 2


def test_incident_members_needs_token_and_newest_is_current(env):
    _, client, provider, pub, _ = env
    h1, _ = guest(provider)
    h2, _ = guest(provider)
    post(client, h1, status="safe", visibility="incident-members")
    post(client, h1, status="cannot_reach_destination", visibility="incident-members")
    url = f"/api/checkins?incident_id={INC}"
    assert client.get(url).json()["checkins"] == []
    members = client.get(url, headers=h2).json()["checkins"]
    assert [c["status"] for c in members] == ["cannot_reach_destination"]
    assert "user_id" not in json.dumps(members) and pub.events == []
    post(client, h1, status="safe")  # newest is private: no longer shown to others
    assert client.get(url, headers=h2).json()["checkins"] == []


def test_validation_and_not_found(env):
    _, client, provider, pub, _ = env
    h, _ = guest(provider)
    r = post(client, h, status="fine")
    assert r.status_code == 400 and r.json()["error"]["fields"][0]["field"] == "status"
    assert post(client, h, visibility="everyone").status_code == 400
    assert post(client, h, share_location=True).status_code == 400
    assert post(client, h, location={"latitude": 99, "longitude": 0}).status_code == 400
    assert client.post("/api/checkins", json={"status": "safe"}, headers=h).status_code == 400
    assert client.post("/api/checkins", json={"incident_id": "nope", "status": "safe"}, headers=h).status_code == 404
    assert client.get("/api/checkins?incident_id=nope").status_code == 404
    assert client.get("/api/checkins").status_code == 400
    assert pub.events == []
    assert client.post("/api/checkins", json={"incident_id": INC, "status": "safe"}).status_code == 401


def test_rate_limit_429(env):
    _, client, provider, _, _ = env
    h, _ = guest(provider)
    for _ in range(10):
        assert post(client, h).status_code == 201
    r = post(client, h)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_status_display_texts_accepted(env):
    _, client, provider, _, _ = env
    h, _ = guest(provider)
    for label, value in [("I'm safe", "safe"), ("I cannot reach my planned destination", "cannot_reach_destination"),
                         ("My route is blocked", "route_blocked")]:
        assert post(client, h, status=label).json()["checkin"]["status"] == value
