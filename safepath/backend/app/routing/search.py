"""Least-cost path search over the masked, directed routing graph.

``astar`` uses the admissible and consistent heuristic ``haversine(n, dst) / v_max`` (design:
CostModel soft step; Route_Cost(e) >= travel_cost(e) >= distance(e) / v_max). ``dijkstra`` is the
reference implementation that tests compare against.

Edges are traversed only from ``edge_from`` to ``edge_to``; the reverse direction is usable only
if the graph contains a separate reverse edge. ``mask`` is a boolean per-edge array (True =
usable). ``route_filter(edge_index) -> bool`` is an optional extra predicate (True = usable),
e.g. a disaster module's outdoor-edge exclusion. ``cost_fn(edge_index) -> float`` must return a
finite, non-negative cost.

Tie-breaking is deterministic: the heap orders by (priority, node index), edges are scanned in
CSR order, and a predecessor is replaced only on a strictly lower cost.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from app.geo import haversine
from app.routing.graph import Graph

CostFn = Callable[[int], float]
RouteFilter = Callable[[int], bool]
NodeRef = int | str


@dataclass(frozen=True)
class Path:
    node_indices: tuple[int, ...]
    edge_indices: tuple[int, ...]
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    total_cost: float


def _resolve_node(graph: Graph, ref: NodeRef) -> int | None:
    if isinstance(ref, str):
        return graph.node_index.get(ref)
    idx = int(ref)
    return idx if 0 <= idx < graph.num_nodes else None


def _check_mask(graph: Graph, mask: np.ndarray | None) -> np.ndarray | None:
    if mask is None:
        return None
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != (graph.num_edges,):
        raise ValueError("mask must be a boolean array with one entry per edge")
    return mask


def _edge_cost(cost_fn: CostFn, e: int) -> float:
    c = float(cost_fn(e))
    if math.isnan(c) or c < 0 or math.isinf(c):
        raise ValueError(f"cost_fn must return a finite non-negative cost (edge {e}: {c})")
    return c


def _build_path(graph: Graph, src: int, dst: int, prev_edge: dict[int, int], cost: float) -> Path:
    edges: list[int] = []
    nodes = [dst]
    n = dst
    while n != src:
        e = prev_edge[n]
        edges.append(e)
        n = int(graph.edge_from[e])
        nodes.append(n)
    edges.reverse()
    nodes.reverse()
    return Path(
        node_indices=tuple(nodes),
        edge_indices=tuple(edges),
        node_ids=tuple(graph.node_ids[i] for i in nodes),
        edge_ids=tuple(graph.edge_ids[e] for e in edges),
        total_cost=cost,
    )


def _search(
    graph: Graph,
    mask: np.ndarray | None,
    src: NodeRef,
    dst: NodeRef,
    cost_fn: CostFn,
    route_filter: RouteFilter | None,
    v_max: float | None,
) -> Path | None:
    s = _resolve_node(graph, src)
    t = _resolve_node(graph, dst)
    if s is None or t is None:
        return None
    mask = _check_mask(graph, mask)
    if s == t:
        return Path((s,), (), (graph.node_ids[s],), (), 0.0)

    lat, lon = graph.node_lat, graph.node_lon
    t_lat, t_lon = float(lat[t]), float(lon[t])
    h_cache: dict[int, float] = {}

    def h(n: int) -> float:
        if v_max is None:
            return 0.0
        v = h_cache.get(n)
        if v is None:
            v = haversine(float(lat[n]), float(lon[n]), t_lat, t_lon) / v_max
            h_cache[n] = v
        return v

    g: dict[int, float] = {s: 0.0}
    prev_edge: dict[int, int] = {}
    closed: set[int] = set()
    heap: list[tuple[float, int]] = [(h(s), s)]
    while heap:
        _, n = heapq.heappop(heap)
        if n in closed:
            continue
        if n == t:
            return _build_path(graph, s, t, prev_edge, g[t])
        closed.add(n)
        gn = g[n]
        for e in graph.out_edges(n):
            e = int(e)
            if mask is not None and not mask[e]:
                continue
            if route_filter is not None and not route_filter(e):
                continue
            m = int(graph.edge_to[e])
            if m in closed:
                continue
            ng = gn + _edge_cost(cost_fn, e)
            if ng < g.get(m, math.inf):
                g[m] = ng
                prev_edge[m] = e
                heapq.heappush(heap, (ng + h(m), m))
    return None


def astar(
    graph: Graph,
    mask: np.ndarray | None,
    src: NodeRef,
    dst: NodeRef,
    cost_fn: CostFn,
    *,
    v_max: float,
    route_filter: RouteFilter | None = None,
) -> Path | None:
    """A* from ``src`` to ``dst`` (node index or id). ``v_max`` is the max speed in m/s that
    bounds ``distance / cost`` for every edge (heuristic admissibility). Returns None if no path."""
    if not (v_max > 0 and math.isfinite(v_max)):
        raise ValueError("v_max must be a positive finite speed (m/s)")
    return _search(graph, mask, src, dst, cost_fn, route_filter, v_max)


def dijkstra(
    graph: Graph,
    mask: np.ndarray | None,
    src: NodeRef,
    dst: NodeRef,
    cost_fn: CostFn,
    *,
    route_filter: RouteFilter | None = None,
) -> Path | None:
    """Reference Dijkstra (zero heuristic). Used by tests as the optimality oracle."""
    return _search(graph, mask, src, dst, cost_fn, route_filter, None)


def search(
    graph: Graph,
    mask: np.ndarray | None,
    src: NodeRef,
    dst: NodeRef,
    cost_fn: CostFn,
    *,
    v_max: float,
    route_filter: RouteFilter | None = None,
) -> Path | None:
    """Design-level entry point: A* over the masked graph."""
    return astar(graph, mask, src, dst, cost_fn, v_max=v_max, route_filter=route_filter)
