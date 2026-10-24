"""MessagingService and /api/messages tests (Req 26, 28.1, 28.2, 28.5, 31.8)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters import MemoryRepository, Role
from app.api.ratelimit import InMemoryRateLimiter
from app.auth.deps import install_auth
from app.auth.provider import DemoTokenAuthProvider
from app.communication.messaging import MESSAGE_TYPES, NO_DISPATCH_NOTICE
from app.config import Settings
from app.main import create_app
from app.models.privacy import coarsen

INC = "inc_test"


@pytest.fixture()
def ctx():
    repo = MemoryRepository()
    provider = DemoTokenAuthProvider(
        "test-secret", roles={"mod1": Role.MODERATOR, "off1": Role.AUTHORIZED_OFFICIAL})
    app = create_app(Settings())
    install_auth(app, auth=provider, repository=repo)
    repo.put("incidents", {"pk": "all", "sk": INC, "incident_id": INC})
    client = TestClient(app, raise_server_exceptions=False)

    def hdr(subject: str, guest: bool = False) -> dict:
        tok, _ = provider.issue_token(subject, is_guest=guest)
        return {"Authorization": f"Bearer {tok}"}

    return app, repo, client, hdr


def post(client, hdr, body=None, who="user1", **over):
    payload = {"incident_id": INC, "message_type": "update", "content": "Bridge is open"}
    payload.update(body or {})
    return client.post("/api/messages", json=payload, headers=hdr(who), **over)


def test_post_persists_and_public_view_hides_sender(ctx):
    app, repo, client, hdr = ctx
    r = post(client, hdr)
    assert r.status_code == 201
    msg = r.json()["message"]
    assert msg["source_label"] == "Community" and msg["created_at"]
    assert repo.get("messages", {"pk": INC, "sk": msg["message_id"]})["sender_id"] == "user1"
    listed = client.get("/api/messages", params={"incident_id": INC})
    assert listed.status_code == 200
    text = listed.text
    assert "user1" not in text and "sender_id" not in text
    assert [m["message_id"] for m in listed.json()["messages"]] == [msg["message_id"]]


def test_guest_may_post_and_all_types_accepted(ctx):
    _, _, client, hdr = ctx
    for t in MESSAGE_TYPES:
        assert post(client, hdr, {"message_type": t}, who="guest_1").status_code == 201


def test_unauthenticated_post_is_401(ctx):
    _, _, client, _ = ctx
    r = client.post("/api/messages", json={"incident_id": INC, "message_type": "update", "content": "x"})
    assert r.status_code == 401


def test_sanitization_strips_html(ctx):
    _, repo, client, hdr = ctx
    r = post(client, hdr, {"content": "<script>alert(1)</script><b>Help</b> here"})
    assert r.status_code == 201
    content = r.json()["message"]["content"]
    assert "<" not in content and "script" not in content.lower() and "Help here" in content
    assert post(client, hdr, {"content": "<b></b>"}).status_code == 400  # empty after sanitizing


def test_validation_400_with_field_errors(ctx):
    _, _, client, hdr = ctx
    r = post(client, hdr, {"content": "", "message_type": "bogus"})
    assert r.status_code == 400
    fields = {f["field"] for f in r.json()["error"]["fields"]}
    assert {"content", "message_type"} <= fields
    r = post(client, hdr, {"content": "x" * 1001})
    assert r.status_code == 400 and r.json()["error"]["fields"][0]["field"] == "content"
    assert post(client, hdr, {"content": "x" * 1000}).status_code == 201
    r = post(client, hdr, {"share_location": True})
    assert r.status_code == 400
    assert {f["field"] for f in r.json()["error"]["fields"]} == {"latitude", "longitude"}


def test_unknown_incident_404(ctx):
    _, _, client, hdr = ctx
    assert post(client, hdr, {"incident_id": "nope"}).status_code == 404
    assert client.get("/api/messages", params={"incident_id": "nope"}).status_code == 404


def test_rate_limit_429_with_retry_after(ctx):
    app, _, client, hdr = ctx
    app.state.rate_limiter = InMemoryRateLimiter()
    codes = [post(client, hdr).status_code for _ in range(21)]
    assert codes[:20] == [201] * 20 and codes[20] == 429
    r = post(client, hdr)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_moderator_hides_message_audit_and_event(ctx):
    app, repo, client, hdr = ctx
    mid = post(client, hdr).json()["message"]["message_id"]
    other = post(client, hdr, {"content": "keep me"}).json()["message"]["message_id"]
    r = client.post(f"/api/messages/{mid}/hide", headers=hdr("mod1"))
    assert r.status_code == 200 and r.json()["hidden"] is True
    ids = [m["message_id"] for m in client.get("/api/messages", params={"incident_id": INC}).json()["messages"]]
    assert ids == [other]
    audit = app.state.audit
    from datetime import datetime, timezone
    entries = audit.entries(datetime.now(timezone.utc).date().isoformat())
    assert any(e.action.startswith("moderation") and e.target_id == mid and e.actor_id == "mod1"
               for e in entries)
    types = [e.type for e in app.state.event_bus.since(INC)]
    assert types.count("message_created") == 2 and types.count("message_hidden") == 1


def test_hide_forbidden_for_user_and_guest_and_unknown_404(ctx):
    _, _, client, hdr = ctx
    mid = post(client, hdr).json()["message"]["message_id"]
    assert client.post(f"/api/messages/{mid}/hide", headers=hdr("user1")).status_code == 403
    assert client.post(f"/api/messages/{mid}/hide", headers=hdr("guest_9", True)).status_code == 403
    assert client.post(f"/api/messages/{mid}/hide").status_code == 401
    assert len(client.get("/api/messages", params={"incident_id": INC}).json()["messages"]) == 1
    assert client.post("/api/messages/msg_missing/hide", headers=hdr("mod1")).status_code == 404


def test_message_created_event_published(ctx):
    app, _, client, hdr = ctx
    got = []
    post(client, hdr)  # builds the bus and publisher
    app.state.publisher.subscribe(INC, got.append)
    mid = post(client, hdr, {"content": "second"}).json()["message"]["message_id"]
    assert [e.type for e in got] == ["message_created"]
    assert got[0].payload["message_id"] == mid
    assert "user1" not in str(got[0].payload)


def test_location_requires_opt_in_and_is_coarsened(ctx):
    _, repo, client, hdr = ctx
    lat, lon = 41.878113, -87.629799
    r = post(client, hdr, {"latitude": lat, "longitude": lon})  # no opt-in
    assert r.json()["message"]["approximate_location"] is None
    row = repo.get("messages", {"pk": INC, "sk": r.json()["message"]["message_id"]})
    assert row["approximate_location"] is None
    r = post(client, hdr, {"latitude": lat, "longitude": lon, "share_location": True})
    loc = r.json()["message"]["approximate_location"]
    assert tuple(loc) == coarsen(lat, lon) and tuple(loc) != (lat, lon)
    stored = repo.get("messages", {"pk": INC, "sk": r.json()["message"]["message_id"]})
    assert tuple(stored["approximate_location"]) == coarsen(lat, lon)
    pub = client.get("/api/messages", params={"incident_id": INC}).json()["messages"]
    assert [tuple(m["approximate_location"]) for m in pub if m["approximate_location"]] == [coarsen(lat, lon)]


def test_official_posting_is_still_community_message(ctx):
    _, _, client, hdr = ctx
    r = post(client, hdr, {"content": "Shelter open"}, who="off1")
    assert r.status_code == 201
    msg = r.json()["message"]
    assert msg["source_label"] == "Community" and msg["classification"] == "Community"
    listed = client.get("/api/messages", params={"incident_id": INC}).json()
    assert listed["source_label"] == "Community"
    assert all(m["source_label"] == "Community" for m in listed["messages"])


def test_assistance_requests_carry_no_dispatch_notice(ctx):
    _, _, client, hdr = ctx
    for t in ("assistance_request", "accessibility_assistance_request"):
        r = post(client, hdr, {"message_type": t})
        assert r.json()["notice"] == NO_DISPATCH_NOTICE
        assert r.json()["message"]["notice"] == NO_DISPATCH_NOTICE
    assert "does not dispatch emergency services" in NO_DISPATCH_NOTICE
    r = post(client, hdr, {"message_type": "update"})
    assert "notice" not in r.json()
    assert client.get("/api/messages", params={"incident_id": INC}).json()["notice"] == NO_DISPATCH_NOTICE


def test_list_pagination_since_and_limit(ctx):
    _, _, client, hdr = ctx
    ids = [post(client, hdr, {"content": f"m{i}"}).json()["message"]["message_id"] for i in range(5)]
    page = client.get("/api/messages", params={"incident_id": INC, "limit": 2}).json()
    assert [m["message_id"] for m in page["messages"]] == ids[:2]
    nxt = client.get("/api/messages", params={"incident_id": INC, "since": page["last_message_id"]}).json()
    assert [m["message_id"] for m in nxt["messages"]] == ids[2:]
