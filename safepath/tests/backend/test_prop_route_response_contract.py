"""Property 17: route responses satisfy the response contract (task 6.30)."""
from __future__ import annotations

from types import SimpleNamespace

from hypothesis import HealthCheck, given, settings, strategies as st

from app.models.domain import Incident, IncidentType, Restriction
from app.routing.cost import load_weights
from app.routing.service import InMemoryContextProvider, RoutingService

from strategies import NOW, graphs

PBT = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
INC = "inc-contract"
SRC = "src-closure-17"
_WEIGHTS = load_weights()
OUTCOMES = {
    "ROUTE_OPTIONS_AVAILABLE", "NO_VERIFIED_ROUTE_AVAILABLE", "OFFICIAL_ORDER_RESTRICTS_ROUTING",
    "SHELTER_IN_PLACE_ADVISED", "INSUFFICIENT_DATA",
}
FIELDS = (
    "route_id", "geometry", "distance_meters", "estimated_duration_seconds", "hazard_flags",
    "excluded_edges_count", "uncertainty_level", "policy_outcome", "sources", "calculated_at", "status",
)


# Feature: safepath-evacuation-navigation, Property 17: Route responses satisfy the response contract
# **Validates: Requirements 15.1, 15.5, 15.6, 43.2, 43.3**
@PBT
@given(data=st.data(), k=st.integers(1, 3))
def test_route_response_contract(data, k):
    g = data.draw(graphs(min_nodes=3, max_nodes=12))
    g.manifest = SimpleNamespace(version="test", built_at=NOW)
    prov = InMemoryContextProvider()
    prov.add_incident(Incident(incident_id=INC, incident_type=IncidentType.FLOOD, source="t",
                               created_at=NOW, updated_at=NOW, simulation_flag=True))
    closed = []
    if g.num_edges and data.draw(st.booleans()):
        closed = data.draw(st.lists(st.sampled_from(g.edge_ids), unique=True, min_size=1, max_size=g.num_edges))
        prov.add_overlay(Restriction(restriction_id="cl-17", incident_id=INC, kind="closure",
                                     edge_ids=sorted(closed), source_record_id=SRC, simulated=True))
    svc = RoutingService(lambda: g, lambda: _WEIGHTS, prov, clock=lambda: NOW)
    a = data.draw(st.integers(0, g.num_nodes - 1))
    b = data.draw(st.integers(0, g.num_nodes - 1))
    req = svc.validate({
        "incident_id": INC,
        "origin": {"latitude": float(g.node_lat[a]), "longitude": float(g.node_lon[a])},
        "destination": {"latitude": float(g.node_lat[b]), "longitude": float(g.node_lon[b])},
        "travel_mode": "walking", "mobility_profile": "standard", "alternatives": k})
    resp = svc.calculate(req)

    assert resp.policy_outcome.value in OUTCOMES
    assert resp.routes, "every response carries at least one route object"
    for r in resp.routes:
        dumped = r.model_dump()
        for f in FIELDS:
            assert f in dumped
        assert r.policy_outcome.value in OUTCOMES
        assert r.route_id and r.status and r.calculated_at is not None

    if resp.policy_outcome.value != "ROUTE_OPTIONS_AVAILABLE":
        assert resp.explanation.strip()
        for r in resp.routes:
            assert r.geometry.coordinates == []
        return

    index = {eid: i for i, eid in enumerate(g.edge_ids)}
    for r in resp.routes:
        assert len(r.geometry.coordinates) >= 2
        total = sum(float(g.edge_dist[index[p.edge_id]]) for p in r.edge_penalties)
        assert abs(r.distance_meters - total) <= 1e-6 * max(1.0, total)
        assert r.excluded_edges_count == len(closed)
        if closed:
            assert SRC in r.sources
    if closed:
        assert SRC in resp.sources
