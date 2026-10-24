"""RoutingService: orchestrates the routing request pipeline (design: "Routing request pipeline").

validate -> load context -> Rules_Engine.evaluate_pre_route -> data sufficiency ->
RestrictionResolver (hard) -> isolation check -> snap on masked graph -> CostModel (soft) ->
A* -> alternatives -> explain -> RouteResponse -> Rules_Engine.finalize.

Routes are always computed at request time; nothing is precomputed or cached per route. The
service never calls the AI assistant, so it works with the assistant absent (Req 13.9).
Non-route outcomes (SHELTER_IN_PLACE, FOLLOW_OFFICIAL_INSTRUCTIONS, NO_VERIFIED_ROUTE_AVAILABLE,
INSUFFICIENT_DATA) return a normal response whose single placeholder route has empty geometry
and ``route_count`` 0; an outdoor route is never computed for the first two.
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import numpy as np
from fastapi import HTTPException

# Importing the modules registers them with the disaster registry.
from app.disasters import building as _building  # noqa: F401
from app.disasters import earthquake as _earthquake  # noqa: F401
from app.disasters import fire as _fire  # noqa: F401
from app.disasters import flood as _flood  # noqa: F401
from app.disasters import tornado as _tornado  # noqa: F401
from app.disasters.base import DisasterModule, IncidentContext, get_module
from app.disasters.rules_engine import evaluate_pre_route, filter_notices, finalize
from app.models.domain import (
    HazardReport,
    Incident,
    OfficialNotice,
    PolicyDecision,
    PolicyOutcome,
    Restriction,
    UncertaintyLevel,
    VerificationStatus,
)
from app.models.routing import LatLon, LineStringGeometry, Route, RouteRequest, RouteResponse
from app.routing.alternatives import k_routes
from app.routing.cost import CostModel, Weights, WeightsError
from app.routing.explain import RouteCandidate, explain_route, latest_data_timestamp
from app.routing.graph import Graph, decode_geometry
from app.routing.restrictions import ResolvedRestrictions, RestrictionResolver
from app.routing.search import Path
from app.routing.validation import validate_route_request

log = logging.getLogger("safepath.routing")

#: A request point farther than this from every usable edge has no snap candidate.
MAX_SNAP_METERS = 500.0

MISSING_GRAPH = "graph"
MISSING_ORIGIN_SNAP = "origin_snap_candidate"
MISSING_DESTINATION_SNAP = "destination_snap_candidate"
MISSING_INDOOR_GRAPH = "indoor_graph"


class OverlayUnavailable(Exception):
    """Overlays (closures, hazard polygons, notices, reports) could not be read."""


# ---------------------------------------------------------------------------- context
@dataclass(frozen=True)
class ContextData:
    """Everything the pipeline needs about an incident at request time."""

    incident: Incident
    notices: tuple[OfficialNotice, ...] = ()
    reports: tuple[HazardReport, ...] = ()
    overlays: tuple[Restriction, ...] = ()
    overlay_version: int = 0
    data_timestamp: datetime | None = None  # latest overlay/notice/report update applied


@runtime_checkable
class ContextProvider(Protocol):
    def incident_exists(self, incident_id: str) -> bool: ...

    def load(self, incident_id: str, now: datetime) -> ContextData | None:
        """Context for the incident, or None when unknown. Raises ``OverlayUnavailable``
        (or any exception) when overlays cannot be read; the service then fails closed."""


class InMemoryContextProvider:
    """Thread-safe in-memory provider (tests and local demo). Task 8.5 adds a
    Repository-backed implementation of the same protocol."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._incidents: dict[str, Incident] = {}
        self._notices: dict[str, dict[str, OfficialNotice]] = {}
        self._reports: dict[str, dict[str, HazardReport]] = {}
        self._overlays: dict[str, dict[str, Restriction]] = {}
        self._versions: dict[str, int] = {}
        self._updated: dict[str, datetime] = {}
        self.fail_overlays = False  # simulate an overlay read failure

    def _touch(self, incident_id: str) -> None:
        self._versions[incident_id] = self._versions.get(incident_id, 0) + 1
        self._updated[incident_id] = datetime.now(timezone.utc)

    def add_incident(self, incident: Incident) -> None:
        with self._lock:
            self._incidents[incident.incident_id] = incident
            self._touch(incident.incident_id)

    def add_notice(self, notice: OfficialNotice) -> None:
        with self._lock:
            self._notices.setdefault(notice.incident_id, {})[notice.notice_id] = notice
            self._touch(notice.incident_id)

    def add_report(self, report: HazardReport) -> None:
        with self._lock:
            self._reports.setdefault(report.incident_id, {})[report.report_id] = report
            self._touch(report.incident_id)

    def add_overlay(self, restriction: Restriction) -> None:
        with self._lock:
            self._overlays.setdefault(restriction.incident_id, {})[restriction.restriction_id] = restriction
            self._touch(restriction.incident_id)

    def remove_overlay(self, incident_id: str, restriction_id: str) -> None:
        with self._lock:
            self._overlays.get(incident_id, {}).pop(restriction_id, None)
            self._touch(incident_id)

    def clear(self) -> None:
        with self._lock:
            for d in (self._incidents, self._notices, self._reports, self._overlays,
                      self._versions, self._updated):
                d.clear()

    # ----------------------------------------------------------------------- protocol
    def incident_exists(self, incident_id: str) -> bool:
        with self._lock:
            return incident_id in self._incidents

    def load(self, incident_id: str, now: datetime) -> ContextData | None:
        with self._lock:
            incident = self._incidents.get(incident_id)
            if incident is None:
                return None
            if self.fail_overlays:
                raise OverlayUnavailable("overlay store unavailable")
            reports = tuple(
                r for r in self._reports.get(incident_id, {}).values()
                if r.verification_status not in (VerificationStatus.RESOLVED, VerificationStatus.EXPIRED)
                and r.expires_at > now
            )
            return ContextData(
                incident=incident,
                notices=tuple(self._notices.get(incident_id, {}).values()),
                reports=reports,
                overlays=tuple(self._overlays.get(incident_id, {}).values()),
                overlay_version=self._versions.get(incident_id, 0),
                data_timestamp=self._updated.get(incident_id),
            )


