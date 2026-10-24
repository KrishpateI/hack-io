"""Property 11: Flood penalties are monotone in elevation and positive for exposed infrastructure.

**Validates: Requirements 17.2, 17.3, 17.8**
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st

from app.disasters.base import IncidentContext
from app.disasters.flood import FloodModule
from app.models.domain import Incident, IncidentType, UncertaintyLevel
from app.models.graph import Edge, Graph as GraphModel, Node
from app.models.routing import LatLon
from app.routing.cost import load_weights
from app.routing.graph import Graph

TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
W = load_weights()
BASE = dict(
    infrastructure_level="surface", pedestrian_access=True, bridge_flag=False,
    tunnel_flag=False, elevation_confidence="High", mobility_accessibility="accessible",
    elevation_start=190.0, elevation_end=190.0,
)
# water line ~33 m north of the nodes (within the 50 m shoreline buffer)
NEAR_WATER = {"type": "LineString", "coordinates": [(-87.64, 41.8803), (-87.62, 41.8803)]}


def make_graph(overrides):
    ids = [f"n{k}" for k in range(len(overrides) + 1)]
    ns = [Node(node_id=n, lat=41.88, lon=-87.63 + 0.0005 * k, kind="intersection") for k, n in enumerate(ids)]
    es = [
        Edge(
            edge_id=f"e{i}", from_node=ids[i], to_node=ids[i + 1], geometry="", distance_meters=100,
            estimated_travel_time_seconds=70, travel_mode=["walking"], source="osm",
            data_timestamp=TS, classification="Static", **{**BASE, **o},
        )
        for i, o in enumerate(overrides)
    ]
    return Graph._from_model(GraphModel.build(ns, es), SimpleNamespace(version="t", built_at=TS), {})


def ctx():
    inc = Incident(incident_id="i1", incident_type=IncidentType.FLOOD, source="t",
                   created_at=TS, updated_at=TS)
    return IncidentContext(incident=inc, now=TS, origin=LatLon(latitude=41.88, longitude=-87.63))


def total(parts):
    return sum(parts.hazard.values())


elev = st.floats(100, 300, allow_nan=False)


@settings(max_examples=50, deadline=None)
@given(elev, elev, elev, elev)
def test_higher_mean_elevation_never_increases_flood_penalty(s1, e1, ds, de):
    # edge 2 is edge 1 with both endpoints raised, so its mean elevation is >= edge 1's
    g = make_graph([
        {"elevation_start": s1, "elevation_end": e1},
        {"elevation_start": s1 + abs(ds), "elevation_end": e1 + abs(de)},
    ])
    m = FloodModule(W, water_geometries=[])
    lo = total(m.edge_penalty(g.edge(0), ctx(), g))
    hi = total(m.edge_penalty(g.edge(1), ctx(), g))
    assert hi <= lo + 1e-9


@settings(max_examples=50, deadline=None)
@given(
    kind=st.sampled_from(["tunnel", "lower", "shore"]),
    e=elev,
)
def test_exposed_infrastructure_has_positive_penalty(kind, e):
    o = {"elevation_start": e, "elevation_end": e}
    water = []
    if kind == "tunnel":
        o["tunnel_flag"] = True
    else:
        o["infrastructure_level"] = "lower-level"
    if kind == "shore":
        water = [NEAR_WATER]
    g = make_graph([o])
    p = FloodModule(W, water_geometries=water).edge_penalty(g.edge(0), ctx(), g)
    assert total(p) > 0
    key = {"tunnel": "flood_tunnel", "lower": "flood_lower_level", "shore": "flood_shoreline"}[kind]
    assert p.hazard[key] > 0


def test_flood_incident_without_polygon_or_notice_has_no_restrictions_and_high_uncertainty():
    g = make_graph([{}])
    m = FloodModule(W, water_geometries=[])
    assert list(m.hard_restrictions(ctx(), g)) == []
    assert m.uncertainty_floor(ctx()) is UncertaintyLevel.HIGH
