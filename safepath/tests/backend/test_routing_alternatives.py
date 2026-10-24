from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app.models.graph import Edge, Graph as GraphModel, Node
from app.routing.alternatives import k_routes
from app.routing.graph import Graph

TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
V_MAX = 1.0


def build(nodes, edges):
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


def diamond():
    """s -> a -> t (cost 200) and s -> b -> t (cost 260); directed, only two routes exist."""
    pos = {"s": (41.880, -87.650), "a": (41.881, -87.649), "b": (41.879, -87.649),
           "t": (41.880, -87.648)}
    edges = [("sa", "s", "a", 100), ("at", "a", "t", 100),
             ("sb", "s", "b", 130), ("bt", "b", "t", 130)]
    return build(pos, edges)


def cost_of(g):
    return lambda e: float(g.edge_dist[e])


def routes(g, k, mask=None, src="s", dst="t"):
    return k_routes(g, mask, src, dst, cost_of(g), k, v_max=V_MAX)


def test_two_clear_alternatives_ordered_with_true_cost():
    g = diamond()
    r = routes(g, 2)
    assert [p.node_ids for p in r] == [("s", "a", "t"), ("s", "b", "t")]
    assert [p.total_cost for p in r] == [200.0, 260.0]  # uninflated


def test_k_greater_than_available_returns_fewer_without_duplicates():
    r = routes(diamond(), 3)
    assert len(r) == 2
    assert len({p.edge_indices for p in r}) == 2


def test_k_one_and_unreachable_and_nonpositive():
    g = diamond()
    assert len(routes(g, 1)) == 1
    assert routes(g, 0) == []
    assert routes(g, 2, src="t", dst="s") == []


def test_mask_is_respected_in_all_passes():
    g = diamond()
    mask = np.ones(g.num_edges, dtype=bool)
    mask[g.edge_ids.index("sb")] = False
    r = routes(g, 3, mask)
    assert len(r) == 1 and "sb" not in r[0].edge_ids


def test_route_filter_respected():
    g = diamond()
    bad = g.edge_ids.index("at")
    r = k_routes(g, None, "s", "t", cost_of(g), 2, v_max=V_MAX, route_filter=lambda e: e != bad)
    assert [p.node_ids for p in r] == [("s", "b", "t")]


def test_distinctness_and_spur_fallback_on_shared_bridge():
    # Parallel options only after a shared first edge: penalty alone may stall, spur must find them.
    pos = {"s": (41.880, -87.650), "m": (41.880, -87.649), "a": (41.881, -87.648),
           "b": (41.879, -87.648), "t": (41.880, -87.647)}
    edges = [("sm", "s", "m", 100), ("ma", "m", "a", 50), ("at", "a", "t", 50),
             ("mb", "m", "b", 70), ("bt", "b", "t", 70)]
    g = build(pos, edges)
    r = routes(g, 3)
    assert len(r) == 2
    sets = [set(p.edge_indices) for p in r]
    assert len(sets[0] ^ sets[1]) >= 1
    for p in r:
        assert p.node_ids[0] == "s" and p.node_ids[-1] == "t"
        assert p.total_cost == pytest.approx(sum(g.edge_dist[e] for e in p.edge_indices))
    assert r[0].total_cost <= r[1].total_cost


def test_deterministic():
    g = diamond()
    a = routes(g, 3)
    b = routes(g, 3)
    assert [p.edge_indices for p in a] == [p.edge_indices for p in b]
    assert [p.total_cost for p in a] == [p.total_cost for p in b]
