"""Property 40: scenario reset is complete, isolated and idempotent (task 11.5; Req 40.3, 40.4, 40.7).

Random SIMULATED activity (server-side steps, ad hoc closures, reports, messages, check-ins and
blocked-stairwell actor actions) runs against one or more seeded demo scenarios, then one scenario
is reset through the real :class:`DemoService`. The reset scenario must match its seeded state
(ignoring generated ids and timestamps), every other scenario's records must be byte-for-byte
unchanged, a second reset must be a successful no-op, and the reset must finish within 30 s.
"""
from __future__ import annotations

import copy
import re
import time
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.adapters import InProcessPublisher, MemoryRepository, Principal, Role
from app.auth.accounts import USERS_TABLE, AccountService
from app.auth.audit import AuditWriter
from app.auth.provider import DemoTokenAuthProvider
from app.communication.messaging import MESSAGE_TYPES
from app.config import Settings
from app.main import create_app
from app.routing.graph import Graph
from app.routing.indoor import merge_indoor_runtime
from app.services import demo_data as dd
from app.services.demo import RESET_LIMIT_SECONDS, DemoError, DemoService
from app.services.overlays import DuplicateRestriction, RepositoryContextProvider
from app.services.reports import CATEGORIES

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "graph"
GRAPH = merge_indoor_runtime(Graph.load(FIXTURE_ROOT))
DEFS = dd.load_definitions()
SIDS = dd.scenario_ids()
INCIDENT_OF = {s["scenario_id"]: s["incident_id"] for s in DEFS["scenarios"]}
USERS_OF = {s["scenario_id"]: [u["user_id"] for u in s.get("users", [])] for s in DEFS["scenarios"]}
STREET_EDGES = [GRAPH.edge_ids[i] for i in range(GRAPH.num_edges) if GRAPH.edge(i).edge_kind == "street"]
NODE_POINTS = [(float(GRAPH.node_lat[i]), float(GRAPH.node_lon[i])) for i in range(0, len(GRAPH.node_lat), 7)]
MODERATOR = Principal(subject=DEFS["moderator"]["user_id"], role=Role.MODERATOR)


def _seed_template() -> dict:
    repo = MemoryRepository()
    dd.seed(repo, graph=GRAPH)
    return copy.deepcopy(repo._data)


TEMPLATE = _seed_template()


@pytest.fixture(scope="module", autouse=True)
def _alias_secret():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SAFEPATH_ALIAS_SECRET", "test-alias-secret")
        yield


def build():
    """A demo-mode app over a fresh copy of the seeded template state."""
    repo = MemoryRepository()
    repo._data = copy.deepcopy(TEMPLATE)
    app = create_app(Settings(demo_mode=True, demo_autoseed=False, profile="local"))
    app.state.repository = repo
    app.state.auth = DemoTokenAuthProvider("t-secret", roles={})
    app.state.audit = AuditWriter(repo)
    app.state.event_bus = None
    app.state.publisher = InProcessPublisher()
    app.state.routing_context_provider = RepositoryContextProvider(repo)
    app.state.routing_service = None
    app.state.graph = GRAPH
    app.state.accounts = AccountService(repo)
    demo = app.state.demo_service = DemoService(app)
    demo.seed_state = "seeded"
    return app, repo, demo


# ------------------------------------------------------------------------- snapshots
def _rows(repo, table, pred):
    return {k: v for k, v in repo._data.get(table, {}).items()
            if all(v.get(f) == x for f, x in pred.items())}


def scenario_records(repo, sid) -> dict:
    """Every raw record attributed to one scenario (keyed by table)."""
    iid = INCIDENT_OF[sid]
    out = {t: _rows(repo, t, {"pk": iid}) for t in dd.INCIDENT_TABLES}
    out.update({t: _rows(repo, t, {"incident_id": iid}) for t in dd.INCIDENT_ATTR_TABLES})
    report_ids = {r.get("report_id") for r in out["reports"].values()}
    out["report_votes"] = {k: v for k, v in repo._data.get("report_votes", {}).items()
                           if v.get("pk") in report_ids}
    out["incidents"] = _rows(repo, "incidents", {"pk": "all", "sk": iid})
    out["demo"] = _rows(repo, dd.DEMO_TABLE, {"sk": sid})  # resolved row and progress row
    out["users"] = {k: v for k, v in repo._data.get(USERS_TABLE, {}).items() if v.get("pk") in USERS_OF[sid]}
    return copy.deepcopy(out)


