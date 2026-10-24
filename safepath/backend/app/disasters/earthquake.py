"""Earthquake module (Req 18).

Hard restrictions: confirmed bridge closures, collapse exclusion zones, debris zones and the
edges named by an active damage notice (18.1, 18.2). Overlay polygons are only the zones that the
caller marks as impassable/prohibited; they are never inferred from building data.

Penalty: a high-rise proximity penalty applies only to edges within ``proximity_meters`` of a
building that is *associated* with an active debris zone, an Official_Notice or a Hazard_Report
(18.3). ``Building_Height_Class`` is carried for display but is never an input to any penalty
decision (18.4). Confirmed structural information from an Official_Source ("intact") replaces any
height-based inference, so such a building carries no penalty (18.5). The module never describes a
route or place as "safe".
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry.base import BaseGeometry

from app.disasters._common import (
    SingleSlotCache,
    edges_within,
    guidance_text,
    is_active,
    notice_in_force,
    official_guidance,
    project,
    project_point,
    report_confirmed,
    report_live,
    weight_section,
)
from app.disasters.base import (
    Advisory,
    IncidentContext,
    PenaltyParts,
    PolicyOverride,
    register_module,
)
from app.models.domain import IncidentType, Restriction, UncertaintyLevel
from app.models.graph import Edge
from app.routing.graph import Graph

DEFAULT_WEIGHTS = {"w_highrise_proximity": 60.0, "proximity_meters": 50.0}
PENALTY_KEY = "highrise_proximity"

# Zone kinds that represent debris / collapse exclusion / confirmed closures.
_ZONE_KINDS = ("impassable_polygon", "prohibited_area")
_CLOSURE_OVERLAY_KINDS = ("closure", *_ZONE_KINDS)
# Official structural statuses: "intact" removes the building from consideration.
INTACT_STATUSES = frozenset({"intact", "no_damage", "structurally_sound"})
REPORT_ASSOCIATION_METERS = 10.0
_DAMAGE_NOTICE_KINDS = ("road_closure", "temporary_perimeter")


@dataclass(frozen=True)
class Building:
    """One entry of the building index.

    ``height_class`` is the Building_Height_Class (Low-rise/Mid-rise/High-rise/Unknown). It is
    informational only. ``structural_status`` with ``structural_source_record_id`` is confirmed
    structural information from an Official_Source.
    """

    building_id: str
    footprint: Mapping[str, Any]  # GeoJSON (lon/lat) polygon or point
    height_class: str = "Unknown"
    structural_status: str | None = None
    structural_source_record_id: str | None = None

    @property
    def has_confirmed_structural_info(self) -> bool:
        return bool(self.structural_status and self.structural_source_record_id)

    @property
    def confirmed_intact(self) -> bool:
        return (
            self.has_confirmed_structural_info
            and str(self.structural_status).lower() in INTACT_STATUSES
        )


@dataclass
class _Plan:
    """Per-(graph, context) computation: edge id -> building ids that penalize it."""

    penalized: dict[str, list[str]] = field(default_factory=dict)


class EarthquakeModule:
    incident_type = IncidentType.EARTHQUAKE

    def __init__(
        self,
        buildings: Iterable[Building] = (),
        weights: Mapping[str, float] | None = None,
    ) -> None:
        self.buildings: tuple[Building, ...] = tuple(buildings)
        w = {**DEFAULT_WEIGHTS, **(dict(weights) if weights is not None else weight_section("earthquake", DEFAULT_WEIGHTS))}
        self.w_highrise_proximity = float(w["w_highrise_proximity"])
        self.proximity_meters = float(w["proximity_meters"])
        self._cache = SingleSlotCache()

    # ------------------------------------------------------------------ policy / filter
    def policy_override(self, ctx: IncidentContext) -> PolicyOverride | None:
        return None

    def route_filter(self, edge: Edge, ctx: IncidentContext) -> bool:
        return True

    def uncertainty_floor(self, ctx: IncidentContext) -> UncertaintyLevel | None:
        return None

    # ----------------------------------------------------------------- hard restrictions
    def hard_restrictions(self, ctx: IncidentContext, graph: Graph) -> Iterable[Restriction]:
        if not is_active(ctx):
            return []
        inc = ctx.incident.incident_id
        out: list[Restriction] = []
        # confirmed closures, collapse exclusion zones and debris zones (overlays)
        for o in ctx.overlays:
            if o.active and o.incident_id == inc and o.kind in _CLOSURE_OVERLAY_KINDS:
                out.append(o)
        # confirmed (Officially_Verified) closures, incl. bridges, from reports
        for r in ctx.reports:
            if report_confirmed(r, ctx) and r.affected_edge_ids:
                out.append(Restriction(
                    restriction_id=r.report_id, incident_id=inc, kind="closure",
                    edge_ids=list(r.affected_edge_ids), source_report_id=r.report_id,
                    source_record_id=r.source_record_id, simulated=r.simulated,
                ))
        # edges identified by an active damage notice
        for n in ctx.notices:
            if not notice_in_force(n, ctx) or n.notice_type not in _DAMAGE_NOTICE_KINDS:
                continue
            if not n.edge_ids and not n.geometry:
                continue
            out.append(Restriction(
                restriction_id=n.notice_id, incident_id=inc,
                kind="prohibited_area" if n.notice_type == "temporary_perimeter" else "closure",
                edge_ids=list(n.edge_ids), geometry=dict(n.geometry) if n.geometry else None,
                source_record_id=n.source_record_id, simulated=n.simulation_flag,
            ))
        return out

    # ---------------------------------------------------------------------- penalties
    def associated_buildings(self, ctx: IncidentContext) -> list[Building]:
        """Buildings tied to an active debris zone, notice or report (never by height class)."""
        zones = [
            g for o in ctx.overlays
            if o.active and o.incident_id == ctx.incident.incident_id and o.kind in _ZONE_KINDS
            and (g := project(o.geometry)) is not None
        ]
        notice_geoms = [
            g for n in ctx.notices
            if notice_in_force(n, ctx) and (g := project(n.geometry)) is not None
        ]
        report_pts = [
            project_point(r.latitude, r.longitude) for r in ctx.reports if report_live(r, ctx)
        ]
        found: list[Building] = []
        for b in self.buildings:
            if b.confirmed_intact:
                continue  # official structural information replaces heuristics (18.5)
            fp = project(b.footprint)
            if fp is None:
                continue
            if (
                any(z.intersects(fp) for z in zones)
                or any(g.intersects(fp) for g in notice_geoms)
                or any(fp.distance(p) <= REPORT_ASSOCIATION_METERS for p in report_pts)
            ):
                found.append(b)
        return found

    def _plan(self, ctx: IncidentContext, graph: Graph) -> _Plan:
        def build() -> _Plan:
            plan = _Plan()
            if not is_active(ctx):
                return plan
            for b in self.associated_buildings(ctx):
                fp: BaseGeometry | None = project(b.footprint)
                for eid in edges_within(graph, fp, self.proximity_meters):  # type: ignore[arg-type]
                    plan.penalized.setdefault(eid, []).append(b.building_id)
            return plan

        return self._cache.get(graph, ctx, build)

    def edge_penalty(self, edge: Edge, ctx: IncidentContext, graph: Graph) -> PenaltyParts:
        if edge.edge_id in self._plan(ctx, graph).penalized:
            return PenaltyParts(hazard={PENALTY_KEY: self.w_highrise_proximity})
        return PenaltyParts()

    def penalized_buildings(self, edge: Edge, ctx: IncidentContext, graph: Graph) -> list[str]:
        return list(self._plan(ctx, graph).penalized.get(edge.edge_id, []))

    # ---------------------------------------------------------------------- advisories
    def advisories(self, ctx: IncidentContext) -> list[Advisory]:
        out: list[Advisory] = []
        if not is_active(ctx):
            return out
        for n in ctx.notices:
            if notice_in_force(n, ctx):
                out.append(Advisory(
                    f"Official notice: {n.text} (Source: {n.source_organization})",
                    n.source_record_id, n.simulation_flag,
                ))
        for o in ctx.overlays:
            if o.active and o.incident_id == ctx.incident.incident_id and o.kind in _ZONE_KINDS:
                sim = bool(o.simulated or ctx.incident.simulation_flag)
                prefix = "Simulated " if sim else ""
                out.append(Advisory(
                    f"{prefix}debris or exclusion zone {o.restriction_id} is avoided in route "
                    "computation.",
                    o.source_record_id, sim,
                ))
        for g in official_guidance("earthquake"):
            out.append(Advisory(guidance_text(g), g.get("source_id")))
        return out


def create_module(**kw: Any) -> EarthquakeModule:
    return EarthquakeModule(**kw)


MODULE = register_module(EarthquakeModule(), replace=True)
