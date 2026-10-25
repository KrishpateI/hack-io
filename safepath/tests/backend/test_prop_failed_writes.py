"""Property 35: failed writes leave no trace.

For any state-changing operation (report creation, corroborate/dispute votes, messages,
check-ins, report moderation and message hiding) and any injected repository failure at any
point of the write path (including part-way through ``Repository.transact``), the response is
503 with the error envelope, no partial record or history entry exists and no realtime event is
published. For any successful write, the published events carry unique ``event_id`` values for
the related Incident and are exactly the events stored by that write.

Faults are injected by ``FaultyRepository``, which wraps a real ``MemoryRepository`` or
``SqliteRepository`` and fails the N-th write call of the operation under test. When that call
is a ``transact``, the fault fires inside the backing store after a drawn number of the
transaction's operations have been applied, so the store's own atomicity is exercised.

**Validates: Requirements 25.1, 30.9**
"""
# Feature: safepath-evacuation-navigation, Property 35: Failed writes leave no trace
from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from hypothesis import given, note, strategies as st

from app.adapters import InProcessPublisher, MemoryRepository, Role, SqliteRepository
from app.adapters.ports import RepositoryUnavailable
from app.api.ratelimit import InMemoryRateLimiter
from app.auth.deps import install_auth
from app.auth.provider import DemoTokenAuthProvider
from app.communication.events import EVENTS_TABLE
from app.communication.messaging import MESSAGE_TYPES
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph
from app.routing.indoor import merge_indoor_runtime
from app.services.overlays import RepositoryContextProvider
from app.services.reports import CATEGORIES

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
GRAPH = merge_indoor_runtime(Graph.load(FIXTURE_ROOT))
POINT = {"latitude": 41.8708, "longitude": -87.6505}  # on fixture-graph streets (edges in 25 m)

OPS = ("create_report", "confirm", "dispute", "message", "checkin", "verify", "resolve", "hide")
# Exact number of events a successful write publishes (None: at least one, moderation may add an
# overlay event alongside the status change).
EXPECTED_EVENTS = {"create_report": 1, "confirm": 1, "dispute": 1, "message": 1, "hide": 1,
                   "verify": None, "resolve": None}


# ------------------------------------------------------------------ fault injection
class FaultyRepository:
    """Repository wrapper that fails the ``fail_at``-th write call (0-based) once armed.

    Reads pass through. A failing ``transact`` aborts inside the backing store after
    ``mid_op % len(ops)`` operations were applied (sqlite3 error for SQLite, which the store maps
    to ``RepositoryUnavailable``; ``RepositoryUnavailable`` directly for the in-memory store).
    """

    def __init__(self, inner: MemoryRepository | SqliteRepository) -> None:
        self.inner = inner
        self.armed = False
        self.fail_at: int | None = None
        self.mid_op = 0
        self.writes = 0
        self.fired: str | None = None  # description of the call that failed

    def arm(self, fail_at: int | None, mid_op: int) -> None:
        self.armed, self.fail_at, self.mid_op, self.writes, self.fired = True, fail_at, mid_op, 0, None

    def _trip(self, what: str) -> bool:
        if not self.armed:
            return False
        n = self.writes
        self.writes += 1
        if self.fail_at is not None and n == self.fail_at and self.fired is None:
            self.fired = f"write #{n}: {what}"
            return True
        return False

    # reads
    def get(self, table, key):
        return self.inner.get(table, key)

    def query(self, table, key_cond, *, index=None, since=None, limit=100):
        return self.inner.query(table, key_cond, index=index, since=since, limit=limit)

    # writes
    def put(self, table, item, *, if_absent=False):
        if self._trip(f"put {table}"):
            raise RepositoryUnavailable(f"injected fault on put {table}")
        return self.inner.put(table, item, if_absent=if_absent)

    def update_cas(self, table, key, expected_version, patch):
        if self._trip(f"update_cas {table}"):
            raise RepositoryUnavailable(f"injected fault on update_cas {table}")
        return self.inner.update_cas(table, key, expected_version, patch)

    def delete_where(self, table, predicate):
        if self._trip(f"delete_where {table}"):
            raise RepositoryUnavailable(f"injected fault on delete_where {table}")
        return self.inner.delete_where(table, predicate)

    def transact(self, ops):
        if not ops or not self._trip("transact [" + ", ".join(f"{o.kind} {o.table}" for o in ops) + "]"):
            return self.inner.transact(ops)
        k = self.mid_op % len(ops)
        self.fired += f" (aborted at op {k})"
        core = self.inner._core
        original = core.apply
        exc = (sqlite3.OperationalError if isinstance(self.inner, SqliteRepository)
               else RepositoryUnavailable)
        applied = [0]

        def failing_apply(data, op):
            if applied[0] == k:
                raise exc("injected fault mid-transaction")
            applied[0] += 1
            return original(data, op)

        core.apply = failing_apply
        try:
            self.inner.transact(ops)
        finally:
            del core.apply  # restore the class method
        raise AssertionError("injected mid-transaction fault did not abort the transaction")


