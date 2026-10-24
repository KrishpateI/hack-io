"""Tornado module (Req 19).

While a tornado warning incident is active and the origin lies inside the incident boundary,
``policy_override`` returns SHELTER_IN_PLACE (19.1). The response names the official shelter and
its Source_Record when a verified one exists inside the boundary (19.3); otherwise it states that no
verified shelter is available and gives official tornado guidance (19.4). ``route_filter`` removes
outdoor edges while the warning is active (19.5), so an outdoor route is never produced; once the
incident status leaves "active" it allows every edge again (19.7). The module never describes any
street or location as "safe".
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from shapely.geometry import Point, shape

from app.disasters._common import (
    guidance_text,
    is_active,
    notice_in_force,
    official_guidance,
)
from app.disasters.base import (
    Advisory,
    IncidentContext,
    PenaltyParts,
    PolicyOverride,
    register_module,
)
from app.disasters.rules_engine import incident_boundary_is_live
from app.models.domain import IncidentType, OfficialNotice, PolicyOutcome, Restriction, UncertaintyLevel
from app.models.graph import Edge
from app.routing.graph import Graph

NO_SHELTER = "No verified shelter available."


@dataclass(frozen=True)
class Shelter:
    """A shelter record. It is *verified* only when it carries an official Source_Record."""

    shelter_id: str
    name: str
    latitude: float
    longitude: float
    source_record_id: str | None = None
    source_url: str | None = None


def _inside(boundary, lat: float, lon: float) -> bool:
    try:
        return bool(shape(boundary).covers(Point(lon, lat)))
    except Exception:
        return False


def _is_outdoor(edge: Edge) -> bool:
    if edge.edge_kind == "street":
        return True
    return edge.edge_kind == "level_transition" and edge.transition_type == "exit"


class TornadoModule:
    incident_type = IncidentType.TORNADO

    def __init__(self, shelters: Iterable[Shelter] = ()) -> None:
        self.shelters: tuple[Shelter, ...] = tuple(shelters)

    # ------------------------------------------------------------------------ shelters
    def verified_shelters(self, ctx: IncidentContext) -> list[tuple[str, str, str]]:
        """(name/text, source_record_id, notice_or_shelter_id) of verified shelters in bounds."""
        boundary = ctx.incident.geographic_boundary
        found: list[tuple[str, str, str]] = []
        for s in self.shelters:
            if s.source_record_id and (
                not boundary or _inside(boundary, s.latitude, s.longitude)
            ):
                found.append((f"Shelter: {s.name}", s.source_record_id, s.shelter_id))
        for n in ctx.notices:
            if self._is_shelter_notice(n, ctx):
                found.append((n.text, n.source_record_id, n.notice_id))
        return found

    @staticmethod
    def _is_shelter_notice(n: OfficialNotice, ctx: IncidentContext) -> bool:
        if not notice_in_force(n, ctx):
            return False
        if n.notice_type != "shelter_in_place" and not (
            n.notice_type == "incident_destination" and "shelter" in n.text.lower()
        ):
            return False
        boundary = ctx.incident.geographic_boundary
        if n.geometry and boundary and incident_boundary_is_live(ctx.incident):
            try:
                return bool(shape(boundary).intersects(shape(n.geometry)))
            except Exception:
                return False
        return True

    def _guidance(self) -> tuple[str, str | None]:
        gs = official_guidance("tornado")
        if not gs:
            return "Follow official tornado guidance from local emergency officials.", None
        return guidance_text(gs[0]), gs[0].get("source_id")

    # ----------------------------------------------------------------------- protocol
    def policy_override(self, ctx: IncidentContext) -> PolicyOverride | None:
        if not is_active(ctx):
            return None
        boundary = ctx.incident.geographic_boundary
        if not boundary or not incident_boundary_is_live(ctx.incident):
            return None
        if not _inside(boundary, ctx.origin.latitude, ctx.origin.longitude):
            return None
        shelters = self.verified_shelters(ctx)
        if shelters:
            text, src, _ = shelters[0]
            notice_ids = tuple(i for _, _, i in shelters if any(n.notice_id == i for n in ctx.notices))
            return PolicyOverride(
                PolicyOutcome.SHELTER_IN_PLACE,
                f"Shelter in place. Official shelter: {text} (Source_Record: {src}).",
                src,
                notice_ids,
            )
        text, src = self._guidance()
        return PolicyOverride(
            PolicyOutcome.SHELTER_IN_PLACE, f"Shelter in place. {NO_SHELTER} {text}", src
        )

    def route_filter(self, edge: Edge, ctx: IncidentContext) -> bool:
        if not is_active(ctx):
            return True  # outdoor routing resumes after the warning ends (19.7)
        return not _is_outdoor(edge)

    def hard_restrictions(self, ctx: IncidentContext, graph: Graph) -> Iterable[Restriction]:
        return []

    def edge_penalty(self, edge: Edge, ctx: IncidentContext, graph: Graph) -> PenaltyParts:
        return PenaltyParts()

    def uncertainty_floor(self, ctx: IncidentContext) -> UncertaintyLevel | None:
        return None

    def advisories(self, ctx: IncidentContext) -> list[Advisory]:
        if not is_active(ctx):
            return []
        out: list[Advisory] = []
        shelters = self.verified_shelters(ctx)
        for text, src, _ in shelters:
            out.append(Advisory(f"Official tornado shelter instruction: {text}", src, ctx.incident.simulation_flag))
        if not shelters:
            out.append(Advisory(NO_SHELTER))
        text, src = self._guidance()
        out.append(Advisory(text, src))
        return out


MODULE = register_module(TornadoModule(), replace=True)
