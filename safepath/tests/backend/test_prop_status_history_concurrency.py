"""Property 26: status history is append-only and consistent under concurrency.

**Validates: Requirements 24.7, 24.10**

A batch of status-change requests (confirm / dispute votes, moderator verify / resolve) on one
report runs on worker threads against MemoryRepository and SqliteRepository. Interleavings are
either chosen deterministically by Hypothesis (a scheduler grants one Repository call at a time)
or left to the OS (real threads released together by a barrier). Every committed write to the
report is captured in server receipt (commit) order, and then:

* each commit bumps ``version`` by exactly one and only appends to ``status_history``
  (no entry is modified or removed);
* every accepted request commits exactly once and rejected requests leave no trace;
* the history has exactly one entry per accepted status change, each with a timestamp, the
  acting role and the previous status, chained consistently;
* replaying the accepted requests sequentially in commit order on a fresh store accepts all of
  them and yields the same final report (status, counters, version, history) and overlays.
"""
# Feature: safepath-evacuation-navigation, Property 26: Status history is append-only and consistent under concurrency
from __future__ import annotations

import copy
import threading
from typing import Any

from hypothesis import given, settings, strategies as st

from app.adapters import InProcessPublisher, MemoryRepository
from app.adapters.ports import Principal, Role
from app.adapters.repository import SqliteRepository
from app.communication.events import EventBus
from app.models.domain import VerificationStatus as VS
from app.services.overlays import OVERLAYS_TABLE, OverlayService
from app.services.reports import REPORTS_TABLE, ReportService
from app.services.verification import VerificationService
from strategies import NOW

INC = "inc_p26"
REPORTER = "rep"
SECRET = "p26-secret"
POINT = {"latitude": 41.8708, "longitude": -87.6505}
STEP_TIMEOUT = 10.0

_ROLE = {"confirm": Role.USER, "dispute": Role.USER,
         "verify": Role.MODERATOR, "resolve": Role.MODERATOR}


# ------------------------------------------------------------------ harness
class _Scheduler:
    """Runs exactly one worker at a time, switching only at Repository calls.

    ``choices`` picks which waiting worker gets the next Repository call, so Hypothesis
    controls (and can shrink) the interleaving."""

    def __init__(self, choices: list[int]) -> None:
        self.cond = threading.Condition()
        self.choices = choices
        self.step = 0
        self.turn: int | None = None
        self.waiting: set[int] = set()
        self.done: set[int] = set()

    def gate(self, wid: int) -> None:
        with self.cond:
            self.waiting.add(wid)
            if self.turn == wid:
                self.turn = None
            self.cond.notify_all()
            if not self.cond.wait_for(lambda: self.turn == wid, timeout=STEP_TIMEOUT):
                raise RuntimeError("scheduler stalled")
            self.waiting.discard(wid)

    def finish(self, wid: int) -> None:
        with self.cond:
            self.done.add(wid)
            self.waiting.discard(wid)
            if self.turn == wid:
                self.turn = None
            self.cond.notify_all()

    def run(self, n: int) -> None:
        while True:
            with self.cond:
                ok = self.cond.wait_for(
                    lambda: self.turn is None and len(self.waiting) + len(self.done) == n,
                    timeout=STEP_TIMEOUT)
                assert ok, "scheduler stalled"
                if len(self.done) == n:
                    return
                ready = sorted(self.waiting)
                pick = self.choices[self.step % len(self.choices)] if self.choices else 0
                self.step += 1
                self.turn = ready[pick % len(ready)]
                self.cond.notify_all()