# ----------------------------------------------------------------------------- service
class RoutingService:
    def __init__(
        self,
        graph_provider: Callable[[], Graph],
        weights_provider: Callable[[], Weights],
        context_provider: ContextProvider,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        resolver: RestrictionResolver | None = None,
    ) -> None:
        self._graph = graph_provider
        self._weights = weights_provider
        self.context_provider = context_provider
        self._clock = clock
        self.resolver = resolver or RestrictionResolver()
        self._mask_cache: dict[tuple, np.ndarray] = {}
        self._mask_lock = threading.Lock()

    # ------------------------------------------------------------------- public API
    def validate(self, raw: Any) -> RouteRequest:
        """400 for bad input, 404 for an unknown incident (raises RequestValidationError)."""
        try:
            return validate_route_request(raw, self.context_provider.incident_exists)
        except OverlayUnavailable:
            raise _unavailable() from None

    def calculate(self, req: RouteRequest, principal: Any = None) -> RouteResponse:
        now = self._clock()
        weights = self._load_weights()
        try:
            data = self.context_provider.load(req.incident_id, now)
        except HTTPException:
            raise
        except Exception as exc:  # fail closed: never route without the overlays
            log.error("overlay read failed: %s", exc)
            raise _unavailable() from None
        if data is None:
            from app.routing.validation import RequestValidationError

            raise RequestValidationError(
                404, "not_found", f"incident '{req.incident_id}' not found",
                [{"field": "incident_id", "problem": "unknown incident"}],
            )

        graph = self._graph()
        ctx = IncidentContext(
            incident=data.incident,
            now=now,
            origin=req.origin,
            destination=req.destination,
            profile=req.mobility_profile,
            mode=req.travel_mode,
            notices=data.notices,
            reports=data.reports,
            overlays=data.overlays,
        )
        module = _module_for(ctx)

        indoor_node: int | None = None
        missing: list[str] = []
        if not graph.loaded:
            missing.append(MISSING_GRAPH)
        else:
            if req.origin_indoor_node_id is not None:
                indoor_node = self._indoor_index(graph, req.origin_indoor_node_id)
                if indoor_node is None:
                    missing.append(MISSING_INDOOR_GRAPH)
            elif _snap(graph, req.origin, None) is None:
                missing.append(MISSING_ORIGIN_SNAP)
            if _snap(graph, req.destination, None) is None:
                missing.append(MISSING_DESTINATION_SNAP)

        decision = evaluate_pre_route(ctx, module=module, missing_data=missing)
        eligible, _ = filter_notices(ctx)
        notice_sources = [n.source_record_id for n in eligible]
        advisories = module.advisories(ctx) if module is not None else []
        data_ts = latest_data_timestamp([
            data.data_timestamp,
            *(n.retrieved_at for n in eligible),
            graph.manifest.built_at if graph.manifest else None,
        ])
        base = dict(
            calculated_at=now,
            graph_version=graph.version,
            data_timestamp=data_ts,
        )
        adv_sources = [a.source_record_id for a in advisories]

        if decision.outcome is not PolicyOutcome.ROUTE_OPTIONS_AVAILABLE:
            return self._non_route(
                decision, now, base, sources=[*notice_sources, *adv_sources],
                limitations=[a.text for a in advisories],
            )

        # ---- hard restrictions (separate step from penalties)
        resolved = self.resolver.resolve(
            ctx, graph, [module] if module is not None else [],
            overlay_version=data.overlay_version,
        )
        restr_mask = np.asarray(resolved.mask(graph))
        try:
            cost_model = CostModel(graph, weights, ctx, module)
        except ValueError as exc:
            raise HTTPException(503, f"routing configuration does not support this request: {exc}") from None
        base_usable = cost_model.step_free_mask() & self._mode_mask(
            graph, req.travel_mode.value, indoor_node is not None
        )  # before hard restrictions: used to tell "isolated by restrictions" from "no data"
        usable = restr_mask & base_usable

        # ---- isolation and snapping on the masked graph
        isolated: list[str] = []
        src_idx: int | None = None
        dst_idx: int | None = None
        outdoor = self._outdoor_mask(graph)
        if indoor_node is not None:
            if not usable[graph.out_edges(indoor_node)].any():
                isolated.append("origin")
            else:
                src_idx = indoor_node
        else:
            src_idx = self._endpoint(graph, req.origin, base_usable, usable, outdoor, outgoing=True)
            if src_idx is None:
                isolated.append("origin")
        dst_idx = self._endpoint(graph, req.destination, base_usable, usable, outdoor, outgoing=False)
        if dst_idx is None:
            isolated.append("destination")

        paths: list[Path] = []
        if not isolated and src_idx is not None and dst_idx is not None:
            paths = k_routes(
                graph, usable, src_idx, dst_idx, cost_model.cost_fn(), req.alternatives,
                v_max=cost_model.v_max,
                route_filter=_module_filter(graph, ctx, module),
                weights=weights,
            )

        final = finalize(decision, bool(paths), isolated)
        if final.outcome is not PolicyOutcome.ROUTE_OPTIONS_AVAILABLE:
            return self._non_route(
                final, now, base, sources=[*notice_sources, *adv_sources, *_restriction_sources(resolved)],
                limitations=[a.text for a in advisories], resolved=resolved,
            )

        routes = self._build_routes(
            graph, paths, cost_model, resolved, weights, ctx, module, now,
            data_ts=data_ts, source_ids=[*notice_sources, *adv_sources], advisories=advisories,
        )
        sources = _dedupe([*notice_sources, *adv_sources, *(s for r in routes for s in r.sources)])
        n = len(routes)
        explanation = f"{n} algorithm-generated route option{'s' if n != 1 else ''} available. " \
                      "Review the restrictions considered and uncertainty before choosing."
        return RouteResponse(
            policy_outcome=PolicyOutcome.ROUTE_OPTIONS_AVAILABLE,
            explanation=explanation,
            status="ok",
            route_count=n,
            routes=routes,
            notices=final.notices,
            sources=sources,
            **base,
        )

    # ---------------------------------------------------------------------- internals
    def _load_weights(self) -> Weights:
        try:
            return self._weights()
        except HTTPException as exc:
            d = exc.detail
            msg = (
                f"routing weights invalid: {d.get('file')} field '{d.get('field')}': {d.get('message')}"
                if isinstance(d, dict) else str(d)
            )
            raise HTTPException(503, msg) from None
        except WeightsError as exc:
            raise HTTPException(503, f"routing weights invalid: {exc}") from None

    @staticmethod
    def _indoor_index(graph: Graph, node_id: str) -> int | None:
        idx = graph.node_index.get(node_id)
        if idx is None or graph.model is None or graph.model.nodes[idx].kind != "indoor":
            return None
        return idx

    def _edge_masks(self, graph: Graph, key: tuple, build: Callable[[], np.ndarray]) -> np.ndarray:
        full = (id(graph), graph.version, *key)
        with self._mask_lock:
            hit = self._mask_cache.get(full)
            if hit is None:
                if len(self._mask_cache) > 32:
                    self._mask_cache.clear()
                hit = self._mask_cache[full] = build()
            return hit

    def _outdoor_mask(self, graph: Graph) -> np.ndarray:
        """True for street edges (snap candidates for outdoor points)."""
        return self._edge_masks(
            graph, ("outdoor",),
            lambda: np.fromiter((e.edge_kind == "street" for e in graph.model.edges),  # type: ignore[union-attr]
                                dtype=bool, count=graph.num_edges),
        )

    def _mode_mask(self, graph: Graph, mode: str, indoor: bool) -> np.ndarray:
        """Edges usable for the travel mode. Outdoor-only requests also skip indoor edges."""

        def build() -> np.ndarray:
            walking = mode != "driving"
            out = np.zeros(graph.num_edges, dtype=bool)
            for i, e in enumerate(graph.model.edges):  # type: ignore[union-attr]
                if mode not in e.travel_mode:
                    continue
                if walking and e.pedestrian_access is False:
                    continue
                if not indoor and e.edge_kind != "street":
                    continue
                out[i] = True
            return out

        return self._edge_masks(graph, ("mode", mode, indoor), build)

    @staticmethod
    def _endpoint(
        graph: Graph, point: LatLon, base_usable: np.ndarray, usable: np.ndarray,
        outdoor: np.ndarray, *, outgoing: bool,
    ) -> int | None:
        """Node to route from/to, or None when hard restrictions isolate the point.

        The point is first snapped ignoring hard restrictions; if the node it belongs to has no
        remaining usable edge, it is isolated (Req 14.8). Otherwise it is snapped again on the
        masked graph so the route never starts or ends on a blocked edge.
        """
        nearest = _snap(graph, point, base_usable & outdoor)
        if nearest is None:
            return None
        if not _has_exit(graph, usable, graph.node_index[nearest.node_id], outgoing=outgoing):
            return None
        s = _snap(graph, point, usable & outdoor)
        if s is None or not _has_exit(graph, usable, graph.node_index[s.node_id], outgoing=outgoing):
            return None
        return graph.node_index[s.node_id]

    def _non_route(
        self,
        decision: PolicyDecision,
        now: datetime,
        base: dict,
        *,
        sources: Iterable[str | None],
        limitations: Sequence[str] = (),
        resolved: ResolvedRestrictions | None = None,
    ) -> RouteResponse:
        from app.routing.explain import restrictions_considered

        src = _dedupe(sources)
        considered = restrictions_considered(resolved) if resolved is not None else []
        placeholder = Route(
            route_id=_route_id(),
            route_type="no-verified-route"
            if decision.outcome is PolicyOutcome.NO_VERIFIED_ROUTE_AVAILABLE
            else "policy",
            geometry=LineStringGeometry(coordinates=[]),
            distance_meters=0.0,
            estimated_duration_seconds=0.0,
            excluded_edges_count=len(resolved.blocked_edges) if resolved is not None else 0,
            uncertainty_level=UncertaintyLevel.UNKNOWN,
            policy_outcome=decision.outcome,
            restrictions_considered=considered,
            reason_summary=decision.explanation,
            sources=src,
            confidence_limitations=list(limitations),
            calculated_at=now,
            status=decision.outcome.value.lower(),
        )
        return RouteResponse(
            policy_outcome=decision.outcome,
            explanation=decision.explanation,
            status=decision.outcome.value.lower(),
            route_count=0,
            routes=[placeholder],
            notices=decision.notices,
            missing_data=decision.missing_data,
            sources=src,
            **base,
        )

    def _build_routes(
        self,
        graph: Graph,
        paths: list[Path],
        cost_model: CostModel,
        resolved: ResolvedRestrictions,
        weights: Weights,
        ctx: IncidentContext,
        module: DisasterModule | None,
        now: datetime,
        *,
        data_ts: datetime | None,
        source_ids: list[str | None],
        advisories: list,
    ) -> list[Route]:
        ids = [_route_id() for _ in paths]
        comps = [[cost_model.components(e) for e in p.edge_indices] for p in paths]
        candidates = [
            RouteCandidate(ids[i], comps[i], [float(graph.edge_dist[e]) for e in p.edge_indices])
            for i, p in enumerate(paths)
        ]
        floor = module.uncertainty_floor(ctx) if module is not None else None
        out: list[Route] = []
        for i, p in enumerate(paths):
            expl = explain_route(
                i, candidates, resolved, weights,
                excluded_edges_count=len(resolved.blocked_edges),
                floor=floor,
                incident_type=ctx.incident.incident_type,
                data_timestamps=[data_ts],
                source_record_ids=source_ids,
                advisories=advisories,
                simulated=ctx.incident.simulation_flag,
            )
            route = Route(
                route_id=ids[i],
                route_type="disaster-adjusted" if i == 0 else "alternate",
                geometry=LineStringGeometry(coordinates=_path_coordinates(graph, p)),
                distance_meters=float(sum(graph.edge_dist[e] for e in p.edge_indices)),
                estimated_duration_seconds=float(sum(c.travel for c in comps[i])),
                excluded_edges_count=len(resolved.blocked_edges),
                uncertainty_level=expl.uncertainty_level,
                policy_outcome=PolicyOutcome.ROUTE_OPTIONS_AVAILABLE,
                calculated_at=now,
                status="ok",
            )
            out.append(expl.apply(route))
        return out