def snapshot(repo: FaultyRepository) -> object:
    """Complete persisted state of the backing store."""
    inner = repo.inner
    if isinstance(inner, SqliteRepository):
        return inner._db.execute("SELECT tbl, k, body FROM items ORDER BY tbl, k").fetchall()
    return copy.deepcopy(inner._data)


def stored_event_ids(repo: FaultyRepository, incident_id: str) -> set[str]:
    return {r["event_id"] for r in repo.inner.query(EVENTS_TABLE, {"pk": incident_id}, limit=100_000)}


# ------------------------------------------------------------------ environment
def build_env(backend: str):
    inner = MemoryRepository() if backend == "memory" else SqliteRepository(":memory:")
    repo = FaultyRepository(inner)
    auth = DemoTokenAuthProvider("p35-secret", roles={"mod": Role.MODERATOR})
    app = create_app(Settings())
    install_auth(app, auth=auth, repository=repo)
    app.state.event_bus = None
    publisher = app.state.publisher = InProcessPublisher()
    app.state.routing_context_provider = RepositoryContextProvider(repo)
    app.state.routing_service = None
    app.state.graph = GRAPH
    app.state.rate_limiter = InMemoryRateLimiter()
    client = TestClient(app, raise_server_exceptions=False)
    return client, repo, auth, publisher


def hdr(auth: DemoTokenAuthProvider, subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth.issue_token(subject)[0]}"}


def ok(resp, *codes: int):
    assert resp.status_code in codes, resp.text
    return resp.json()


# ------------------------------------------------------------------ strategies
_TEXT = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=1, max_size=40).filter(str.strip)
_JITTER = st.floats(-0.0002, 0.0002, allow_nan=False, allow_infinity=False)


@st.composite
def scenarios(draw):
    op = draw(st.sampled_from(OPS))
    s = {
        "backend": draw(st.sampled_from(["memory", "sqlite"])),
        "op": op,
        # None: no fault (success branch); otherwise the 0-based write call to fail.
        "fail_at": draw(st.one_of(st.none(), st.integers(0, 2), st.integers(0, 2))),
        "mid_op": draw(st.integers(0, 6)),
    }
    if op == "create_report":
        s["report"] = {
            "category": draw(st.sampled_from(CATEGORIES)),
            "description": draw(st.one_of(st.just(""), _TEXT)),
            "latitude": POINT["latitude"] + draw(_JITTER),
            "longitude": POINT["longitude"] + draw(_JITTER),
        }
        if draw(st.booleans()):
            s["report"]["client_report_id"] = draw(_TEXT)
    elif op in ("confirm", "dispute"):
        s["prior_vote"] = draw(st.booleans())  # a second vote crosses the threshold (history entry)
    elif op == "message":
        s["message"] = {"message_type": draw(st.sampled_from(MESSAGE_TYPES)), "content": draw(_TEXT)}
        if draw(st.booleans()):
            s["message"].update(share_location=True, **POINT)
    elif op == "checkin":
        share = draw(st.booleans())
        s["pref_share"] = share
        s["checkin"] = {
            "status": draw(st.sampled_from(["safe", "need_assistance", "cannot_reach_destination",
                                            "route_blocked"])),
            "visibility": draw(st.sampled_from(["private", "incident-members", "public"])),
        }
        if share or draw(st.booleans()):
            s["checkin"].update(share_location=share,
                                location={"latitude": POINT["latitude"], "longitude": POINT["longitude"]})
    return s


