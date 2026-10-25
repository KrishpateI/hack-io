"""Property 29: Authorization matrix (task 11.6).

Every protected operation is driven through the real FastAPI app (local settings, in-memory
repository, ``DemoTokenAuthProvider``) with every kind of principal:

* no token, an expired token, an invalid token          -> 401
* an authenticated role outside the operation's role set -> 403
* an authorized role                                    -> the operation proceeds

Rejected attempts leave the repository unchanged (the only exception is the audit entry that a
rejected Moderator/Authorized_Official self-assignment must write). Audited operations write a
who/what entry when they succeed. Guest sessions expire exactly 24 hours after creation.

**Validates: Requirements 21.8, 22.1, 22.4, 22.7, 24.9, 26.9, 28.8, 31.4, 31.7, 40.6, 42.3, 42.4**
"""
# Feature: safepath-evacuation-navigation, Property 29: Authorization matrix
from __future__ import annotations

import copy
import functools
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from app.adapters import InProcessPublisher, MemoryRepository, Role, Unauthenticated
from app.api.ratelimit import InMemoryRateLimiter
from app.auth.accounts import AccountService
from app.auth.audit import AUDIT_TABLE, AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph
from app.routing.indoor import merge_indoor_runtime
from app.services.overlays import RepositoryContextProvider

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
POINT = {"latitude": 41.8708, "longitude": -87.6505}
MOD, OFF = "authz-mod", "authz-off"
SECRET = "authz-secret"

MODS = frozenset({Role.MODERATOR, Role.AUTHORIZED_OFFICIAL})
#: operation -> (roles allowed to proceed, success status code)
MATRIX: dict[str, tuple[frozenset[Role], int]] = {
    "create_incident": (MODS, 201),
    "moderate_report": (MODS, 200),
    "confirm_stairwell": (MODS, 200),
    "hide_message": (MODS, 200),
    "create_notice": (frozenset({Role.AUTHORIZED_OFFICIAL}), 201),
    "demo_add_closure": (MODS, 201),
    "role_self_assign": (frozenset(), 0),  # never proceeds: roles come only from admin config
}
#: audit action written by a successful call (None: operation is not audited)
AUDIT_ON_SUCCESS = {
    "moderate_report": "moderation:verify",
    "confirm_stairwell": "moderation:verify",
    "hide_message": "moderation:hide_message",
    "create_notice": "official_notice",
}
ROLE_REQUESTS = ("moderator", "authorized_official", "Moderator", " AUTHORIZED_OFFICIAL ",
                 "user", "guest", "admin")
PRIVILEGED_REQUESTS = {"moderator", "authorized_official"}


@functools.lru_cache(maxsize=1)
def _env():
    import os

    os.environ.setdefault("SAFEPATH_ALIAS_SECRET", "test-alias-secret")
    repo = MemoryRepository()
    auth = DemoTokenAuthProvider(SECRET, roles={MOD: Role.MODERATOR, OFF: Role.AUTHORIZED_OFFICIAL})
    app = create_app(Settings(profile="local", demo_mode=True, demo_autoseed=False))
    app.state.repository = repo
    app.state.auth = auth
    app.state.audit = AuditWriter(repo)
    app.state.accounts = AccountService(repo)
    app.state.event_bus = None
    app.state.publisher = InProcessPublisher()
    app.state.routing_context_provider = RepositoryContextProvider(repo)
    app.state.routing_service = None
    app.state.graph = merge_indoor_runtime(Graph.load(FIXTURE_ROOT))
    app.state.rate_limiter = InMemoryRateLimiter()
    from app.services.demo import DemoService

    app.state.demo_service = DemoService(app)
    return TestClient(app, raise_server_exceptions=False), app, repo, auth


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _as(auth, sub: str) -> dict[str, str]:
    return _bearer(auth.issue_token(sub)[0])


def _ok(resp, code: int) -> dict[str, Any]:
    assert resp.status_code == code, resp.text
    return resp.json()


# ------------------------------------------------------------------ principals
PRINCIPAL_KINDS = ("none", "expired", "invalid", "guest", "user", "moderator", "official")
ROLE_OF = {"guest": Role.GUEST, "user": Role.USER, "moderator": Role.MODERATOR,
           "official": Role.AUTHORIZED_OFFICIAL}
INVALID_KINDS = ("garbage", "tampered_sig", "tampered_body", "other_secret", "wrong_scheme", "empty")