_GEN_ID = re.compile(r"^[a-z]{3,8}_[0-9A-HJKMNP-TV-Z]{26}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_VOLATILE = {"alias_seed", "sk", "ttl"}  # generated / time-derived fields


def _norm(obj):
    if isinstance(obj, dict):
        return {k: _norm(v) for k, v in obj.items() if k not in _VOLATILE and not k.endswith("_at")}
    if isinstance(obj, list):
        return [_norm(v) for v in obj]
    if isinstance(obj, str) and (_GEN_ID.match(obj) or _TS.match(obj)):
        return "<generated>"
    return obj


def seeded_fingerprint(records: dict) -> dict:
    """Records with generated ids/timestamps masked; reset bookkeeping events ignored."""
    out = {}
    for table, rows in records.items():
        vals = [_norm(v) for v in rows.values()]
        if table == "events":
            vals = [v for v in vals if v.get("type") != "scenario_reset"]
        out[table] = sorted(vals, key=repr)
    return out


SEED_FINGERPRINT = {sid: seeded_fingerprint(scenario_records(type("R", (), {"_data": TEMPLATE})(), sid))
                    for sid in SIDS}


# ------------------------------------------------------------------------- activity
op_strategy = st.one_of(
    st.tuples(st.just("step"), st.integers(0, len(SIDS) - 1), st.integers(0, 50)),
    st.tuples(st.just("closure"), st.integers(0, len(SIDS) - 1), st.integers(0, len(STREET_EDGES) - 1)),
    st.tuples(st.just("report"), st.integers(0, len(SIDS) - 1),
              st.tuples(st.integers(0, len(CATEGORIES) - 1), st.integers(0, len(NODE_POINTS) - 1),
                        st.integers(0, 3))),
    st.tuples(st.just("message"), st.integers(0, len(SIDS) - 1),
              st.tuples(st.integers(0, len(MESSAGE_TYPES) - 1), st.booleans(), st.integers(0, 3))),
    st.tuples(st.just("checkin"), st.integers(0, len(SIDS) - 1),
              st.tuples(st.sampled_from(["safe", "need_assistance"]),
                        st.sampled_from(["private", "incident-members", "public"]), st.booleans(),
                        st.integers(0, 3))),
    st.tuples(st.just("stair"), st.just(SIDS.index("blocked_stairwell")),
              st.tuples(st.sampled_from(["A", "B", "C"]), st.sampled_from(
                  ("report", "corroborate", "dispute", "message", "confirm", "resolve")))),
)


def run_op(app, repo, demo, op) -> None:
    from app.api.routers.checkins import get_checkin_service
    from app.api.routers.messages import get_messaging_service
    from app.api.routers.reports import get_report_service

    kind, sid_i, arg = op
    sid = SIDS[sid_i]
    iid = INCIDENT_OF[sid]
    who = lambda n: Principal(subject=f"sim-p40-user-{n}", role=Role.USER)  # noqa: E731
    if kind == "step":
        steps = dd.get_scenario(repo, sid)["steps"]
        demo.apply_step(sid, steps[arg % len(steps)]["id"])
    elif kind == "closure":
        try:
            demo.add_closure(iid, edge_ids=[STREET_EDGES[arg]], geometry=None, kind="closure")
        except DuplicateRestriction:  # same-millisecond ad hoc id; nothing was written
            pass
    elif kind == "report":
        cat, pt, n = arg
        get_report_service(app).create(who(n), {
            "incident_id": iid, "category": CATEGORIES[cat], "latitude": NODE_POINTS[pt][0],
            "longitude": NODE_POINTS[pt][1], "description": "SIMULATED property-test report"})
    elif kind == "message":
        mt, share, n = arg
        lat, lon = NODE_POINTS[0]
        get_messaging_service(app).create(who(n), {
            "incident_id": iid, "message_type": MESSAGE_TYPES[mt], "content": "SIMULATED property-test message",
            "share_location": share, "latitude": lat, "longitude": lon})
    elif kind == "checkin":
        status, vis, share, n = arg
        lat, lon = NODE_POINTS[0]
        body = {"incident_id": iid, "status": status, "visibility": vis, "share_location": share}
        if share:
            body["location"] = {"latitude": lat, "longitude": lon}
        get_checkin_service(app).create(who(n), body)
    elif kind == "stair":
        actor, action = arg
        is_mod = action in ("confirm", "resolve")
        try:
            demo.act(sid, MODERATOR, "moderator" if is_mod else actor, action)
        except DemoError:  # e.g. nothing to corroborate yet, duplicate vote, invalid transition
            pass


# Feature: safepath-evacuation-navigation, Property 40: Scenario reset is complete, isolated and idempotent
@given(ops=st.lists(op_strategy, max_size=12), target=st.sampled_from(SIDS))
def test_scenario_reset_is_complete_isolated_and_idempotent(ops, target):
    """**Validates: Requirements 40.3, 40.4, 40.7**"""
    app, repo, demo = build()
    for op in ops:
        run_op(app, repo, demo, op)

    others_before = {sid: scenario_records(repo, sid) for sid in SIDS if sid != target}
    was_dirty = not dd.is_seeded_state(repo, target)

    started = time.monotonic()
    first = demo.reset(target)
    elapsed = time.monotonic() - started

    # complete: the reset scenario equals its seeded state, all SIMULATED (40.3, 40.4)
    assert first["within_limit"] is True and elapsed <= RESET_LIMIT_SECONDS
    assert first["changed"] is was_dirty and first["pristine"] is True
    assert dd.is_seeded_state(repo, target)
    after_first = scenario_records(repo, target)
    assert seeded_fingerprint(after_first) == SEED_FINGERPRINT[target]
    assert not after_first["reports"] and not after_first["messages"] and not after_first["checkins"]
    assert not any(r["sk"] == target for r in _rows(repo, dd.DEMO_TABLE, {"pk": "progress"}).values())
    for inc in after_first["incidents"].values():
        assert inc["simulation_flag"] is True and inc["scenario_id"] == target

    # isolated: every other scenario's records are untouched (40.3)
    for sid, before in others_before.items():
        assert scenario_records(repo, sid) == before, sid

    # idempotent: resetting again changes nothing and reports success (40.7)
    snapshot = copy.deepcopy(repo._data)
    second = demo.reset(target)
    assert second["changed"] is False and second["pristine"] is True and second["within_limit"] is True
    assert second["event"] is None
    assert repo._data == snapshot
