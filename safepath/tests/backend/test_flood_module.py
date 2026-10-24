from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from hypothesis import given, strategies as st

from app.disasters.base import IncidentContext, get_module
from app.disasters.flood import FloodModule
from app.disasters.base import RestrictionSet
from app.models.domain import (
    Incident,
    IncidentType,
    OfficialNotice,
    Restriction,
    UncertaintyLevel,
)
from app.models.graph import Edge, Graph as GraphModel, Node
from app.models.routing import LatLon
from app.routing.cost import load_weights
from app.routing.graph import Graph

TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
W = load_weights()
F = W.flood
BASE = dict(
    infrastructure_level="surface", pedestrian_access=True, bridge_flag=False,
    tunnel_flag=False, elevation_confidence="High", mobility_accessibility="accessible",
    elevation_start=190.0, elevation_end=190.0,
)
# ~0.001 deg lat = 111 m; river is a horizontal line just north of the nodes
RIVER = {"type": "LineString", "coordinates": [(-87.64, 41.8806), (-87.62, 41.8806)]}


def make_graph(specs):
    ids = sorted({x for s in specs for x in s[1:3]})
    ns = [Node(node_id=n, lat=41.88, lon=-87.63 + 0.0005 * k, kind="intersection") for k, n in enumerate(ids)]
    es = [
        Edge(
            edge_id=i, from_node=f, to_node=t, geometry="", distance_meters=100,
            estimated_travel_time_seconds=70, travel_mode=["walking"], source="osm",
            data_timestamp=TS, classification="Static", **{**BASE, **o},
        )
        for i, f, t, o in specs
    ]
    return Graph._from_model(GraphModel.build(ns, es), SimpleNamespace(version="t"), {})


def ctx(**kw):
    inc = Incident(incident_id="i1", incident_type=IncidentType.FLOOD, source="t",
                   created_at=TS, updated_at=TS)
    return IncidentContext(incident=inc, now=TS, origin=LatLon(latitude=41.88, longitude=-87.63), **kw)


def notice(nid="n1", text="Flood warning: Lower Wacker flooded", ntype="road_closure", **kw):
    return OfficialNotice(
        notice_id=nid, incident_id="i1", notice_type=ntype, text=text,
        source_organization="OEMC", source_url="https://x", source_record_id="SR-" + nid,
        effective_at=TS - timedelta(hours=1), published_at=TS, retrieved_at=TS,
        verification_status="Officially_Verified", simulation_flag=False, **kw,
    )


def overlay(rid="o1", **kw):
    return Restriction(restriction_id=rid, incident_id="i1", kind="impassable_polygon",
                       source_record_id="SR-o", simulated=True, **kw)


def test_registered_for_flood():
    import app.disasters.flood  # noqa: F401
    assert isinstance(get_module(IncidentType.FLOOD), FloodModule)


def test_tunnel_and_lower_level_penalties_positive():
    g = make_graph([("t", "a", "b", {"tunnel_flag": True}), ("l", "b", "c", {"infrastructure_level": "lower-level"})])
    m = FloodModule(W, water_geometries=[])
    t = m.edge_penalty(g.edge(0), ctx(), g)
    l = m.edge_penalty(g.edge(1), ctx(), g)
    assert t.hazard["flood_tunnel"] == pytest.approx(F["w_tunnel"] * 1.0)
    assert l.hazard["flood_lower_level"] == pytest.approx(F["w_lower"] * 1.0)
    assert "flood_tunnel" not in l.hazard


def test_shoreline_penalty_only_for_lower_level_near_water():
    g = make_graph([
        ("low", "a", "b", {"infrastructure_level": "lower-level"}),
        ("surf", "b", "c", {}),
    ])
    m = FloodModule(W, water_geometries=[RIVER])  # ~67 m north: buffer test below uses 50 m
    assert "flood_shoreline" not in m.edge_penalty(g.edge(0), ctx(), g).hazard
    close = {"type": "LineString", "coordinates": [(-87.64, 41.8803), (-87.62, 41.8803)]}  # ~33 m
    m = FloodModule(W, water_geometries=[close])
    assert m.edge_penalty(g.edge(0), ctx(), g).hazard["flood_shoreline"] == pytest.approx(F["w_shore"])
    assert "flood_shoreline" not in m.edge_penalty(g.edge(1), ctx(), g).hazard


