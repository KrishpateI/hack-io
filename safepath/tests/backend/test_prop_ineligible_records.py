"""Property 16: ineligible records never influence evaluation (task 6.29).

# Feature: safepath-evacuation-navigation, Property 16: Ineligible records never influence evaluation

**Validates: Requirements 8.9, 9.5, 10.8, 11.9**

Ineligible: no Source_Record ("Unknown source"), CBD_Plan-derived, Cook County Hazard Mitigation
Plan areas used as live boundaries, and notices with now > expires_at + 60 s. A notice within the
60 s window (or unexpired) stays eligible. Evaluation (policy decision, hard restrictions, flood
module output) is identical with or without the ineligible records.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from hypothesis import HealthCheck, assume, given, settings, strategies as st

from app.disasters.flood import FloodModule
from app.disasters.rules_engine import (
    REASON_CBD,
    REASON_EXPIRED,
    REASON_HMP,
    REASON_UNKNOWN_SOURCE,
    UNKNOWN_SOURCE,
    evaluate_pre_route,
    filter_notices,
)
from app.models.domain import OfficialNotice
from app.routing.restrictions import RestrictionResolver

from strategies import NOW, graphs, incident_contexts

PBT = settings(max_examples=100, deadline=None, suppress_health_check=list(HealthCheck))

TYPES = ["temporary_perimeter", "road_closure", "incident_destination", "shelter_in_place",
         "road_direction_restriction", "transportation_rule", "follow_instructions"]
FLAVORS = ["no_source", "cbd", "hmp", "expired"]
EXPECTED_REASON = {"no_source": REASON_UNKNOWN_SOURCE, "cbd": REASON_CBD,
                   "hmp": REASON_HMP, "expired": REASON_EXPIRED}


def _notice(nid, iid, ntype, edge_ids, *, source="src-1", cbd=False, org="OEMC",
            effective=None, expires=None):
    effective = effective or NOW - timedelta(minutes=30)
    return OfficialNotice(
        notice_id=nid, incident_id=iid, notice_type=ntype, text=f"text {nid}", edge_ids=list(edge_ids),
        source_organization=org, source_url="https://example.invalid/n", source_record_id=source,
        effective_at=effective, expires_at=expires, published_at=effective - timedelta(minutes=5),
        retrieved_at=effective, verification_status="Officially_Verified", simulation_flag=True,
        cbd_derived=cbd)


@st.composite
def eligible_notice(draw, iid, edge_ids, k):
    expires = draw(st.one_of(
        st.none(),
        st.integers(0, 600).map(lambda s: NOW + timedelta(seconds=s)),        # unexpired
        st.integers(-60, 0).map(lambda s: NOW + timedelta(seconds=s))))       # inside 60 s grace
    return _notice(f"ok{k}", iid, draw(st.sampled_from(TYPES)),
                   draw(st.lists(st.sampled_from(edge_ids), unique=True, max_size=4)), expires=expires)


@st.composite
def ineligible_notice(draw, iid, edge_ids, k):
    flavor = draw(st.sampled_from(FLAVORS))
    ntype = draw(st.sampled_from(TYPES))
    edges = draw(st.lists(st.sampled_from(edge_ids), min_size=1, unique=True, max_size=4))
    kw = {}
    if flavor == "no_source":
        kw["source"] = ""
    elif flavor == "cbd":
        kw["cbd"] = True
    elif flavor == "hmp":
        ntype = "temporary_perimeter"
        kw["org"] = "Cook County Hazard Mitigation Plan"
    else:
        kw["expires"] = NOW - timedelta(seconds=draw(st.integers(61, 7200)))
        kw["effective"] = NOW - timedelta(hours=5)
    return flavor, _notice(f"bad{k}", iid, ntype, edges, **kw)


@st.composite
def scenario(draw):
    g = draw(graphs(min_nodes=3, max_nodes=10))
    assume(g.num_edges > 0)
    ctx = draw(incident_contexts(g))
    ctx = replace(ctx, notices=(), reports=(), overlays=())
    ids = list(g.edge_ids)
    iid = ctx.incident.incident_id
    good = tuple(draw(eligible_notice(iid, ids, k)) for k in range(draw(st.integers(0, 3))))
    bad = [draw(ineligible_notice(iid, ids, k)) for k in range(draw(st.integers(1, 4)))]
    return g, ctx, good, bad


def _decision_key(d):
    return (d.outcome, [n.notice_id for n in d.notices], d.explanation, d.missing_data,
            d.destination_priority)


@PBT
@given(sc=scenario(), shuffle=st.randoms(use_true_random=False))
def test_policy_decision_identical_with_or_without_ineligible(sc, shuffle):
    # Feature: safepath-evacuation-navigation, Property 16: Ineligible records never influence evaluation
    g, ctx, good, bad = sc
    mixed = list(good) + [n for _, n in bad]
    shuffle.shuffle(mixed)
    with_bad = replace(ctx, notices=tuple(mixed))
    without = replace(ctx, notices=good)
    assert _decision_key(evaluate_pre_route(with_bad)) == _decision_key(evaluate_pre_route(without))

    eligible, excluded = filter_notices(with_bad)
    assert {n.notice_id for n in eligible} == {n.notice_id for n in good}
    reasons = {e.notice.notice_id: e for e in excluded}
    for flavor, n in bad:
        assert reasons[n.notice_id].reason == EXPECTED_REASON[flavor]
        if flavor == "no_source":
            assert reasons[n.notice_id].label == UNKNOWN_SOURCE


@PBT
@given(sc=scenario(), with_flood=st.booleans())
def test_hard_restrictions_identical_with_or_without_ineligible(sc, with_flood):
    # Feature: safepath-evacuation-navigation, Property 16: Ineligible records never influence evaluation
    g, ctx, good, bad = sc
    mods = [FloodModule()] if with_flood else []
    with_bad = replace(ctx, notices=good + tuple(n for _, n in bad))
    without = replace(ctx, notices=good)
    a = RestrictionResolver().resolve(with_bad, g, mods)
    b = RestrictionResolver().resolve(without, g, mods)
    assert a.blocked_edges == b.blocked_edges
    assert a.excluded_edges_count == b.excluded_edges_count


@PBT
@given(sc=scenario())
def test_flood_module_advisories_identical_with_or_without_ineligible(sc):
    # Feature: safepath-evacuation-navigation, Property 16: Ineligible records never influence evaluation
    g, ctx, good, bad = sc
    with_bad = replace(ctx, notices=good + tuple(n for _, n in bad))
    without = replace(ctx, notices=good)
    m = FloodModule()
    key = lambda c: [(a.text, a.source_record_id) for a in m.advisories(c)]  # noqa: E731
    assert key(with_bad) == key(without)
    assert [r.restriction_id for r in m.hard_restrictions(with_bad, g)] == \
           [r.restriction_id for r in m.hard_restrictions(without, g)]


def test_grace_window_boundary_is_inclusive():
    # exactly expires_at + 60 s is still eligible; +61 s is not
    from app.disasters.rules_engine import notice_exclusion
    n60 = _notice("a", "inc-1", "road_closure", [], expires=NOW - timedelta(seconds=60))
    n61 = _notice("b", "inc-1", "road_closure", [], expires=NOW - timedelta(seconds=61))
    assert notice_exclusion(n60, NOW, "inc-1") is None
    assert notice_exclusion(n61, NOW, "inc-1").reason == REASON_EXPIRED
