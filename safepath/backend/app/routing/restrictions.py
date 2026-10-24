"""RestrictionResolver: the hard step of the routing pipeline (Req 14, 11.7).

Produces a ``RestrictionSet`` (edge_id -> causes) from confirmed closures, official
prohibited/impassable areas, notice road-direction restrictions and module hard restrictions.
Unverified / Corroborated / Disputed reports are never hard restrictions (14.5); they are
ignored here and only reach the CostModel as soft context.

Geometry rule (14.4): an edge is blocked when its LineString ``intersects`` the polygon. The
graph's STRtree supplies candidates and shapely performs the exact predicate test.

Direction rule (11.7): ``road_direction_restriction`` notices list *directed* edge ids in the
prohibited direction. Only those edges are blocked; the reverse edge stays usable.
"""
from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from app.disasters.base import DisasterModule, IncidentContext, RestrictionSet
from app.models.domain import (
    HazardReport,
    OfficialNotice,
    Restriction,
    RestrictionCause,
    VerificationStatus,
)
from app.routing.graph import _COS_REF, _M_PER_DEG, Graph

NOTICE_EXPIRY_GRACE = timedelta(seconds=60)  # Req 11.9

_AREA_NOTICE_KINDS = {
    "temporary_perimeter": "prohibited_area",
    "road_closure": "closure",
}


@dataclass(frozen=True)
class ResolvedRestrictions(RestrictionSet):
    """RestrictionSet plus resolution metadata."""

    excluded_edges_count: int = 0
    isolated_origin: bool = False
    isolated_destination: bool = False

    @property
    def isolated(self) -> bool:
        return self.isolated_origin or self.isolated_destination


# ------------------------------------------------------------------------------ helpers
def _to_projected(geojson: Mapping) -> BaseGeometry | None:
    """GeoJSON (lon/lat) -> shapely geometry in the graph's local metric projection."""
    try:
        geom = shape(dict(geojson))
        if geom.is_empty:
            return None
        if not geom.is_valid:
            geom = geom.buffer(0)
            if geom.is_empty:
                return None
        scale = np.array([_COS_REF * _M_PER_DEG, _M_PER_DEG])
        return shapely.transform(geom, lambda c: c[:, :2] * scale, include_z=False)
    except Exception:
        return None


def _notice_active(n: OfficialNotice, ctx: IncidentContext) -> bool:
    if n.incident_id != ctx.incident.incident_id or n.cbd_derived or not n.source_record_id:
        return False
    if n.effective_at > ctx.now:
        return False
    return n.expires_at is None or ctx.now <= n.expires_at + NOTICE_EXPIRY_GRACE


def _report_confirmed(r: HazardReport, ctx: IncidentContext) -> bool:
    return (
        r.incident_id == ctx.incident.incident_id
        and r.verification_status == VerificationStatus.OFFICIALLY_VERIFIED
        and r.expires_at > ctx.now
    )


class _Collector:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.index = {eid: i for i, eid in enumerate(graph.edge_ids)}
        self.blocked: dict[str, list[RestrictionCause]] = {}

    def add_edges(self, edge_ids: Iterable[str], cause: RestrictionCause) -> None:
        for eid in edge_ids:
            if eid not in self.index:
                continue
            causes = self.blocked.setdefault(eid, [])
            if cause not in causes:
                causes.append(cause)

    def add_geometry(self, geojson: Mapping | None, cause: RestrictionCause) -> None:
        if not geojson or self.graph.num_edges == 0:
            return
        geom = _to_projected(geojson)
        if geom is None:
            return
        g = self.graph
        g._ensure_tree()
        assert g._tree is not None
        hits = g._tree.query(geom, predicate="intersects")  # exact test on STRtree candidates
        self.add_edges((g.edge_ids[int(i)] for i in hits), cause)


