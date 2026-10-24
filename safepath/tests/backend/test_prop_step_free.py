"""Property 10: Step-free profile excludes known-inaccessible edges.

# Feature: safepath-evacuation-navigation, Property 10: Step-free profile excludes known-inaccessible edges

**Validates: Requirements 13.8**
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from hypothesis import HealthCheck, given, settings, strategies as st

from app.disasters.base import IncidentContext
from app.models.domain import Incident, IncidentType, MobilityProfile, TravelMode
from app.models.graph import Edge, Graph as GraphModel, Node
from app.models.routing import LatLon, RouteRequest
from app.routing.cost import CostModel, load_weights
from app.routing.graph import Graph
from app.routing.service import InMemoryContextProvider, RoutingService

from strategies import NOW, graphs

PBT = settings(max_examples=100, deadline=None, suppress_health_check=list(HealthCheck))
W = load_weights()
TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
KNOWN = dict(
    infrastructure_level="surface", pedestrian_access=True, bridge_flag=False,
    tunnel_flag=False, elevation_confidence="High",
)


def _pair_graph(a: str, b: str, dist: float) -> Graph:
    ns = [Node(node_id=f"n{k}", lat=41.88 + 0.001 * k, lon=-87.63, kind="intersection") for k in range(3)]
    es = [
        Edge(edge_id=f"e{k}", from_node=f"n{k}", to_node=f"n{k + 1}", geometry="",
             distance_meters=dist, estimated_travel_time_seconds=dist / 1.4,
             travel_mode=["walking"], source="osm", data_timestamp=TS, classification="Static",
             mobility_accessibility=acc, **KNOWN)
        for k, acc in enumerate([a, b])
    ]
    return Graph._from_model(GraphModel.build(ns, es), SimpleNamespace(version="t", built_at=TS), {})


def _model(g: Graph, profile=MobilityProfile.STEP_FREE) -> CostModel:
    inc = Incident(incident_id="i1", incident_type=IncidentType.FLOOD, source="t",
                   created_at=TS, updated_at=TS)
    ctx = IncidentContext(incident=inc, now=TS, origin=LatLon(latitude=41.88, longitude=-87.63),
                          profile=profile, mode=TravelMode.WALKING, reports=())
    return CostModel(g, W, ctx)


# Feature: safepath-evacuation-navigation, Property 10: Step-free profile excludes known-inaccessible edges
@PBT
@given(data=st.data())
def test_step_free_routes_never_contain_inaccessible_edges(data):
    g = data.draw(graphs(min_nodes=3, max_nodes=12))
    object.__setattr__(g, "manifest", SimpleNamespace(version=g.version, built_at=NOW))
    inaccessible = {e.edge_id for e in g.model.edges if e.mobility_accessibility == "inaccessible"}

    provider = InMemoryContextProvider()
    inc = Incident(incident_id="inc-1", incident_type=IncidentType.EARTHQUAKE, source="t",
                   created_at=NOW, updated_at=NOW, status="active")
    provider.add_incident(inc)
    svc = RoutingService(lambda: g, load_weights, provider, clock=lambda: NOW)

    for _ in range(3):
        src = data.draw(st.integers(0, g.num_nodes - 1))
        dst = data.draw(st.integers(0, g.num_nodes - 1))
        req = RouteRequest(
            incident_id="inc-1",
            origin=LatLon(latitude=float(g.node_lat[src]), longitude=float(g.node_lon[src])),
            destination=LatLon(latitude=float(g.node_lat[dst]), longitude=float(g.node_lon[dst])),
            travel_mode="walking", mobility_profile="step_free", alternatives=3,
        )
        resp = svc.calculate(req)
        for route in resp.routes:
            used = {p.edge_id for p in route.edge_penalties}
            assert not (used & inaccessible), "step-free route traverses an inaccessible edge"

    # the cost model's usable mask agrees with the oracle
    mask = _model(g).step_free_mask()
    for i, eid in enumerate(g.edge_ids):
        assert bool(mask[i]) == (eid not in inaccessible)


@PBT
@given(dist=st.floats(1, 2000, allow_nan=False))
def test_unknown_accessibility_costs_more_than_accessible_under_step_free(dist):
    g = _pair_graph("Unknown", "accessible", dist)
    m = _model(g)
    assert m.components(0).uncertainty["profile_unknown"] > 0
    assert m.cost(0) > m.cost(1)


@PBT
@given(dist=st.floats(1, 2000, allow_nan=False))
def test_standard_profile_does_not_exclude_inaccessible(dist):
    g = _pair_graph("inaccessible", "accessible", dist)
    assert _model(g, MobilityProfile.STANDARD).step_free_mask().all()
    assert not _model(g).step_free_mask()[0]
