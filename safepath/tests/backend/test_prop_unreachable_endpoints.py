"""Property test for unreachable or isolated endpoints.

# Feature: safepath-evacuation-navigation, Property 6: Unreachable or isolated endpoints yield NO_VERIFIED_ROUTE_AVAILABLE
**Validates: Requirements 13.6, 14.8, 44.4**

Outdoor graph variant (the indoor variant is added in task 11.7).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
from hypothesis import HealthCheck, assume, given, settings, strategies as st

from app.models.domain import Incident, IncidentType, Restriction
from app.models.graph import Graph as GraphModel
from app.models.routing import LatLon, RouteRequest
from app.routing.graph import Graph
from app.routing.service import InMemoryContextProvider, RoutingService
from app.routing.cost import load_weights
from strategies import graph_models

NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
INC = "inc-1"
SETTINGS = settings(
    max_examples=120, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
)
W = load_weights()


def _normalised(model: GraphModel) -> GraphModel:
    """All edges plain walkable street edges, so only hard restrictions decide routability."""
    edges = [
        e.model_copy(update=dict(
            edge_kind="street", transition_type=None, travel_mode=["walking", "driving"],
            pedestrian_access=True, mobility_accessibility="accessible",
            current_closure_status="open", infrastructure_level="surface",
            bridge_flag=False, tunnel_flag=False,
        ))
        for e in model.edges
    ]
    return GraphModel.build(list(model.nodes), edges)


def _service(graph: Graph, closed: list[str]) -> RoutingService:
    provider = InMemoryContextProvider()
    provider.add_incident(Incident(
        incident_id=INC, incident_type=IncidentType.FLOOD, source="test", created_at=NOW,
        updated_at=NOW, simulation_flag=True))
    if closed:
        provider.add_overlay(Restriction(
            restriction_id="cl-1", incident_id=INC, kind="closure", edge_ids=sorted(closed),
            source_record_id="src-cl", simulated=True))
    return RoutingService(lambda: graph, lambda: W, provider, clock=lambda: NOW)


def _request(graph: Graph, o: int, d: int) -> RouteRequest:
    return RouteRequest(
        incident_id=INC,
        origin=LatLon(latitude=float(graph.node_lat[o]), longitude=float(graph.node_lon[o])),
        destination=LatLon(latitude=float(graph.node_lat[d]), longitude=float(graph.node_lon[d])),
        travel_mode="walking", mobility_profile="standard", alternatives=1,
    )


def _assert_no_route(resp, isolated: set[str] | None = None) -> None:
    assert resp.policy_outcome.value == "NO_VERIFIED_ROUTE_AVAILABLE"
    assert resp.explanation.strip()
    assert resp.route_count == 0
    assert all(r.geometry.coordinates == [] for r in resp.routes)
    if isolated is not None:
        for who in ("origin", "destination"):
            if who in isolated:
                assert who in resp.explanation, resp.explanation


@st.composite
def scenario(draw, connected=None):
    model = _normalised(draw(graph_models(min_nodes=3, max_nodes=8, connected=connected)))
    graph = Graph._from_model(model, SimpleNamespace(version="t", built_at=NOW), {})
    n = graph.num_nodes
    o = draw(st.integers(0, n - 1))
    d = draw(st.integers(0, n - 1).filter(lambda x: x != o))
    return graph, o, d


@SETTINGS
@given(st.data())
def test_cut_or_disconnected_endpoints_yield_no_verified_route(data):
    graph, o, d = data.draw(scenario())
    assume(graph.num_edges > 0)
    ids = list(graph.edge_ids)
    closed = data.draw(st.lists(st.sampled_from(ids), unique=True, max_size=len(ids)))
    req = _request(graph, o, d)
    closed_set = set(closed)
    usable = np.array([eid not in closed_set for eid in ids])

    # Oracle: snap exactly as the service does on the restricted graph, then BFS on directed edges.
    s_o = graph.snap(req.origin.latitude, req.origin.longitude, usable)
    s_d = graph.snap(req.destination.latitude, req.destination.longitude, usable)
    reachable = False
    if s_o is not None and s_d is not None:
        src, dst = graph.node_index[s_o.node_id], graph.node_index[s_d.node_id]
        seen, queue = {src}, deque([src])
        while queue:
            u = queue.popleft()
            for e in graph.out_edges(u):
                e = int(e)
                v = int(graph.edge_to[e])
                if usable[e] and v not in seen:
                    seen.add(v)
                    queue.append(v)
        reachable = dst in seen
    assume(not reachable)

    resp = _service(graph, closed).calculate(req)
    _assert_no_route(resp)


@SETTINGS
@given(st.data())
def test_isolated_endpoint_is_identified(data):
    graph, o, d = data.draw(scenario(connected=True))
    isolate_o, isolate_d = data.draw(st.sampled_from([(True, False), (False, True), (True, True)]))
    req = _request(graph, o, d)
    n_o = graph.snap(req.origin.latitude, req.origin.longitude, None).node_id
    n_d = graph.snap(req.destination.latitude, req.destination.longitude, None).node_id
    blocked_nodes = {graph.node_index[n] for n, flag in ((n_o, isolate_o), (n_d, isolate_d)) if flag}
    closed = [
        graph.edge_ids[e] for e in range(graph.num_edges)
        if int(graph.edge_from[e]) in blocked_nodes or int(graph.edge_to[e]) in blocked_nodes
    ]
    assume(closed)
    closed_set = set(closed)

    def has_exit(node: int, outgoing: bool) -> bool:
        edges = graph.out_edges(node) if outgoing else np.flatnonzero(graph.edge_to == node)
        return any(graph.edge_ids[int(e)] not in closed_set for e in edges)

    iso = set()
    if not has_exit(graph.node_index[n_o], True):
        iso.add("origin")
    if not has_exit(graph.node_index[n_d], False):
        iso.add("destination")
    assert iso
    resp = _service(graph, closed).calculate(req)
    _assert_no_route(resp, iso)