# ------------------------------------------------------------------------------ resolver
class RestrictionResolver:
    def __init__(self, max_cache: int = 64) -> None:
        self._cache: dict[tuple, ResolvedRestrictions] = {}
        self._max_cache = max_cache
        self._lock = threading.Lock()

    def resolve(
        self,
        ctx: IncidentContext,
        graph: Graph,
        modules: Iterable[DisasterModule] | Mapping | None = None,
        *,
        overlay_version: int = 0,
    ) -> ResolvedRestrictions:
        mods = list(modules.values()) if isinstance(modules, Mapping) else list(modules or [])
        key = (
            overlay_version,
            ctx.incident.incident_id,
            graph.version,
            self._fingerprint(ctx, mods),
        )
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return hit
        result = self._compute(ctx, graph, mods, overlay_version)
        with self._lock:
            if len(self._cache) >= self._max_cache:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = result
        return result

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    @staticmethod
    def _fingerprint(ctx: IncidentContext, mods: list) -> tuple:
        """Guards the (overlay_version, incident, graph) cache against unversioned input drift."""
        return (
            tuple(sorted(
                (r.restriction_id, r.active, len(r.edge_ids), r.geometry is not None)
                for r in ctx.overlays
            )),
            tuple(sorted(
                (n.notice_id, n.effective_at, n.expires_at, ctx.now > (n.expires_at or ctx.now)
                 + NOTICE_EXPIRY_GRACE) for n in ctx.notices
            )),
            tuple(sorted(
                (r.report_id, r.verification_status.value, r.expires_at > ctx.now)
                for r in ctx.reports
            )),
            tuple(id(m) for m in mods),
        )

    # --------------------------------------------------------------------------- compute
    def _compute(
        self, ctx: IncidentContext, graph: Graph, mods: list, overlay_version: int
    ) -> ResolvedRestrictions:
        if not graph.loaded:
            return ResolvedRestrictions({}, overlay_version, 0, True, ctx.destination is not None)
        col = _Collector(graph)

        # confirmed closures: Officially_Verified reports only (14.5)
        for r in ctx.reports:
            if _report_confirmed(r, ctx) and r.affected_edge_ids:
                col.add_edges(
                    r.affected_edge_ids,
                    RestrictionCause(kind="closure", id=r.report_id,
                                     source_record_id=r.source_record_id, simulated=r.simulated),
                )

        # overlays (demo Moderator-confirmed closures, impassable polygons) and module output
        module_items: list[Restriction] = []
        for m in mods:
            module_items.extend(m.hard_restrictions(ctx, graph))
        for res in [*ctx.overlays, *module_items]:
            if not res.active or res.incident_id != ctx.incident.incident_id:
                continue
            cause = res.cause()
            col.add_edges(res.edge_ids, cause)
            col.add_geometry(res.geometry, cause)

        # official notices
        for n in ctx.notices:
            if not _notice_active(n, ctx):
                continue
            if n.notice_type == "road_direction_restriction":
                # directed: only the listed (prohibited-direction) edges; reverse stays usable
                col.add_edges(
                    n.edge_ids,
                    RestrictionCause(kind="road_direction", id=n.notice_id,
                                     source_record_id=n.source_record_id,
                                     simulated=n.simulation_flag),
                )
            elif n.notice_type in _AREA_NOTICE_KINDS:
                cause = RestrictionCause(kind=_AREA_NOTICE_KINDS[n.notice_type], id=n.notice_id,
                                         source_record_id=n.source_record_id,
                                         simulated=n.simulation_flag)
                col.add_edges(n.edge_ids, cause)
                col.add_geometry(n.geometry, cause)

        rs = RestrictionSet({k: tuple(v) for k, v in col.blocked.items()}, overlay_version)
        mask = np.asarray(rs.mask(graph))
        iso_o = self._isolated(graph, mask, ctx.origin.latitude, ctx.origin.longitude, outgoing=True)
        iso_d = (
            self._isolated(graph, mask, ctx.destination.latitude, ctx.destination.longitude, outgoing=False)
            if ctx.destination is not None
            else False
        )
        return ResolvedRestrictions(rs.blocked_edges, overlay_version, len(col.blocked), iso_o, iso_d)

    @staticmethod
    def _isolated(graph: Graph, mask: np.ndarray, lat: float, lon: float, *, outgoing: bool) -> bool:
        """True when the point cannot be snapped, or its snapped node has no usable edge."""
        if graph.num_edges == 0:
            return True
        snap = graph.snap(lat, lon, mask)
        if snap is None:
            return True
        node = graph.node_index[snap.node_id]
        if outgoing:
            edges = graph.out_edges(node)
        else:
            edges = np.flatnonzero(graph.edge_to == node)
        return not bool(mask[edges].any()) if len(edges) else True