def test_missing_water_data_skips_shore_term_with_note():
    g = make_graph([("low", "a", "b", {"infrastructure_level": "lower-level"})])
    m = FloodModule(W)
    assert "flood_shoreline" not in m.edge_penalty(g.edge(0), ctx(), g).hazard
    assert any("unavailable" in a.text for a in m.advisories(ctx(), g))


def test_elevation_penalty_endpoints_and_unknown():
    g = make_graph([
        ("lo", "a", "b", {"elevation_start": 170.0, "elevation_end": 170.0}),
        ("hi", "b", "c", {"elevation_start": 200.0, "elevation_end": 200.0}),
        ("unk", "c", "d", {"elevation_start": "Unknown", "elevation_end": 190.0, "elevation_confidence": "Unknown"}),
    ])
    m = FloodModule(W, water_geometries=[])
    assert m.edge_penalty(g.edge(0), ctx(), g).hazard["flood_elevation"] == pytest.approx(F["w_elev"])
    assert m.edge_penalty(g.edge(1), ctx(), g).hazard["flood_elevation"] == 0
    p = m.edge_penalty(g.edge(2), ctx(), g)
    assert "flood_elevation" not in p.hazard and p.uncertainty_total > 0


def test_low_confidence_adds_uncertainty():
    g = make_graph([("a1", "a", "b", {"elevation_confidence": "Low"})])
    p = FloodModule(W, water_geometries=[]).edge_penalty(g.edge(0), ctx(), g)
    assert p.uncertainty_total > 0


@given(st.floats(150, 220, allow_nan=False), st.floats(0, 40, allow_nan=False))
def test_elevation_penalty_non_increasing(e, delta):
    g = make_graph([
        ("a1", "a", "b", {"elevation_start": e, "elevation_end": e}),
        ("a2", "b", "c", {"elevation_start": e + delta, "elevation_end": e + delta}),
    ])
    m = FloodModule(W, water_geometries=[])
    p1 = m.edge_penalty(g.edge(0), ctx(), g).hazard["flood_elevation"]
    p2 = m.edge_penalty(g.edge(1), ctx(), g).hazard["flood_elevation"]
    assert p2 <= p1 + 1e-9


def test_no_evidence_sets_high_floor_and_evidence_clears_it():
    m = FloodModule(W, water_geometries=[])
    assert m.uncertainty_floor(ctx()) is UncertaintyLevel.HIGH
    assert m.uncertainty_floor(ctx(notices=(notice(),))) is None
    assert m.uncertainty_floor(ctx(overlays=(overlay(edge_ids=["x"]),))) is None


def test_hard_restrictions_from_overlay_and_flooded_notice():
    g = make_graph([("e1", "a", "b", {}), ("e2", "b", "c", {}), ("e3", "c", "d", {})])
    m = FloodModule(W, water_geometries=[])
    c = ctx(overlays=(overlay(edge_ids=["e1"]),), notices=(notice(edge_ids=["e2"]),
            notice("n2", text="Road closed for construction", edge_ids=["e3"])))
    res = list(m.hard_restrictions(c, g))
    ids = {e for r in res for e in r.edge_ids}
    assert ids == {"e1", "e2"}
    blocked = RestrictionSet({e: (r.cause(),) for r in res for e in r.edge_ids})
    assert blocked.is_blocked("e2") and not blocked.is_blocked("e3")


def test_advisories_guidance_source_and_wording():
    m = FloodModule(W, water_geometries=[])
    adv = m.advisories(ctx(notices=(notice(),), overlays=(overlay(edge_ids=["x"]),)))
    texts = " ".join(a.text for a in adv)
    assert "ready.illinois.gov" in texts and "retrieved" in texts
    assert any(a.source_record_id == "SR-n1" for a in adv)
    assert any(a.simulated and "No water-depth" in a.text for a in adv)
    assert "flood-safe" not in texts.lower() and "lower modeled exposure" in texts