def _principal_headers(auth, kind: str, detail: Any) -> tuple[dict[str, str], str | None]:
    """Headers for one principal plus its subject (None when unauthenticated)."""
    if kind == "none":
        return {}, None
    if kind == "expired":
        is_guest, seconds = detail
        tok, _ = auth.issue_token(_uid("exp"), is_guest=is_guest, ttl=timedelta(seconds=-seconds))
        return _bearer(tok), None
    if kind == "invalid":
        variant, garbage = detail
        good = auth.issue_token(MOD)[0]  # forging a privileged token must fail too
        body, sig = good.split(".")
        if variant == "garbage":
            return _bearer(garbage or "x"), None
        if variant == "tampered_sig":
            return _bearer(f"{body}.{'0' * len(sig)}" if sig != "0" * len(sig) else f"{body}.{'1' * len(sig)}"), None
        if variant == "tampered_body":
            return _bearer(f"{body}A.{sig}"), None
        if variant == "other_secret":
            other = DemoTokenAuthProvider("not-the-server-secret", roles={MOD: Role.MODERATOR})
            return _bearer(other.issue_token(MOD)[0]), None
        if variant == "wrong_scheme":
            return {"Authorization": f"Basic {good}"}, None
        return {"Authorization": "Bearer "}, None
    if kind == "guest":
        g = auth.issue_guest()
        return _bearer(g.token), g.principal.subject
    sub = {"user": _uid("usr"), "moderator": MOD, "official": OFF}[kind]
    return _as(auth, sub), sub


principals = st.sampled_from(PRINCIPAL_KINDS).flatmap(lambda k: st.tuples(st.just(k), {
    "expired": st.tuples(st.booleans(), st.integers(min_value=1, max_value=10 * 24 * 3600)),
    "invalid": st.tuples(st.sampled_from(INVALID_KINDS),
                         st.text(alphabet="abcdefXYZ0123456789.-_", max_size=40)),
}.get(k, st.none())))


# ------------------------------------------------------------------ operations
def _incident(client, auth, simulated: bool = True) -> str:
    return _ok(client.post("/api/incidents", json={"incident_type": "flood", "source": "authz test",
                                                   "simulation_flag": simulated},
                           headers=_as(auth, MOD)), 201)["incident_id"]


def _report(client, auth, iid: str, category: str) -> str:
    return _ok(client.post("/api/reports", json={"incident_id": iid, "category": category,
                                                 "description": "authz", **POINT},
                           headers=_as(auth, _uid("rep"))), 201)["report_id"]


def _prepare(op: str, client, app, auth, role_req: str) -> dict[str, Any]:
    """Create the prerequisites of one call; returns the request to send."""
    if op == "create_incident":
        return {"method": "post", "url": "/api/incidents",
                "json": {"incident_type": "earthquake", "source": "authz", "simulation_flag": True}}
    if op in ("moderate_report", "confirm_stairwell"):
        iid = _incident(client, auth)
        cat = "inaccessible stairwell" if op == "confirm_stairwell" else "blocked road"
        rid = _report(client, auth, iid, cat)
        return {"method": "post", "url": f"/api/reports/{rid}/moderate", "json": {"action": "verify"},
                "iid": iid, "target": rid}
    if op == "hide_message":
        iid = _incident(client, auth)
        mid = _ok(client.post("/api/messages", json={"incident_id": iid, "message_type": "update",
                                                     "content": "authz"},
                              headers=_as(auth, _uid("msg"))), 201)["message"]["message_id"]
        return {"method": "post", "url": f"/api/messages/{mid}/hide", "iid": iid, "target": mid}
    if op == "create_notice":
        iid = _incident(client, auth)
        return {"method": "post", "url": "/api/notices", "iid": iid, "json": {
            "incident_id": iid, "notice_type": "road_closure", "text": "Road closed (authz test)",
            "source_organization": "Test Agency", "source_url": "https://example.org/notice",
            "source_record_id": _uid("src"), "simulation_flag": True}}
    if op == "demo_add_closure":
        iid = _incident(client, auth)
        edge = sorted(app.state.graph.edge_ids)[0]
        return {"method": "post", "url": "/api/demo/closures", "iid": iid,
                "json": {"incident_id": iid, "edge_ids": [edge]}}
    assert op == "role_self_assign"
    return {"method": "post", "url": "/api/auth/role-request", "json": {"role": role_req}}


def _send(client, req: dict[str, Any], headers: dict[str, str]):
    kw: dict[str, Any] = {"headers": headers}
    if "json" in req:
        kw["json"] = req["json"]
    return getattr(client, req["method"])(req["url"], **kw)


