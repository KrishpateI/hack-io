"""Penalty-based k alternatives with a Yen-style spur fallback (Req 13.4).

1. Find the optimal path P1.
2. Repeatedly multiply the search cost of every edge on already-seen paths by ``inflate_factor``
   (search only; reported costs are always the true, uninflated ``cost_fn``) and search again, up
   to ``max_attempts`` times. A result is accepted only if it differs from every accepted path
   by at least ``min_distinct_edges`` edges (size of the symmetric difference of edge sets).
3. If fewer than ``k`` distinct routes result, run Yen spur passes on the accepted paths and
   take the cheapest distinct spur path until ``k`` is reached or no candidate remains.

The hard-restriction ``mask`` and ``route_filter`` are applied in every pass and never relaxed.
Fewer than ``k`` routes may be returned; duplicates are never used as padding. Results are
ordered by true cost (ties by edge indices), and the whole procedure is deterministic.
"""
from __future__ import annotations

from dataclasses import replace
import numpy as np

from app.routing.cost import Weights
from app.routing.graph import Graph
from app.routing.search import CostFn, NodeRef, Path, RouteFilter, astar

DEFAULT_INFLATE_FACTOR = 1.5
DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_MIN_DISTINCT_EDGES = 1


def _params(weights: Weights | None) -> tuple[float, int, int]:
    if weights is None:
        return DEFAULT_INFLATE_FACTOR, DEFAULT_MAX_ATTEMPTS, DEFAULT_MIN_DISTINCT_EDGES
    a = weights.alternatives
    return (
        float(a.get("inflate_factor", DEFAULT_INFLATE_FACTOR)),
        int(a.get("max_attempts", DEFAULT_MAX_ATTEMPTS)),
        max(1, int(a.get("min_distinct_edges", DEFAULT_MIN_DISTINCT_EDGES))),
    )


def _true_path(p: Path, cost_fn: CostFn) -> Path:
    return replace(p, total_cost=float(sum(cost_fn(e) for e in p.edge_indices)))


def _distinct(edges: tuple[int, ...], accepted: list[Path], min_distinct: int) -> bool:
    s = set(edges)
    return all(len(s ^ set(a.edge_indices)) >= min_distinct for a in accepted)


def _spur_candidates(
    graph: Graph,
    mask: np.ndarray | None,
    dst: int,
    cost_fn: CostFn,
    v_max: float,
    route_filter: RouteFilter | None,
    accepted: list[Path],
) -> list[Path]:
    """Yen-style spur paths branching off each accepted path (true costs)."""
    out: list[Path] = []
    for base in accepted:
        nodes, edges = base.node_indices, base.edge_indices
        root_cost = 0.0
        for i in range(len(edges)):
            spur = nodes[i]
            root_nodes = set(nodes[:i])
            root_edges = edges[:i]
            blocked = {
                a.edge_indices[i]
                for a in accepted
                if len(a.edge_indices) > i and a.edge_indices[:i] == root_edges
            }

            def usable(e: int, blocked=blocked, root_nodes=root_nodes) -> bool:
                if e in blocked:
                    return False
                if int(graph.edge_to[e]) in root_nodes:
                    return False
                return route_filter(e) if route_filter is not None else True

            sp = astar(graph, mask, spur, dst, cost_fn, v_max=v_max, route_filter=usable)
            if sp is not None:
                node_idx = nodes[:i] + sp.node_indices
                edge_idx = root_edges + sp.edge_indices
                out.append(
                    Path(
                        node_indices=node_idx,
                        edge_indices=edge_idx,
                        node_ids=tuple(graph.node_ids[n] for n in node_idx),
                        edge_ids=tuple(graph.edge_ids[e] for e in edge_idx),
                        total_cost=root_cost + sp.total_cost,
                    )
                )
            root_cost += float(cost_fn(edges[i]))
    return out


def k_routes(
    graph: Graph,
    mask: np.ndarray | None,
    src: NodeRef,
    dst: NodeRef,
    cost_fn: CostFn,
    k: int,
    *,
    v_max: float,
    route_filter: RouteFilter | None = None,
    weights: Weights | None = None,
) -> list[Path]:
    """Up to ``k`` distinct routes from ``src`` to ``dst``, ordered by true cost."""
    if k <= 0:
        return []
    inflate, max_attempts, min_distinct = _params(weights)

    first = astar(graph, mask, src, dst, cost_fn, v_max=v_max, route_filter=route_filter)
    if first is None:
        return []
    first = _true_path(first, cost_fn)
    accepted: list[Path] = [first]
    if k == 1 or not first.edge_indices:
        return accepted

    # --- penalty phase
    mult: dict[int, float] = {}

    def inflate_edges(p: Path) -> None:
        for e in p.edge_indices:
            mult[e] = mult.get(e, 1.0) * inflate

    def inflated(e: int) -> float:
        return float(cost_fn(e)) * mult.get(e, 1.0)

    inflate_edges(first)
    seen: set[tuple[int, ...]] = {first.edge_indices}
    for _ in range(max_attempts):
        if len(accepted) >= k:
            break
        cand = astar(graph, mask, src, dst, inflated, v_max=v_max, route_filter=route_filter)
        if cand is None:
            break
        if cand.edge_indices in seen:
            # Search is stuck on an already-seen path; inflating it again makes progress.
            inflate_edges(cand)
            continue
        seen.add(cand.edge_indices)
        inflate_edges(cand)
        if _distinct(cand.edge_indices, accepted, min_distinct):
            accepted.append(_true_path(cand, cost_fn))

    # --- Yen spur fallback
    dst_idx = first.node_indices[-1]
    src_idx = first.node_indices[0]
    while len(accepted) < k:
        cands = _spur_candidates(graph, mask, dst_idx, cost_fn, v_max, route_filter, accepted)
        cands = [
            _true_path(c, cost_fn)
            for c in cands
            if c.node_indices[0] == src_idx and _distinct(c.edge_indices, accepted, min_distinct)
        ]
        if not cands:
            break
        accepted.append(min(cands, key=lambda c: (c.total_cost, c.edge_indices)))

    accepted.sort(key=lambda p: (p.total_cost, p.edge_indices))
    return accepted[:k]
