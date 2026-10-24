import random
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app.models.graph import Edge, Graph as GraphModel, Node
from app.routing.graph import Graph
from app.routing.search import astar, dijkstra, search

TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
V_MAX = 1.0  # cost_fn below is >= distance_meters, so distance/1 m/s is admissible


def build(nodes, edges):
    """nodes: {id: (lat, lon)}; edges: [(id, from, to, meters)] (directed)."""
    ns = [Node(node_id=i, lat=la, lon=lo, kind="intersection") for i, (la, lo) in nodes.items()]
    es = [
        Edge(
            edge_id=i, from_node=f, to_node=t, geometry="", distance_meters=d,
            estimated_travel_time_seconds=d, travel_mode=["walking"], source="osm",
            data_timestamp=TS, classification="Static",
        )
        for i, f, t, d in edges
    ]
    return Graph._from_model(GraphModel.build(ns, es), SimpleNamespace(version="t"), {})


def both(i, a, b, d):
    return [(f"{i}f", a, b, d), (f"{i}r", b, a, d)]


def six_node_graph():
    # 0.001 deg lat ~ 111 m; layout:  a - b - c
    #                                 |   |   |
    #                                 d - e - f
    pos = {
        "a": (41.8810, -87.650), "b": (41.8810, -87.649), "c": (41.8810, -87.648),
        "d": (41.8800, -87.650), "e": (41.8800, -87.649), "f": (41.8800, -87.648),
    }
    edges = []
    for i, a, b, d in [
        ("ab", "a", "b", 83), ("bc", "b", "c", 83), ("de", "d", "e", 83), ("ef", "e", "f", 83),
        ("ad", "a", "d", 111), ("be", "b", "e", 111), ("cf", "c", "f", 111),
    ]:
        edges += both(i, a, b, d)
    return build(pos, edges)


def cost_of(g):
    return lambda e: float(g.edge_dist[e])


def run(g, src, dst, mask=None, **kw):
    return astar(g, mask, src, dst, cost_of(g), v_max=V_MAX, **kw)


def test_known_shortest_path():
    g = six_node_graph()
    p = run(g, "a", "f")
    assert p.total_cost == pytest.approx(83 + 83 + 111)
    assert p.edge_ids == tuple(g.edge_ids[e] for e in p.edge_indices)
    assert p.node_ids[0] == "a" and p.node_ids[-1] == "f"
    assert search(g, None, "a", "f", cost_of(g), v_max=V_MAX).total_cost == p.total_cost


def test_mask_removal_changes_path():
    g = six_node_graph()
    base = run(g, "a", "c")
    assert base.node_ids == ("a", "b", "c") and base.total_cost == 166
    mask = np.ones(g.num_edges, dtype=bool)
    mask[g.edge_ids.index("abf")] = False
    alt = run(g, "a", "c", mask)
    # detour via the lower row; a-d-e-b-c and a-d-e-f-c tie at 388, either is optimal
    assert alt.node_ids[:3] == ("a", "d", "e") and alt.node_ids[-1] == "c"
    assert alt.total_cost == pytest.approx(111 + 83 + 111 + 83)
    assert "abf" not in alt.edge_ids
    # mask is not mutated by the search
    assert mask.sum() == g.num_edges - 1


def test_route_filter_excludes_edges():
    g = six_node_graph()
    banned = {g.edge_ids.index("bcf"), g.edge_ids.index("bcr")}
    p = run(g, "a", "c", route_filter=lambda e: e not in banned)
    assert p.total_cost > 166 and not (set(p.edge_indices) & banned)
    assert run(g, "a", "c", route_filter=lambda e: False) is None


def test_directed_edge_respected():
    g = build(
        {"x": (41.88, -87.65), "y": (41.88, -87.64)},
        [("xy", "x", "y", 830)],
    )
    assert run(g, "x", "y").total_cost == 830
    assert run(g, "y", "x") is None
    assert dijkstra(g, None, "y", "x", cost_of(g)) is None


def test_disconnected_and_unknown_nodes_return_none():
    g = build(
        {"x": (41.88, -87.65), "y": (41.88, -87.64), "z": (41.89, -87.64)},
        both("xy", "x", "y", 830),
    )
    assert run(g, "x", "z") is None
    assert dijkstra(g, None, "x", "z", cost_of(g)) is None
    assert run(g, "x", "nope") is None


def test_same_source_and_destination():
    g = six_node_graph()
    p = run(g, "a", "a")
    assert p.total_cost == 0 and p.edge_indices == () and p.node_ids == ("a",)


def test_invalid_inputs():
    g = six_node_graph()
    with pytest.raises(ValueError):
        run(g, "a", "f", np.ones(1, dtype=bool))
    with pytest.raises(ValueError):
        astar(g, None, "a", "f", lambda e: -1.0, v_max=V_MAX)
    with pytest.raises(ValueError):
        astar(g, None, "a", "f", cost_of(g), v_max=0)


def test_deterministic_tie_breaking():
    # two equal-cost routes x->y->w and x->z->w
    g = build(
        {"x": (41.88, -87.65), "y": (41.881, -87.649), "z": (41.879, -87.649), "w": (41.88, -87.648)},
        [("xy", "x", "y", 100), ("yw", "y", "w", 100), ("xz", "x", "z", 100), ("zw", "z", "w", 100)],
    )
    first = run(g, "x", "w")
    for _ in range(5):
        assert run(g, "x", "w") == first
    assert dijkstra(g, None, "x", "w", cost_of(g)).total_cost == first.total_cost


@pytest.mark.parametrize("seed", range(5))
def test_astar_cost_equals_dijkstra_on_random_graphs(seed):
    rng = random.Random(seed)
    n = 25
    pos = {f"n{i}": (41.85 + rng.random() * 0.05, -87.68 + rng.random() * 0.07) for i in range(n)}
    from app.geo import haversine

    edges = []
    for k in range(80):
        a, b = rng.sample(sorted(pos), 2)
        d = haversine(*pos[a], *pos[b])
        edges.append((f"e{k}", a, b, d))
    g = build(pos, edges)
    factors = [1.0 + rng.random() * 3 for _ in range(g.num_edges)]
    cost = lambda e: float(g.edge_dist[e]) * factors[e]  # noqa: E731
    mask = np.array([rng.random() > 0.15 for _ in range(g.num_edges)])
    for _ in range(15):
        s, t = rng.randrange(n), rng.randrange(n)
        a = astar(g, mask, s, t, cost, v_max=V_MAX)
        d = dijkstra(g, mask, s, t, cost)
        assert (a is None) == (d is None)
        if a is not None:
            assert a.total_cost == pytest.approx(d.total_cost)
            assert all(mask[e] for e in a.edge_indices)
