"""Property test: unconfirmed evidence is a penalty, never a Hard_Restriction (design.md Property 7).

# Feature: safepath-evacuation-navigation, Property 7: Unconfirmed evidence is a penalty, never a Hard_Restriction

**Validates: Requirements 14.3, 14.5, 16.5, 20.6**
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from hypothesis import HealthCheck, assume, given, settings, strategies as st

from app.disasters.fire import PERIMETER_KEY, FireModule, FirePerimeter
from app.models.domain import HazardReport, IncidentType, VerificationStatus
from app.routing.cost import CostModel, load_weights
from app.routing.restrictions import RestrictionResolver

from strategies import NOW, graphs, incident_contexts

PBT = settings(max_examples=100, deadline=None, suppress_health_check=list(HealthCheck))

UNCONFIRMED = [VerificationStatus.UNVERIFIED, VerificationStatus.CORROBORATED,
               VerificationStatus.DISPUTED]
CATEGORIES = ["road_closure", "blocked_street", "fire_perimeter", "perimeter", "fire", "smoke", "debris"]


def _report(rid, iid, category, status, edge_ids, lat=41.871, lon=-87.65, source_record_id=None):
    return HazardReport(
        report_id=rid, incident_id=iid, category=category, latitude=lat, longitude=lon,
        affected_edge_ids=list(edge_ids), verification_status=status, created_at=NOW,
        expires_at=NOW + timedelta(hours=1), simulated=True, source_record_id=source_record_id)


@st.composite
def fire_scenario(draw):
    g = draw(graphs(min_nodes=3, max_nodes=12))
    assume(g.num_edges > 0)
    ctx = draw(incident_contexts(g))
    inc = ctx.incident.model_copy(update={"incident_type": IncidentType.FIRE, "status": "active"})
    ctx = replace(ctx, incident=inc, notices=(), overlays=(), reports=())
    return g, ctx


def _unconfirmed_reports(data, g, ctx, *, categories=CATEGORIES):
    ids = list(g.edge_ids)
    return tuple(
        _report(f"u{k}", ctx.incident.incident_id, data.draw(st.sampled_from(categories)),
                data.draw(st.sampled_from(UNCONFIRMED)),
                data.draw(st.lists(st.sampled_from(ids), min_size=1, max_size=4, unique=True)))
        for k in range(data.draw(st.integers(1, 4))))


@PBT
@given(data=st.data(), use_module=st.booleans())
def test_unconfirmed_reports_never_enter_restriction_set(data, use_module):
    # Feature: safepath-evacuation-navigation, Property 7: Unconfirmed evidence is a penalty, never a Hard_Restriction
    g, ctx = data.draw(fire_scenario())
    ctx = replace(ctx, reports=_unconfirmed_reports(data, g, ctx))
    resolved = RestrictionResolver().resolve(ctx, g, [FireModule()] if use_module else [])
    assert not resolved.blocked_edges
    assert resolved.excluded_edges_count == 0


@PBT
@given(data=st.data())
def test_unconfirmed_reports_yield_nonnegative_penalties_on_their_edges(data):
    # Feature: safepath-evacuation-navigation, Property 7: Unconfirmed evidence is a penalty, never a Hard_Restriction
    g, ctx = data.draw(fire_scenario())
    ctx = replace(ctx, reports=_unconfirmed_reports(data, g, ctx))
    cm = CostModel(g, load_weights(), ctx, FireModule())
    index = {eid: i for i, eid in enumerate(g.edge_ids)}
    for r in ctx.reports:
        for eid in r.affected_edge_ids:
            c = cm.components(index[eid])
            assert all(v >= 0 for v in c.hazard.values())
            assert all(v >= 0 for v in c.uncertainty.values())
            assert r.report_id in c.report_ids
            if r.verification_status == VerificationStatus.DISPUTED:
                assert c.uncertainty_total > 0 or c.hazard_total > 0
            else:
                assert c.hazard_total > 0
            if r.category in ("fire_perimeter", "perimeter"):
                assert c.hazard.get(PERIMETER_KEY, 0) > 0


@PBT
@given(data=st.data())
def test_unofficial_fire_perimeter_is_warning_and_penalty_only(data):
    # Feature: safepath-evacuation-navigation, Property 7: Unconfirmed evidence is a penalty, never a Hard_Restriction
    g, ctx = data.draw(fire_scenario())
    ids = list(g.edge_ids)
    lon, lat = float(g.node_lon[0]), float(g.node_lat[0])
    half = data.draw(st.floats(0.0001, 0.002))
    ring = [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half],
            [lon - half, lat + half], [lon - half, lat - half]]
    perimeter = FirePerimeter(
        perimeter_id="per-x", geometry={"type": "Polygon", "coordinates": [ring]},
        verification_status=data.draw(st.sampled_from(["Unverified", "Corroborated", "Disputed"])),
        source_record_id=None)
    report_perim = _report(
        "rp", ctx.incident.incident_id, "fire_perimeter",
        data.draw(st.sampled_from(UNCONFIRMED)), data.draw(st.lists(st.sampled_from(ids), min_size=1, max_size=3)))
    ctx = replace(ctx, reports=(report_perim,))
    module = FireModule([perimeter])

    resolved = RestrictionResolver().resolve(ctx, g, [module])
    assert not resolved.blocked_edges

    warnings = " ".join(a.text if hasattr(a, "text") else str(a) for a in module.advisories(ctx))
    assert "per-x" in warnings and "rp" in warnings

    cm = CostModel(g, load_weights(), ctx, module)
    penalised = {i for i in range(g.num_edges) if cm.components(i).hazard.get(PERIMETER_KEY, 0) > 0}
    index = {eid: i for i, eid in enumerate(g.edge_ids)}
    assert {index[e] for e in report_perim.affected_edge_ids} <= penalised
    assert all(cm.components(i).hazard_total >= 0 for i in range(g.num_edges))


@PBT
@given(data=st.data(), with_module=st.booleans())
def test_confirmed_closures_always_appear_in_restriction_set(data, with_module):
    # Feature: safepath-evacuation-navigation, Property 7: Unconfirmed evidence is a penalty, never a Hard_Restriction
    from app.models.domain import Restriction

    g, ctx = data.draw(fire_scenario())
    ids = list(g.edge_ids)
    verified_edges = data.draw(st.lists(st.sampled_from(ids), min_size=1, max_size=3, unique=True))
    moderator_edges = data.draw(st.lists(st.sampled_from(ids), min_size=1, max_size=3, unique=True))
    iid = ctx.incident.incident_id
    verified = _report("ov", iid, "road_closure", VerificationStatus.OFFICIALLY_VERIFIED, verified_edges,
                       source_record_id="src-ov")
    overlay = Restriction(restriction_id="mod-1", incident_id=iid, kind="closure",
                          edge_ids=moderator_edges, source_record_id="src-mod", active=True)
    noise = _unconfirmed_reports(data, g, ctx)
    ctx = replace(ctx, reports=(verified, *noise), overlays=(overlay,))
    resolved = RestrictionResolver().resolve(ctx, g, [FireModule()] if with_module else [])
    assert set(verified_edges) | set(moderator_edges) <= set(resolved.blocked_edges)