class _Recorder:
    """Repository proxy: optional scheduling gate per call plus a commit log of the report row.

    Holding ``lock`` across ``transact`` and the follow-up read makes the log order equal to the
    real commit order (server receipt order) for both repository implementations."""

    def __init__(self, inner, report_key: dict, sched: _Scheduler | None) -> None:
        self.inner = inner
        self.report_key = report_key
        self.sched = sched
        self.local = threading.local()
        self.lock = threading.Lock()
        self.log: list[tuple[int | None, dict]] = []  # (worker id, report row after commit)

    def _gate(self) -> None:
        wid = getattr(self.local, "wid", None)
        if self.sched is not None and wid is not None:
            self.sched.gate(wid)

    def put(self, table, item, *, if_absent=False):
        self._gate()
        return self.inner.put(table, item, if_absent=if_absent)

    def get(self, table, key):
        self._gate()
        return self.inner.get(table, key)

    def query(self, table, key_cond, **kw):
        self._gate()
        return self.inner.query(table, key_cond, **kw)

    def update_cas(self, table, key, expected_version, patch):
        self._gate()
        return self.inner.update_cas(table, key, expected_version, patch)

    def delete_where(self, table, predicate):
        self._gate()
        return self.inner.delete_where(table, predicate)

    def transact(self, ops):
        self._gate()
        with self.lock:
            self.inner.transact(ops)  # Conflict propagates; nothing is logged
            if any(op.kind == "update_cas" and op.table == REPORTS_TABLE for op in ops):
                row = self.inner.get(REPORTS_TABLE, self.report_key)
                self.log.append((getattr(self.local, "wid", None), copy.deepcopy(row)))


def _make_repo(kind: str):
    return MemoryRepository() if kind == "memory" else SqliteRepository(":memory:")


def _services(repo) -> VerificationService:
    clock = lambda: NOW  # noqa: E731 - fixed clock makes histories comparable across runs
    bus = EventBus(repo, InProcessPublisher(), clock=clock)
    reports = ReportService(repo, bus, graph_provider=None, clock=clock, alias_secret=SECRET)
    return VerificationService(reports, bus, OverlayService(repo, bus, clock=clock), clock=clock)


def _seed(repo, with_edges: bool) -> dict:
    """Simulated incident plus one Unverified report; returns the report key."""
    repo.put("incidents", {"pk": "all", "sk": INC, "incident_id": INC, "simulation_flag": True})
    svc = _services(repo)
    res = svc.reports.create(Principal(subject=REPORTER, role=Role.USER), {
        "incident_id": INC, "category": "blocked road", "description": "x",
        "client_report_id": "p26", **POINT})
    key = {"pk": INC, "sk": res.report.report_id}
    if with_edges:
        repo.put(REPORTS_TABLE, {**repo.get(REPORTS_TABLE, key), "affected_edge_ids": ["e1", "e2"]})
    return key


def _apply(svc: VerificationService, report_id: str, req: tuple[str, str]) -> bool:
    """Run one request; returns whether it changed the status. Raises on rejection."""
    kind, subject = req
    principal = Principal(subject=subject, role=_ROLE[kind])
    if kind in ("confirm", "dispute"):
        return svc.vote(principal, report_id, kind).status_changed
    svc.moderate(principal, report_id, kind)
    return True


def _run_concurrently(repo_kind: str, with_edges: bool, requests: list[tuple[str, str]],
                      choices: list[int] | None):
    base = _make_repo(repo_kind)
    key = _seed(base, with_edges)
    sched = _Scheduler(choices) if choices is not None else None
    rec = _Recorder(base, key, sched)
    svc = _services(rec)
    initial = copy.deepcopy(base.get(REPORTS_TABLE, key))
    results: dict[int, tuple[str, Any]] = {}
    barrier = threading.Barrier(len(requests))

    def worker(wid: int) -> None:
        rec.local.wid = wid
        try:
            if sched is None:
                barrier.wait(timeout=STEP_TIMEOUT)
            results[wid] = ("ok", _apply(svc, key["sk"], requests[wid]))
        except Exception as exc:  # rejection (409/403) or a genuine failure, checked below
            results[wid] = ("rejected", exc)
        finally:
            if sched is not None:
                sched.finish(wid)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(len(requests))]
    for t in threads:
        t.start()
    if sched is not None:
        sched.run(len(requests))
    for t in threads:
        t.join(timeout=STEP_TIMEOUT * 3)
        assert not t.is_alive(), "worker did not finish"
    final = base.get(REPORTS_TABLE, key)
    overlays = sorted(o["status"] for o in base.query(OVERLAYS_TABLE, {"pk": INC}, limit=1000))
    return key, initial, rec.log, results, final, overlays


_EXPECTED_REJECTIONS = ("DuplicateVote", "OwnReportVote", "InvalidTransition", "ConcurrentUpdate")


