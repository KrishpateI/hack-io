"""Authentication, roles and audit tests (Req 22, 28.7, 31.4, 31.7, 42.3, 42.4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.adapters import MemoryRepository, Role, Unauthenticated
from app.auth.audit import AuditWriter
from app.auth.deps import require
from app.auth.provider import DemoTokenAuthProvider, load_roles
from app.config import Settings
from app.main import create_app

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def make(roles=None, clock=None):
    clock = clock or Clock()
    return DemoTokenAuthProvider("test-secret", roles=roles, clock=clock), clock


def build_client(provider, repo=None):
    from app.auth.deps import install_auth

    app = create_app(Settings())
    install_auth(app, auth=provider, repository=repo or MemoryRepository())

    @app.get("/t/mod")
    def mod(p=Depends(require({Role.MODERATOR, Role.AUTHORIZED_OFFICIAL}))):
        return {"sub": p.subject}

    @app.get("/t/user")
    def usr(p=Depends(require({Role.USER}))):
        return {"sub": p.subject}

    return app, TestClient(app, raise_server_exceptions=False)


def h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_guest_token_roundtrip_and_24h_expiry():
    p, clock = make()
    g = p.issue_guest()
    assert g.expires_at == T0 + timedelta(hours=24)
    pr = p.verify(g.token)
    assert pr.role is Role.GUEST and pr.is_guest
    clock.now = T0 + timedelta(hours=24)
    with pytest.raises(Unauthenticated):
        p.verify(g.token)


def test_tampered_and_garbage_tokens_rejected():
    p, _ = make()
    g = p.issue_guest()
    body, sig = g.token.split(".")
    for bad in (None, "", "abc", f"{body}.{'0' * 64}", f"{body}x.{sig}", g.token + "0"):
        with pytest.raises(Unauthenticated):
            p.verify(bad)
    other = DemoTokenAuthProvider("other-secret", clock=Clock())
    with pytest.raises(Unauthenticated):
        other.verify(g.token)


def test_ephemeral_secret_when_unset(caplog):
    with caplog.at_level("WARNING", logger="safepath.auth"):
        p = DemoTokenAuthProvider(None)
    assert "ephemeral" in caplog.text
    assert p.verify(p.issue_guest().token).is_guest
    with pytest.raises(ValueError):
        DemoTokenAuthProvider(None, allow_ephemeral=False)


def test_roles_only_from_admin_config(tmp_path):
    f = tmp_path / "roles.yaml"
    f.write_text("users:\n  usr_m: moderator\n  usr_o: authorized_official\n", encoding="utf-8")
    assert load_roles(f) == {"usr_m": Role.MODERATOR, "usr_o": Role.AUTHORIZED_OFFICIAL}
    assert load_roles(None) == {} and load_roles(tmp_path / "missing.yaml") == {}
    p = DemoTokenAuthProvider("s", roles_path=f)
    assert p.verify(p.issue_token("usr_m")[0]).role is Role.MODERATOR
    assert p.verify(p.issue_token("usr_other")[0]).role is Role.USER
    # A guest-kind token for a configured subject cannot be crafted by clients (needs the secret);
    # default for unknown subjects is never privileged.
    assert p.role_for("usr_nobody") is Role.USER
    f.write_text("users:\n  usr_x: wizard\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_roles(f)


def test_role_aliases():
    assert Role.REGISTERED is Role.USER and Role.OFFICIAL is Role.AUTHORIZED_OFFICIAL


def test_require_401_and_403():
    p, _ = make(roles={"usr_m": Role.MODERATOR})
    _, c = build_client(p)
    assert c.get("/t/mod").status_code == 401
    assert c.get("/t/mod", headers=h("bad.token")).status_code == 401
    guest = p.issue_guest().token
    r = c.get("/t/mod", headers=h(guest))
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert c.get("/t/mod", headers=h(p.issue_token("usr_m")[0])).status_code == 200
    assert c.get("/t/user", headers=h(guest)).status_code == 403
    assert c.get("/t/user", headers=h(p.issue_token("usr_a")[0])).status_code == 200


def test_expired_token_401():
    p, clock = make()
    _, c = build_client(p)
    tok = p.issue_guest().token
    clock.now += timedelta(hours=25)
    assert c.get("/api/auth/me", headers=h(tok)).status_code == 401


def test_guest_endpoint_and_privacy_default_off():
    p, _ = make()
    _, c = build_client(p)
    r = c.post("/api/auth/guest")
    assert r.status_code == 200 and r.json()["role"] == "guest"
    tok = r.json()["token"]
    me = c.get("/api/auth/me", headers=h(tok)).json()
    assert me["privacy_preferences"] == {"share_location": False}
    r = c.put("/api/auth/me/privacy", headers=h(tok), json={"share_location": True})
    assert r.json()["privacy_preferences"]["share_location"] is True
    assert c.get("/api/auth/me", headers=h(tok)).json()["privacy_preferences"]["share_location"] is True


def test_register_login_and_no_plaintext_password():
    p, _ = make()
    repo = MemoryRepository()
    _, c = build_client(p, repo)
    r = c.post("/api/auth/register", json={"display_name": "Ann", "password": "hunter2hunter2"})
    assert r.status_code == 201 and r.json()["role"] == "user"
    uid = r.json()["subject"]
    assert c.post("/api/auth/login", json={"user_id": uid, "password": "wrong-password"}).status_code == 401
    assert c.post("/api/auth/login", json={"user_id": "nope", "password": "x"}).status_code == 401
    ok = c.post("/api/auth/login", json={"user_id": uid, "password": "hunter2hunter2"})
    assert ok.status_code == 200
    assert c.get("/api/auth/me", headers=h(ok.json()["token"])).json()["display_name"] == "Ann"
    stored = repr(repo.get("credentials", {"pk": uid, "sk": ""})) + repr(repo.get("users", {"pk": uid, "sk": ""}))
    assert "hunter2hunter2" not in stored


def test_client_cannot_supply_role():
    p, _ = make()
    _, c = build_client(p)
    r = c.post("/api/auth/register", json={"password": "longenough1", "role": "moderator"})
    assert r.status_code == 400
    r = c.post("/api/auth/guest", json={"role": "moderator"}, headers={"X-Role": "moderator"})
    assert r.json()["role"] == "guest"


def test_self_escalation_rejected_and_audited():
    p, _ = make()
    repo = MemoryRepository()
    app, c = build_client(p, repo)
    tok = c.post("/api/auth/guest").json()
    for role in ("moderator", "authorized_official"):
        r = c.post("/api/auth/role-request", headers=h(tok["token"]), json={"role": role})
        assert r.status_code == 403
    entries = AuditWriter(repo).entries(datetime.now(timezone.utc).date().isoformat())
    assert [e.action for e in entries] == ["role_escalation_rejected"] * 2
    assert entries[0].actor_id == tok["subject"] and entries[0].target_id == "moderator"
    # still guest afterwards
    assert c.get("/api/auth/me", headers=h(tok["token"])).json()["role"] == "guest"


def test_audit_writer_who_what_when():
    repo = MemoryRepository()
    clock = Clock()
    a = AuditWriter(repo, clock)
    a.role_change("adm", "authorized_official", "usr_1")
    a.moderation("mod", "moderator", "rpt_1", "hide")
    a.official_notice("off", "authorized_official", "ntc_1")
    got = a.entries("2025-01-01")
    assert [e.action for e in got] == ["role_change", "moderation:hide", "official_notice"]
    assert got[0].ts == T0 and got[1].actor_id == "mod"


def test_request_state_set_for_rate_limiter():
    p, _ = make()
    app, c = build_client(p)
    seen = {}

    @app.get("/t/state")
    def state(request: Request, pr=Depends(require({Role.GUEST}))):
        seen["subject"], seen["principal"] = request.state.subject, request.state.principal
        return {}

    g = p.issue_guest()
    assert c.get("/t/state", headers=h(g.token)).status_code == 200
    assert seen["subject"] == g.principal.subject and seen["principal"].role is Role.GUEST
