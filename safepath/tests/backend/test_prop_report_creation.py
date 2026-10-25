"""Property 28: report creation, edge association and duplicate linking.

**Validates: Requirements 23.4, 28.3, 41.6**
"""
# Feature: safepath-evacuation-navigation, Property 28: Report creation, edge association and duplicate linking
from __future__ import annotations

import math
from datetime import datetime, timedelta

from hypothesis import assume, given, strategies as st

from app.adapters import MemoryRepository
from app.adapters.ports import Principal, Role
from app.geo import haversine
from app.models.domain import VerificationStatus
from app.routing.graph import _xy
from app.services.reports import (
    DUPLICATE_RADIUS_M, DUPLICATE_WINDOW, EDGE_RADIUS_M, REPORTS_TABLE, ReportService,
)
from strategies import NOW, build_runtime_graph, graph_models

INC = "inc_p28"
BASE_LAT, BASE_LON = 41.8708, -87.6505
_M_PER_DEG_LAT = 111_320.0


def _repo() -> MemoryRepository:
    repo = MemoryRepository()
    repo.put("incidents", {"pk": "all", "sk": INC, "incident_id": INC, "simulation_flag": True})
    return repo


def _principal(subject: str) -> Principal:
    return Principal(subject=subject, role=Role.USER)


def _seg_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Euclidean point-to-segment distance (projected meters), independent of shapely."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


# ------------------------------------------------------------------ edge association (23.4)
@st.composite
def _graph_and_point(draw):
    model = draw(graph_models(2, 10, multilevel=False))
    if draw(st.booleans()):  # near a node, so in-range edges are common
        n = draw(st.sampled_from(model.nodes))
        dn, de = draw(st.floats(-40, 40)), draw(st.floats(-40, 40))
        lat = n.lat + dn / _M_PER_DEG_LAT
        lon = n.lon + de / (_M_PER_DEG_LAT * math.cos(math.radians(n.lat)))
    else:
        lat = draw(st.floats(41.8690, 41.8735))
        lon = draw(st.floats(-87.6510, -87.6465))
    return model, lat, lon


@given(_graph_and_point())
def test_affected_edges_are_exactly_edges_within_radius(case):
    model, lat, lon = case
    graph = build_runtime_graph(model)
    nodes = {n.node_id: n for n in model.nodes}
    p = _xy(lon, lat)
    dists = {}
    for e in model.edges:
        a, b = nodes[e.from_node], nodes[e.to_node]
        dists[e.edge_id] = _seg_dist(p, _xy(a.lon, a.lat), _xy(b.lon, b.lat))
    # WKT stores 7 decimals (~1 cm); skip points numerically on the 25 m boundary.
    assume(all(abs(d - EDGE_RADIUS_M) > 0.05 for d in dists.values()))
    expected = sorted(eid for eid, d in dists.items() if d <= EDGE_RADIUS_M)

    svc = ReportService(_repo(), None, graph_provider=lambda: graph,
                        clock=lambda: NOW, alias_secret="p28-secret")
    res = svc.create(_principal("u1"), {"incident_id": INC, "category": "blocked road",
                                        "latitude": lat, "longitude": lon})
    assert res.created
    assert sorted(res.report.affected_edge_ids) == expected


# ------------------------------------------------------------------ duplicates (28.3) + idempotency (41.6)
_CATEGORIES = ("blocked road", "flooding")
# Small alphabets so repeated (subject, client_report_id) pairs and look-alike pairs both occur.
_SUBJECTS = ("a", "ab", "b")
_CLIENT_IDS = (None, "c", "bc")

_submit = st.tuples(
    st.just("submit"),
    st.sampled_from(_SUBJECTS),
    st.sampled_from(_CLIENT_IDS),
    st.sampled_from(_CATEGORIES),
    st.floats(0, 130, allow_nan=False),  # metres east of the base point
    st.integers(0, 70),                  # minutes the clock advances before this step
)
_resolve = st.tuples(st.just("resolve"), st.integers(0, 50))


@given(st.lists(st.one_of(_submit, _submit, _submit, _resolve), min_size=1, max_size=12))
def test_duplicate_linking_and_idempotent_resubmission(steps):
    repo = _repo()
    clock = [NOW]
    svc = ReportService(repo, None, graph_provider=None, clock=lambda: clock[0],
                        alias_secret="p28-secret")
    stored: list[dict] = []  # model of persisted reports in creation order
    by_key: dict[tuple[str, str], dict] = {}
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(BASE_LAT))

    for step in steps:
        if step[0] == "resolve":
            if not stored:
                continue
            m = stored[step[1] % len(stored)]
            row = repo.get(REPORTS_TABLE, {"pk": INC, "sk": m["id"]})
            repo.put(REPORTS_TABLE, {**row, "verification_status": VerificationStatus.RESOLVED.value})
            m["active"] = False
            continue

        _, subject, client_id, category, east_m, advance = step
        clock[0] = clock[0] + timedelta(minutes=advance)
        now = clock[0]
        lat, lon = BASE_LAT, BASE_LON + east_m / m_per_deg_lon
        payload = {"incident_id": INC, "category": category, "latitude": lat, "longitude": lon}
        if client_id is not None:
            payload["client_report_id"] = client_id
        before = len(repo.query(REPORTS_TABLE, {"pk": INC}, limit=1000))

        key = (subject, client_id) if client_id is not None else None
        if key is not None and key in by_key:
            res = svc.create(_principal(subject), payload)
            assert not res.created, f"resubmission of {key} created a second report"
            assert res.report.report_id == by_key[key]["id"]
            assert len(repo.query(REPORTS_TABLE, {"pk": INC}, limit=1000)) == before
            continue

        candidates = [
            m for m in stored
            if m["category"] == category and m["active"]
            and now - m["created_at"] <= DUPLICATE_WINDOW
            and haversine(lat, lon, m["lat"], m["lon"]) <= DUPLICATE_RADIUS_M
        ]
        assume(all(abs(haversine(lat, lon, m["lat"], m["lon"]) - DUPLICATE_RADIUS_M) > 1e-6
                   for m in stored))

        res = svc.create(_principal(subject), payload)
        assert res.created, f"new submission {(subject, client_id)} was treated as a resubmission"
        rid = res.report.report_id
        assert rid not in {m["id"] for m in stored}
        assert len(repo.query(REPORTS_TABLE, {"pk": INC}, limit=1000)) == before + 1
        if candidates:
            assert res.report.duplicate_of in {m["id"] for m in candidates}, (
                f"expected a duplicate link to one of {[m['id'] for m in candidates]}, "
                f"got {res.report.duplicate_of}")
        else:
            assert res.report.duplicate_of is None

        m = {"id": rid, "category": category, "lat": lat, "lon": lon, "created_at": now,
             "active": True}
        stored.append(m)
        if key is not None:
            by_key[key] = m