def _check(repo_kind, with_edges, requests, key, initial, log, results, final, overlays):
    # 0. only lifecycle rejections; no crashes
    for wid, (status, val) in results.items():
        if status == "rejected":
            assert type(val).__name__ in _EXPECTED_REJECTIONS, (
                f"request {requests[wid]} failed unexpectedly: {val!r}")
    accepted = sorted(w for w, (s, _) in results.items() if s == "ok")

    # 1. one commit per accepted request, none for rejected ones
    committed = [w for w, _ in log]
    assert sorted(committed) == accepted, (
        f"commit log workers {committed} != accepted requests {accepted}")

    # 2. append-only history and +1 version per commit, in commit order
    prev = initial
    for wid, row in log:
        assert row["version"] == prev["version"] + 1
        old_h, new_h = prev["status_history"], row["status_history"]
        assert new_h[:len(old_h)] == old_h, f"history entry modified/removed by {requests[wid]}"
        assert len(new_h) - len(old_h) in (0, 1)
        if row["verification_status"] != prev["verification_status"]:
            assert len(new_h) == len(old_h) + 1
            assert new_h[-1]["status"] == row["verification_status"]
            assert new_h[-1]["previous_status"] == prev["verification_status"]
        else:
            assert len(new_h) == len(old_h)
        prev = row
    assert final == prev, "final row differs from the last committed write"

    # 3. exactly one history entry per accepted status change, with at/role/previous_status
    hist = final["status_history"]
    changes = sum(1 for w in accepted if results[w][1])
    assert len(hist) == 1 + changes
    assert hist[0]["status"] == VS.UNVERIFIED.value
    for a, b in zip(hist, hist[1:]):
        assert b["previous_status"] == a["status"] and b["status"] != a["status"]
        assert b["at"] and b["actor_role"]
    assert hist[-1]["status"] == final["verification_status"]

    # 4. final state == sequential application of accepted requests in receipt order
    replay_repo = _make_repo(repo_kind)
    replay_key = _seed(replay_repo, with_edges)
    assert replay_key == key
    rsvc = _services(replay_repo)
    for wid in committed:
        changed = _apply(rsvc, key["sk"], requests[wid])  # must be accepted again
        assert changed == results[wid][1], f"replayed {requests[wid]} changed={changed}"
    expected = replay_repo.get(REPORTS_TABLE, key)
    for field in ("verification_status", "confirmations", "disputes", "version",
                  "status_history", "source_category"):
        assert final.get(field) == expected.get(field), (
            f"{field}: concurrent={final.get(field)!r} sequential={expected.get(field)!r}")
    assert bool(final.get("restriction_id")) == bool(expected.get("restriction_id"))
    assert overlays == sorted(
        o["status"] for o in replay_repo.query(OVERLAYS_TABLE, {"pk": INC}, limit=1000))


# ------------------------------------------------------------------ strategies
# Small voter pool so duplicate votes, own-report votes and threshold crossings all occur.
_vote = st.tuples(st.sampled_from(["confirm", "dispute"]),
                  st.sampled_from(["v1", "v2", "v3", "v4", REPORTER]))
_mod = st.tuples(st.sampled_from(["verify", "resolve"]), st.just("mod"))
_requests = st.lists(st.one_of(_vote, _vote, _vote, _mod), min_size=2, max_size=6)


@given(repo_kind=st.sampled_from(["memory", "sqlite"]), with_edges=st.booleans(),
       requests=_requests, choices=st.lists(st.integers(0, 5), max_size=60))
def test_status_history_consistent_under_scheduled_interleavings(repo_kind, with_edges,
                                                                requests, choices):
    out = _run_concurrently(repo_kind, with_edges, requests, choices)
    _check(repo_kind, with_edges, requests, *out)


@settings(max_examples=25)
@given(repo_kind=st.sampled_from(["memory", "sqlite"]), with_edges=st.booleans(),
       requests=_requests)
def test_status_history_consistent_under_real_threads(repo_kind, with_edges, requests):
    out = _run_concurrently(repo_kind, with_edges, requests, None)
    _check(repo_kind, with_edges, requests, *out)