# ------------------------------------------------------------------ property
@given(scenarios())
def test_failed_writes_leave_no_trace(s):
    client, repo, auth, publisher = build_env(s["backend"])

    # ---- setup (no faults): incident, a report, a message
    inc = ok(client.post("/api/incidents", json={"incident_type": "flood", "source": "p35",
                                                 "simulation_flag": True},
                         headers=hdr(auth, "mod")), 201)["incident_id"]
    rid = ok(client.post("/api/reports", json={"incident_id": inc, "category": "blocked road",
                                                "description": "base", **POINT},
                         headers=hdr(auth, "rep")), 201)["report_id"]
    mid = ok(client.post("/api/messages", json={"incident_id": inc, "message_type": "update",
                                                 "content": "base"},
                         headers=hdr(auth, "u1")), 201)["message"]["message_id"]
    op = s["op"]
    if op in ("confirm", "dispute") and s["prior_vote"]:
        kind = "corroborate" if op == "confirm" else "dispute"
        ok(client.post(f"/api/reports/{rid}/{kind}", headers=hdr(auth, "v0")), 200)
    if op == "resolve":
        ok(client.post(f"/api/reports/{rid}/moderate", json={"action": "verify"},
                       headers=hdr(auth, "mod")), 200)
    if op == "checkin":  # provisions the account and sets the privacy preference
        ok(client.put("/api/auth/me/privacy", json={"share_location": s["pref_share"]},
                      headers=hdr(auth, "c1")), 200)

    # ---- operation under fault injection
    before = snapshot(repo)
    events_before = stored_event_ids(repo, inc)
    published_before = len(publisher.published)
    repo.arm(s["fail_at"], s["mid_op"])

    if op == "create_report":
        resp = client.post("/api/reports", json={"incident_id": inc, **s["report"]},
                           headers=hdr(auth, "rep2"))
    elif op in ("confirm", "dispute"):
        kind = "corroborate" if op == "confirm" else "dispute"
        resp = client.post(f"/api/reports/{rid}/{kind}", headers=hdr(auth, "v1"))
    elif op == "message":
        resp = client.post("/api/messages", json={"incident_id": inc, **s["message"]},
                           headers=hdr(auth, "u2"))
    elif op == "checkin":
        resp = client.post("/api/checkins", json={"incident_id": inc, **s["checkin"]},
                           headers=hdr(auth, "c1"))
    elif op in ("verify", "resolve"):
        resp = client.post(f"/api/reports/{rid}/moderate", json={"action": op},
                           headers=hdr(auth, "mod"))
    else:  # hide
        resp = client.post(f"/api/messages/{mid}/hide", headers=hdr(auth, "mod"))

    repo.armed = False
    after = snapshot(repo)
    new_published = publisher.published[published_before:]
    new_events = stored_event_ids(repo, inc) - events_before
    note(f"op={op} backend={s['backend']} fault={repo.fired} status={resp.status_code} "
         f"published={[e.type for _, e in new_published]}")

    if resp.status_code == 503:
        # Failed write: proper error, nothing persisted, nothing published.
        assert repo.fired is not None, "503 without an injected fault"
        assert resp.json()["error"]["code"] == "service_unavailable", resp.text
        assert after == before, f"{op}: 503 response but persisted state changed ({repo.fired})"
        assert new_events == set(), f"{op}: 503 response but event rows stored: {new_events}"
        assert new_published == [], (
            f"{op}: 503 response but published {[e.type for _, e in new_published]} ({repo.fired})")
        return

    # Successful write (no fault, or the fault hit a best-effort step after the commit).
    assert resp.status_code in (200, 201), f"{op}: unexpected {resp.status_code} {resp.text}"
    assert after != before, f"{op}: success response but nothing was persisted"
    ids = [e.id for _, e in new_published]
    assert len(ids) == len(set(ids)), f"duplicate event ids published: {ids}"
    assert all(i == inc and e.incident_id == inc for i, e in new_published)
    assert set(ids) == new_events, "published events differ from the events stored by the write"
    if op == "checkin":
        expected = 1 if s["checkin"]["visibility"] == "public" else 0
        assert len(ids) == expected
    elif EXPECTED_EVENTS[op] is None:
        assert len(ids) >= 1
    else:
        assert len(ids) == EXPECTED_EVENTS[op], f"{op}: published {len(ids)} events"
