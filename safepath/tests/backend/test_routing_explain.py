"""Tests for routing/explain.py (task 6.8; Req 15.1-15.3, 15.6, 1.7, 17.7)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from hypothesis import given, strategies as st

from app.disasters.base import Advisory, RestrictionSet
from app.models.domain import RestrictionCause, UncertaintyLevel
from app.models.routing import ALGORITHM_LABEL, LineStringGeometry, Route
from app.models.domain import PolicyOutcome
from app.routing.cost import PenaltyComponents, parse_weights
from app.routing.explain import (
    RouteCandidate,
    explain_change,
    explain_route,
    latest_data_timestamp,
    uncertainty_level,
)

L, M, H, U = (UncertaintyLevel.LOW, UncertaintyLevel.MEDIUM, UncertaintyLevel.HIGH,
              UncertaintyLevel.UNKNOWN)

FORBIDDEN = re.compile(
    r"\bsafe(st)?\b|\bguarantee[sd]?\b|\bcertif(y|ied|ies|ication)\b|\bapproved\b|\bflood[-\s]?safe\b",
    re.I,
)
NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _weights():
    from app.routing.cost import load_weights
    return load_weights()


def _pc(eid, travel=10.0, hazard=None, unc=None):
    return PenaltyComponents(edge_id=eid, travel=travel, hazard=hazard or {}, uncertainty=unc or {})


def _cand(rid, comps, lengths=None):
    return RouteCandidate(rid, comps, lengths or [100.0] * len(comps))


def _route(edges, dist=100.0, dur=60.0):
    return Route(
        route_id="r", geometry=LineStringGeometry(coordinates=[[0, 0], [1, 1]]),
        distance_meters=dist, estimated_duration_seconds=dur, excluded_edges_count=0,
        uncertainty_level=L, policy_outcome=PolicyOutcome.ROUTE_OPTIONS if hasattr(
            PolicyOutcome, "ROUTE_OPTIONS") else list(PolicyOutcome)[0],
        edge_penalties=[_pc(e).to_edge_penalty() for e in edges], calculated_at=NOW,
    )


def test_uncertainty_thresholds():
    assert uncertainty_level(0.0) is L
    assert uncertainty_level(0.049) is L
    assert uncertainty_level(0.05) is M
    assert uncertainty_level(0.199) is M
    assert uncertainty_level(0.20) is H
    assert uncertainty_level(None) is U


def test_floor_raises_but_never_lowers():
    assert uncertainty_level(0.0, H) is H
    assert uncertainty_level(0.0, M) is M
    assert uncertainty_level(0.5, L) is H
    assert uncertainty_level(0.5, M) is H


@given(st.one_of(st.none(), st.floats(0, 1)), st.sampled_from([None, L, M, H]))
def test_floor_never_below_computed(frac, floor):
    base = uncertainty_level(frac)
    out = uncertainty_level(frac, floor)
    order = {U: -1, L: 0, M: 1, H: 2}
    assert order[out] >= order[base]


def test_explanation_fields():
    cause = RestrictionCause(kind="closure", id="c1", source_record_id="src-1", simulated=True)
    rs = RestrictionSet({"e9": (cause,), "e8": (cause,)})
    a = _cand("r1", [_pc("e1", 10), _pc("e2", 10, hazard={"tunnel": 5.0})])
    b = _cand("r2", [_pc("e3", 30, unc={"unknown_x": 4.0})])
    t1, t2 = NOW, datetime(2025, 1, 2, tzinfo=timezone.utc)
    ex = explain_route(0, [a, b], rs, _weights(), data_timestamps=[t1, None, t2],
                       source_record_ids=["src-2", "src-1"],
                       advisories=[Advisory("Check official notices.", simulated=True)])
    assert ex.label == ALGORITHM_LABEL
    assert ex.excluded_edges_count == 2
    assert [r.id for r in ex.restrictions_considered] == ["c1"]
    assert ex.restrictions_considered[0].simulated
    assert ex.sources == ["src-1", "src-2"]
    assert ex.data_timestamp == t2
    assert ex.hazard_flags == ["tunnel"]
    assert len(ex.edge_penalties) == 2
    assert "SIMULATED" in ex.reason_summary
    assert "Chosen over r2" in ex.reason_summary
    assert any(s.startswith("SIMULATED:") for s in ex.confidence_limitations)
    assert ex.uncertainty_level is L


def test_unknown_when_no_coverage_and_timestamp_none():
    ex = explain_route(0, [_cand("r", [])], RestrictionSet(), _weights())
    assert ex.uncertainty_level is U
    assert ex.data_timestamp is None
    assert latest_data_timestamp([]) is None


def test_uncertain_length_drives_level():
    comps = [_pc("a", unc={"u": 1.0}), _pc("b")]
    ex = explain_route(0, [_cand("r", comps, [30.0, 70.0])], RestrictionSet(), _weights())
    assert ex.uncertainty_level is H


def test_report_and_flood_wording():
    c = _cand("r", [_pc("a", hazard={"report_unverified": 3.0})])
    ex = explain_route(0, [c], RestrictionSet(), _weights(), incident_type="flood")
    text = " ".join(ex.confidence_limitations)
    assert "unverified warnings" in text and "never close" in text
    assert "lower modeled exposure" in text


def test_explain_change():
    cause = RestrictionCause(kind="closure", id="c9", source_record_id="s", simulated=True)
    before = _route(["e1", "e2", "e3"], 300, 180)
    after = _route(["e1", "e4", "e3"], 360, 220)
    ch = explain_change(before, after, RestrictionSet(), RestrictionSet({"e2": (cause,)}))
    assert ch.changed and ch.removed_edge_ids == ["e2"]
    assert ch.causes["e2"][0].id == "c9"
    assert ch.distance_delta_meters == 60
    assert "SIMULATED" in ch.text and "c9" in ch.text
    same = explain_change(before, before, RestrictionSet(), RestrictionSet())
    assert not same.changed and "unchanged" in same.text
    none = explain_change(before, None, RestrictionSet(), RestrictionSet({"e2": (cause,)}))
    assert "no route is currently available" in none.text


@given(
    st.lists(st.tuples(st.floats(0, 500), st.floats(0, 500), st.floats(0, 500)), min_size=1, max_size=5),
    st.booleans(),
    st.sampled_from(["flood", "fire", None]),
)
def test_forbidden_words_never_appear(rows, sim, itype):
    comps = [_pc(f"e{i}", t, {"report_unverified": h}, {"u": u}) for i, (t, h, u) in enumerate(rows)]
    other = [_pc("z", 1.0)]
    cause = RestrictionCause(kind="closure", id="c", simulated=sim)
    rs = RestrictionSet({"e0": (cause,)})
    cands = [_cand("r1", comps), _cand("r2", other)]
    for i in (0, 1):
        ex = explain_route(i, cands, rs, _weights(), incident_type=itype, simulated=sim)
        texts = [ex.reason_summary, *ex.confidence_limitations]
        assert not any(FORBIDDEN.search(t) for t in texts), texts
    ch = explain_change(_route(["e0", "x"]), _route(["y"]), RestrictionSet(), rs)
    assert not FORBIDDEN.search(ch.text)
