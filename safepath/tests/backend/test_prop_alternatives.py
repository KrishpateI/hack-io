"""Property 5: alternatives are valid, distinct and counted (task 6.18)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from hypothesis import HealthCheck, given, settings, strategies as st

from app.models.domain import Incident, IncidentType
from app.routing.alternatives import k_routes
from app.routing.cost import load_weights
from app.routing.graph import Graph
from app.routing.service import InMemoryContextProvider, RoutingService

from strategies import NOW, graphs

PBT = settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
INC = "inc-alt"
_WEIGHTS = load_weights()


def _cost(g):
    return lambda e: float(g.edge_dist[e]) + 1.0


def _check_path(g, path, s, t):
    assert path.node_indices[0] == s and path.node_indices[-1] == t
    assert len(path.edge_indices) == len(path.node_indices) - 1
    for k, e in enumerate(path.edge_indices):
        assert int(g.edge_from[e]) == path.node_indices[k]
        assert int(g.edge_to[e]) == path.node_indices[k + 1]


# Feature: safepath-evacuation-navigation, Property 5: Alternatives are valid, distinct and counted
# **Validates: Requirements 13.4, 44.8**
@PBT
@given(data=st.data(), k=st.integers(1, 3))
def test_k_routes_valid_distinct_bounded(data, k):
    g = data.draw(graphs(min_nodes=3, max_nodes=12))
    s = data.draw(st.integers(0, g.num_nodes - 1))
    t = data.draw(st.integers(0, g.num_nodes - 1))
    mask = None
    if data.draw(st.booleans()):
        mask = np.array([data.draw(st.booleans()) for _ in range(g.num_edges)], dtype=bool)
    cost = _cost(g)
    paths = k_routes(g, mask, s, t, cost, k, v_max=1.0, weights=_WEIGHTS)
    assert len(paths) <= k
    # a path exists iff at least one route is returned (k >= 1)
    if s != t:
        from app.routing.search import dijkstra
        assert (dijkstra(g, mask, s, t, cost) is not None) == bool(paths)
    for p in paths:
        _check_path(g, p, s, t)
        if mask is not None:
            assert all(mask[e] for e in p.edge_indices)
        assert abs(p.total_cost - sum(cost(e) for e in p.edge_indices)) < 1e-6
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            assert set(paths[i].edge_indices) != set(paths[j].edge_indices)


# Feature: safepath-evacuation-navigation, Property 5: Alternatives are valid, distinct and counted
# **Validates: Requirements 13.4, 44.8**
@PBT
@given(data=st.data(), k=st.integers(1, 3))
def test_service_route_count_matches_routes(data, k):
    g = data.draw(graphs(min_nodes=3, max_nodes=12))
    g.manifest = SimpleNamespace(version="test", built_at=NOW)
    prov = InMemoryContextProvider()
    prov.add_incident(Incident(incident_id=INC, incident_type=IncidentType.FLOOD, source="t",
                               created_at=NOW, updated_at=NOW, simulation_flag=True))
    svc = RoutingService(lambda: g, lambda: _WEIGHTS, prov, clock=lambda: NOW)
    a = data.draw(st.integers(0, g.num_nodes - 1))
    b = data.draw(st.integers(0, g.num_nodes - 1))
    req = svc.validate({
        "incident_id": INC,
        "origin": {"latitude": float(g.node_lat[a]), "longitude": float(g.node_lon[a])},
        "destination": {"latitude": float(g.node_lat[b]), "longitude": float(g.node_lon[b])},
        "travel_mode": "walking", "mobility_profile": "standard", "alternatives": k})
    resp = svc.calculate(req)
    if resp.policy_outcome.value != "ROUTE_OPTIONS_AVAILABLE":
        # non-route outcomes carry a placeholder route and a zero count
        assert resp.route_count == 0
        return
    assert 1 <= resp.route_count == len(resp.routes) <= k
    sets = [tuple(p.edge_id for p in r.edge_penalties) for r in resp.routes]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            assert set(sets[i]) != set(sets[j])
