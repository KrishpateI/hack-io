"""Property test for search optimality.

# Feature: safepath-evacuation-navigation, Property 2: Search returns an optimal path
**Validates: Requirements 13.1, 44.1, 44.2**
"""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.disasters.base import IncidentContext
from app.geo import haversine
from app.models.domain import Incident, IncidentType, MobilityProfile, TravelMode
from app.models.graph import Edge, Graph as GraphModel, Node
from app.models.routing import LatLon
from app.routing.cost import CostModel, load_weights
from app.routing.graph import Graph
from app.routing.search import astar, dijkstra
from strategies import graphs, incident_contexts

SETTINGS = settings(
    max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large]
)
W = load_weights()
TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
V_MAX = 5.0
REL = 1e-9


def brute_force_min(g: Graph, src: int, dst: int, cost) -> float | None:
    """Minimum total cost over all simple (node-distinct) src->dst edge paths, or None."""
    best: list[float | None] = [None]

    def dfs(n: int, visited: frozenset, acc: float) -> None:
        if n == dst:
            if best[0] is None or acc < best[0]:
                best[0] = acc
            return
        for e in g.out_edges(n):
            e = int(e)
            m = int(g.edge_to[e])
            if m not in visited:
                dfs(m, visited | {m}, acc + cost(e))

    dfs(src, frozenset({src}), 0.0)
    return best[0]


def check_valid(g: Graph, p, src: int, dst: int, cost) -> None:
    assert p.node_indices[0] == src and p.node_indices[-1] == dst
    assert len(p.node_indices) == len(p.edge_indices) + 1
    assert len(set(p.node_indices)) == len(p.node_indices)
    total = 0.0
    for k, e in enumerate(p.edge_indices):
        assert int(g.edge_from[e]) == p.node_indices[k]  # directed edges respected
        assert int(g.edge_to[e]) == p.node_indices[k + 1]
        total += cost(e)
    assert p.total_cost == pytest.approx(total, rel=REL, abs=1e-9)
    assert p.edge_ids == tuple(g.edge_ids[e] for e in p.edge_indices)


@st.composite
def scenario(draw):
    g = draw(graphs(min_nodes=2, max_nodes=8))
    n = g.num_nodes
    src, dst = draw(st.integers(0, n - 1)), draw(st.integers(0, n - 1))
    # cost >= haversine(edge endpoints) / V_MAX, so the heuristic is admissible and consistent
    mult = draw(st.lists(
        st.one_of(st.just(1.0), st.floats(1.0, 50.0, allow_nan=False)),
        min_size=g.num_edges, max_size=g.num_edges))
    bound = [
        haversine(float(g.node_lat[g.edge_from[e]]), float(g.node_lon[g.edge_from[e]]),
                  float(g.node_lat[g.edge_to[e]]), float(g.node_lon[g.edge_to[e]])) / V_MAX
        for e in range(g.num_edges)
    ]
    costs = [b * m for b, m in zip(bound, mult)]
    return g, src, dst, costs


@SETTINGS
@given(scenario())
def test_astar_is_optimal(sc):
    g, src, dst, costs = sc
    cost = costs.__getitem__
    a = astar(g, None, src, dst, cost, v_max=V_MAX)
    d = dijkstra(g, None, src, dst, cost)
    best = brute_force_min(g, src, dst, cost)
    if best is None:
        assert a is None and d is None
        return
    assert a is not None and d is not None
    check_valid(g, a, src, dst, cost)
    check_valid(g, d, src, dst, cost)
    assert a.total_cost == pytest.approx(best, rel=1e-7, abs=1e-9)
    assert d.total_cost == pytest.approx(best, rel=1e-7, abs=1e-9)
    # deterministic repeat
    assert astar(g, None, src, dst, cost, v_max=V_MAX) == a
    assert dijkstra(g, None, src, dst, cost) == d


@SETTINGS
@given(st.data())
def test_astar_matches_dijkstra_with_real_cost_model(data):
    g = data.draw(graphs(min_nodes=2, max_nodes=8))
    ctx = data.draw(incident_contexts(g))
    model = CostModel(g, W, ctx)
    cost, vmax = model.cost_fn(), model.v_max
    n = g.num_nodes
    src, dst = data.draw(st.integers(0, n - 1)), data.draw(st.integers(0, n - 1))
    mask = model.step_free_mask()
    flt = model.route_filter()
    a = astar(g, mask, src, dst, cost, v_max=vmax, route_filter=flt)
    d = dijkstra(g, mask, src, dst, cost, route_filter=flt)
    assert (a is None) == (d is None)
    if a is not None:
        assert a.total_cost == pytest.approx(d.total_cost, rel=1e-6, abs=1e-6)
        assert astar(g, mask, src, dst, cost, v_max=vmax, route_filter=flt) == a


# ---------------------------------------------------------------- driving v_max regression
def _fast_driving_graph() -> Graph:
    """s -> t direct at ~31.8 m/s, or s -> a -> t at 40 m/s (a on the straight line)."""
    known = dict(
        infrastructure_level="surface", pedestrian_access=True, bridge_flag=False,
        tunnel_flag=False, elevation_confidence="High", mobility_accessibility="accessible",
    )
    nodes = [Node(node_id=f"n{i}", lat=41.80 + 0.01 * i, lon=-87.63, kind="intersection") for i in range(3)]
    # n0=s, n1=a (midway), n2=t
    def d(i, j):
        return haversine(nodes[i].lat, nodes[i].lon, nodes[j].lat, nodes[j].lon)
    spec = [("direct", 0, 2, 70.0), ("sa", 0, 1, d(0, 1) / 40.0), ("at", 1, 2, d(1, 2) / 40.0)]
    edges = [
        Edge(
            edge_id=eid, from_node=f"n{i}", to_node=f"n{j}", geometry="", distance_meters=d(i, j),
            estimated_travel_time_seconds=t, travel_mode=["driving"], source="osm",
            data_timestamp=TS, classification="Static", **known,
        )
        for eid, i, j, t in spec
    ]
    return Graph._from_model(GraphModel.build(nodes, edges), SimpleNamespace(version="t"), {})


def test_driving_v_max_covers_fast_edges_regression():
    g = _fast_driving_graph()
    inc = Incident(incident_id="i1", incident_type=IncidentType.FLOOD, source="t", created_at=TS, updated_at=TS)
    ctx = IncidentContext(
        incident=inc, now=TS, origin=LatLon(latitude=41.8, longitude=-87.63),
        profile=MobilityProfile.STANDARD, mode=TravelMode.DRIVING,
    )
    # the shipped weights cover the motorway speed assigned by build_graph.py
    assert W.v_max_mps["driving"] >= 24.6
    # and even a stale 20 m/s configuration is raised to the graph's fastest implied speed
    stale = replace(W, v_max_mps={**W.v_max_mps, "driving": 20.0})
    model = CostModel(g, stale, ctx)
    assert model.v_max >= 40.0 - 1e-6
    cost = model.cost_fn()
    best = dijkstra(g, None, 0, 2, cost)
    assert best.edge_ids == ("sa", "at")
    assert astar(g, None, 0, 2, cost, v_max=model.v_max).total_cost == pytest.approx(best.total_cost)
    # documents the original bug: an inadmissible 20 m/s bound returns the worse direct edge
    bad = astar(g, None, 0, 2, cost, v_max=20.0)
    assert bad.total_cost > best.total_cost * 1.1
