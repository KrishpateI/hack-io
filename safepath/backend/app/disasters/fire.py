"""Fire and urban hazard module (Req 20).

Hard restrictions (20.1, 20.2): edges intersecting a *verified* fire perimeter (Officially_Verified
status or an official Source_Record), an official prohibited area, or a confirmed blocked street.
A perimeter with neither an Official_Source Source_Record nor Officially_Verified status is never a
hard restriction (20.6): its edges receive a penalty and the module emits a warning advisory.
Unverified/Corroborated/Disputed fire or smoke reports add a penalty to edges within
``radius_meters`` (20.3). Official fire instructions are surfaced as advisories with their
Source_Record; the Rules_Engine ranks them ahead of computed routes (20.4). Simulated perimeters
are labeled Simulated (20.5). The module never describes a route or location as "safe".
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

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
from app.models.domain import IncidentType, Restriction, UncertaintyLevel, VerificationStatus
from app.models.graph import Edge
from app.routing.graph import Graph

DEFAULT_WEIGHTS = {"w_smoke_report": 90.0, "radius_meters": 100.0}
SMOKE_KEY = "smoke_report"
PERIMETER_KEY = "unverified_perimeter"

_FIRE_WORDS = ("fire", "smoke", "flame", "burning")
_REPORT_STATUSES = (
    VerificationStatus.UNVERIFIED,
    VerificationStatus.CORROBORATED,
    VerificationStatus.DISPUTED,
)
_INSTRUCTION_KINDS = (
    "follow_instructions", "transportation_rule", "shelter_in_place", "incident_destination",
)
_BLOCKED_STREET_CATEGORIES = ("road_closure", "blocked_street", "street_blocked", "closure")
_PERIMETER_CATEGORIES = ("fire_perimeter", "perimeter")


@dataclass(frozen=True)
class FirePerimeter:
    """A fire perimeter polygon (GeoJSON). Verified = Officially_Verified or an official Source_Record."""

    perimeter_id: str
    geometry: Mapping[str, Any]
    verification_status: str = "Unverified"
    source_record_id: str | None = None
    simulated: bool = False

    @property
    def verified(self) -> bool:
        return bool(self.source_record_id) or self.verification_status == "Officially_Verified"


def is_fire_report(category: str) -> bool:
    c = category.lower()
    return any(w in c for w in _FIRE_WORDS) and not any(p in c for p in _PERIMETER_CATEGORIES)


@dataclass
class _Plan:
    smoke: set[str] = field(default_factory=set)
    unverified_perimeter: set[str] = field(default_factory=set)


class FireModule:
    incident_type = IncidentType.FIRE

    def __init__(
        self,
        perimeters: Iterable[FirePerimeter] = (),
        weights: Mapping[str, float] | None = None,
    ) -> None:
        self.perimeters: tuple[FirePerimeter, ...] = tuple(perimeters)
        w = {**DEFAULT_WEIGHTS, **(dict(weights) if weights is not None else weight_section("fire", DEFAULT_WEIGHTS))}
        self.w_smoke_report = float(w["w_smoke_report"])
        self.radius_meters = float(w["radius_meters"])
        self._cache = SingleSlotCache()

    # ------------------------------------------------------------------------ protocol
    def policy_override(self, ctx: IncidentContext) -> PolicyOverride | None:
        return None

    def route_filter(self, edge: Edge, ctx: IncidentContext) -> bool:
        return True

    def uncertainty_floor(self, ctx: IncidentContext) -> UncertaintyLevel | None:
        return None

    def hard_restrictions(self, ctx: IncidentContext, graph: Graph) -> Iterable[Restriction]:
        if not is_active(ctx):
            return []
        inc = ctx.incident.incident_id
        out: list[Restriction] = []
        for p in self.perimeters:
            if p.verified:
                out.append(Restriction(
                    restriction_id=p.perimeter_id, incident_id=inc, kind="prohibited_area",
                    geometry=dict(p.geometry), source_record_id=p.source_record_id,
                    simulated=p.simulated or ctx.incident.simulation_flag,
                ))
        for n in ctx.notices:  # official prohibited areas and blocked streets
            if notice_in_force(n, ctx) and n.notice_type in ("temporary_perimeter", "road_closure"):
                out.append(Restriction(
                    restriction_id=n.notice_id, incident_id=inc,
                    kind="prohibited_area" if n.notice_type == "temporary_perimeter" else "closure",
                    edge_ids=list(n.edge_ids), geometry=dict(n.geometry) if n.geometry else None,
                    source_record_id=n.source_record_id, simulated=n.simulation_flag,
                ))
        for r in ctx.reports:  # confirmed blocked streets
            if report_confirmed(r, ctx) and r.affected_edge_ids and (
                r.category.lower() in _BLOCKED_STREET_CATEGORIES
            ):
                out.append(Restriction(
                    restriction_id=r.report_id, incident_id=inc, kind="closure",
                    edge_ids=list(r.affected_edge_ids), source_report_id=r.report_id,
                    source_record_id=r.source_record_id, simulated=r.simulated,
                ))
        return out

    # --------------------------------------------------------------------- penalties
    def unverified_perimeters(self, ctx: IncidentContext) -> list[tuple[str, bool]]:
        """(id, simulated) of perimeters lacking an Official_Source and Officially_Verified status."""
        out = [
            (p.perimeter_id, p.simulated or ctx.incident.simulation_flag)
            for p in self.perimeters if not p.verified
        ]
        for r in ctx.reports:
            if (
                report_live(r, ctx) and r.category.lower() in _PERIMETER_CATEGORIES
                and r.verification_status != VerificationStatus.OFFICIALLY_VERIFIED
                and not r.source_record_id
            ):
                out.append((r.report_id, r.simulated))
        return out

    def _plan(self, ctx: IncidentContext, graph: Graph) -> _Plan:
        def build() -> _Plan:
            plan = _Plan()
            if not is_active(ctx):
                return plan
            for r in ctx.reports:
                if not report_live(r, ctx):
                    continue
                if r.verification_status in _REPORT_STATUSES and is_fire_report(r.category):
                    plan.smoke |= edges_within(
                        graph, project_point(r.latitude, r.longitude), self.radius_meters
                    )
                    plan.smoke |= set(r.affected_edge_ids)
                elif (
                    r.category.lower() in _PERIMETER_CATEGORIES
                    and r.verification_status != VerificationStatus.OFFICIALLY_VERIFIED
                    and not r.source_record_id
                ):
                    plan.unverified_perimeter |= set(r.affected_edge_ids)
            for p in self.perimeters:
                if not p.verified:
                    g = project(p.geometry)
                    if g is not None:
                        plan.unverified_perimeter |= edges_within(graph, g, 0.0)
            return plan

        return self._cache.get(graph, ctx, build)

    def edge_penalty(self, edge: Edge, ctx: IncidentContext, graph: Graph) -> PenaltyParts:
        plan = self._plan(ctx, graph)
        hazard: dict[str, float] = {}
        if edge.edge_id in plan.smoke:
            hazard[SMOKE_KEY] = self.w_smoke_report
        if edge.edge_id in plan.unverified_perimeter:
            hazard[PERIMETER_KEY] = self.w_smoke_report  # penalty and warning only (20.6)
        return PenaltyParts(hazard=hazard)

    # --------------------------------------------------------------------- advisories
    def advisories(self, ctx: IncidentContext) -> list[Advisory]:
        if not is_active(ctx):
            return []
        out: list[Advisory] = []
        for n in ctx.notices:
            if notice_in_force(n, ctx) and n.notice_type in _INSTRUCTION_KINDS:
                out.append(Advisory(
                    f"Official fire instruction: {n.text} (Source: {n.source_organization})",
                    n.source_record_id, n.simulation_flag,
                ))
        for pid, sim in self.unverified_perimeters(ctx):
            label = "Simulated " if sim else ""
            out.append(Advisory(
                f"Warning: {label}fire perimeter {pid} has no official source and is not "
                "verified. It is treated as a penalty only, not as a closed area.",
                None, sim,
            ))
        for p in self.perimeters:
            if p.verified and (p.simulated or ctx.incident.simulation_flag):
                out.append(Advisory(
                    f"Simulated fire perimeter {p.perimeter_id} is avoided in route computation.",
                    p.source_record_id, True,
                ))
        for g in official_guidance("fire"):
            out.append(Advisory(guidance_text(g), g.get("source_id")))
        return out


MODULE = register_module(FireModule(), replace=True)