def _audit_rows(repo) -> dict[tuple, dict]:
    return copy.deepcopy(repo._data.get(AUDIT_TABLE, {}))


def _non_audit_state(repo) -> dict[str, dict]:
    return {t: copy.deepcopy(rows) for t, rows in repo._data.items() if t != AUDIT_TABLE}


def _check_effect(op: str, req: dict[str, Any], body: dict[str, Any], client, repo) -> None:
    """The authorized call really happened."""
    if op == "create_incident":
        assert client.get(f"/api/incidents/{body['incident_id']}").status_code == 200
    elif op in ("moderate_report", "confirm_stairwell"):
        assert body["verification_status"] == "Officially_Verified"
        assert repo.get("reports", {"pk": req["iid"], "sk": req["target"]})["verification_status"] \
            == "Officially_Verified"
    elif op == "hide_message":
        assert body["hidden"] is True
        listed = client.get("/api/messages", params={"incident_id": req["iid"]}).json()["messages"]
        assert req["target"] not in [m["message_id"] for m in listed]
    elif op == "create_notice":
        listed = client.get("/api/notices", params={"incident_id": req["iid"]}).json()
        assert body["notice_id"] in [n["notice_id"] for n in listed]
    elif op == "demo_add_closure":
        assert body["incident_id"] == req["iid"] and body["restriction_id"]


@given(op=st.sampled_from(sorted(MATRIX)), principal=principals,
       role_req=st.sampled_from(ROLE_REQUESTS))
def test_authorization_matrix(op, principal, role_req):
    # Feature: safepath-evacuation-navigation, Property 29: Authorization matrix
    client, app, repo, auth = _env()
    kind, detail = principal
    req = _prepare(op, client, app, auth, role_req)
    headers, subject = _principal_headers(auth, kind, detail)

    before_state, before_audit = _non_audit_state(repo), _audit_rows(repo)
    resp = _send(client, req, headers)
    new_audit = [r for k, r in _audit_rows(repo).items() if k not in before_audit]

    allowed, success = MATRIX[op]
    if kind in ("none", "expired", "invalid"):
        assert resp.status_code == 401, (op, kind, resp.text)
    elif ROLE_OF[kind] not in allowed:
        assert resp.status_code == 403, (op, kind, resp.text)
    else:
        body = _ok(resp, success)
        _check_effect(op, req, body, client, repo)
        action = AUDIT_ON_SUCCESS.get(op)
        if action is not None:
            mine = [r for r in new_audit if r["action"] == action]
            assert len(mine) == 1 and mine[0]["actor_id"] == subject
            assert mine[0]["actor_role"] == ROLE_OF[kind].value and mine[0]["ts"]
        return

    # Rejected: nothing persisted.
    assert _non_audit_state(repo) == before_state, (op, kind)
    escalation = (op == "role_self_assign" and resp.status_code == 403
                  and role_req.strip().lower() in PRIVILEGED_REQUESTS)
    if escalation:  # Req 22.4: rejected self-assignment is audited (who, what, when)
        assert len(new_audit) == 1, new_audit
        entry = new_audit[0]
        assert entry["action"] == "role_escalation_rejected"
        assert entry["actor_id"] == subject and entry["actor_role"] == ROLE_OF[kind].value
        assert entry["target_id"] == role_req.strip().lower() and entry["ts"]
    else:
        assert new_audit == [], (op, kind, new_audit)
    if op == "role_self_assign" and subject is not None:
        # the caller's role is unchanged after the attempt
        me = client.get("/api/auth/me", headers=headers).json()
        assert me["role"] == ROLE_OF[kind].value


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@given(start=st.datetimes(min_value=datetime(2024, 1, 1), max_value=datetime(2030, 1, 1),
                          timezones=st.just(timezone.utc)),
       elapsed=st.integers(min_value=0, max_value=48 * 3600))
def test_guest_sessions_expire_exactly_24_hours_after_creation(start, elapsed):
    # Feature: safepath-evacuation-navigation, Property 29: Authorization matrix
    start = start.replace(microsecond=0)  # token expiry is carried with second precision
    clock = _Clock(start)
    auth = DemoTokenAuthProvider(SECRET, clock=clock)
    g = auth.issue_guest()
    assert g.expires_at == start + timedelta(hours=24)
    clock.now = start + timedelta(seconds=elapsed)
    if elapsed < 24 * 3600:
        p = auth.verify(g.token)
        assert p.role is Role.GUEST and p.is_guest
    else:
        with pytest.raises(Unauthenticated):
            auth.verify(g.token)