# ----------------------------------------------------------------------------- helpers
def _route_id() -> str:
    return f"rt-{uuid.uuid4().hex[:12]}"


def _dedupe(items: Iterable[str | None]) -> list[str]:
    seen: dict[str, None] = {}
    for x in items:
        if x:
            seen.setdefault(x, None)
    return list(seen)


def _restriction_sources(resolved: ResolvedRestrictions) -> list[str | None]:
    return [c.source_record_id for cs in resolved.blocked_edges.values() for c in cs]


def _unavailable() -> HTTPException:
    return HTTPException(
        503, "road restrictions and overlays could not be read; routing is unavailable until they can"
    )


def _module_for(ctx: IncidentContext) -> DisasterModule | None:
    try:
        return get_module(ctx.incident.incident_type)
    except KeyError:
        return None


def _snap(graph: Graph, point: LatLon, mask: np.ndarray | None):
    s = graph.snap(point.latitude, point.longitude, mask)
    if s is None or s.distance_m > MAX_SNAP_METERS:
        return None
    return s


def _has_exit(graph: Graph, usable: np.ndarray, node: int, *, outgoing: bool) -> bool:
    edges = graph.out_edges(node) if outgoing else np.flatnonzero(graph.edge_to == node)
    return bool(len(edges) and usable[edges].any())


def _module_filter(graph: Graph, ctx: IncidentContext, module: DisasterModule | None):
    if module is None:
        return None
    cache: dict[int, bool] = {}

    def allowed(e: int) -> bool:
        v = cache.get(e)
        if v is None:
            v = cache[e] = bool(module.route_filter(graph.edge(e), ctx))
        return v

    return allowed


def _edge_coordinates(graph: Graph, e: int) -> list[list[float]]:
    """Edge geometry (WKT) as [lon, lat] pairs oriented from_node -> to_node."""
    coords = decode_geometry(graph.edge(e).geometry)
    a, b = int(graph.edge_from[e]), int(graph.edge_to[e])
    start = (float(graph.node_lon[a]), float(graph.node_lat[a]))
    end = (float(graph.node_lon[b]), float(graph.node_lat[b]))
    if coords is None:
        return [list(start), list(end)]

    def d2(p: tuple[float, float], q: tuple[float, float]) -> float:
        return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2

    if d2(coords[0], end) + d2(coords[-1], start) < d2(coords[0], start) + d2(coords[-1], end):
        coords = coords[::-1]
    return [[x, y] for x, y in coords]


def _path_coordinates(graph: Graph, path: Path) -> list[list[float]]:
    if not path.edge_indices:  # origin and destination snapped to the same node
        n = path.node_indices[0]
        pt = [float(graph.node_lon[n]), float(graph.node_lat[n])]
        return [pt, list(pt)]
    out: list[list[float]] = []
    for e in path.edge_indices:
        pts = _edge_coordinates(graph, e)
        out.extend(pts[1:] if out and out[-1] == pts[0] else pts)
    return out
