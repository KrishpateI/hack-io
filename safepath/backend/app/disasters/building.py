"""Building emergency module (Req 21) for the fictional SIMULATED indoor building.

Hard restrictions (21.4): every edge touching a stairwell (its Level_Transition edges and its
corridor landing links) whose closure is *confirmed* by a demo Moderator (an active overlay /
closure restriction naming indoor edges) or an Authorized_Official (an in-force official notice,
or an Officially_Verified report, naming indoor edges). Reported-blocked stairwells that are not
confirmed (Unverified/Corroborated/Disputed reports) are never hard restrictions (21.3): the
stairwell's transition edges carry ``w_reported_blocked_stair`` and the module emits a warning.
When every stairwell from a floor to the exits is removed, the router finds no path and the
pipeline yields NO_VERIFIED_ROUTE_AVAILABLE (21.7). Stairwells and exits are only ever described
as reported conditions, never as guaranteed (21.5).
"""
from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.disasters._common import (
    SingleSlotCache,
    is_active,
    notice_in_force,
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
from app.geo import haversine
from app.models.domain import IncidentType, Restriction, UncertaintyLevel, VerificationStatus
from app.models.graph import Edge
from app.routing.graph import Graph

DEFAULT_WEIGHTS = {"w_reported_blocked_stair": 100.0}
BLOCKED_STAIR_KEY = "reported_blocked_stairwell"
#: A report without indoor edge ids is attributed to a stairwell within this distance.
STAIR_MATCH_METERS = 15.0

_STAIR_NODE = re.compile(r"stair-([^-]+)$")
_UNCONFIRMED = (
    VerificationStatus.UNVERIFIED,
    VerificationStatus.CORROBORATED,
    VerificationStatus.DISPUTED,
)
_CLOSURE_KINDS = ("stairwell_closure", "closure")
_BLOCK_WORDS = ("block", "inaccessible", "closed", "closure", "impassable")


def is_stairwell_report(category: str) -> bool:
    """A report about a stairwell being blocked or inaccessible."""
    c = category.lower()
    return "stair" in c and any(w in c for w in _BLOCK_WORDS)


@dataclass(frozen=True)
class StairIndex:
    """Stairwell structure of one runtime graph."""

    edges: Mapping[str, tuple[str, ...]]  # stair id -> all edge ids touching it
    transitions: Mapping[str, tuple[str, ...]]  # stair id -> Level_Transition edge ids
    edge_stair: Mapping[str, str]  # edge id -> stair id
    positions: Mapping[str, tuple[float, float]]  # stair id -> (lat, lon)


def build_stair_index(graph: Graph) -> StairIndex:
    if not graph.loaded or graph.model is None:
        return StairIndex({}, {}, {}, {})
    stair_of: dict[str, str] = {}
    pos: dict[str, tuple[float, float]] = {}
    for n in graph.model.nodes:
        if n.kind != "indoor":
            continue
        m = _STAIR_NODE.search(n.node_id)
        if m:
            stair_of[n.node_id] = m.group(1)
            pos.setdefault(m.group(1), (n.lat, n.lon))
    edges: dict[str, list[str]] = {}
    trans: dict[str, list[str]] = {}
    edge_stair: dict[str, str] = {}
    for e in graph.model.edges:
        sid = stair_of.get(e.from_node) or stair_of.get(e.to_node)
        if sid is None:
            continue
        edges.setdefault(sid, []).append(e.edge_id)
        edge_stair[e.edge_id] = sid
        if e.transition_type == "stairwell":
            trans.setdefault(sid, []).append(e.edge_id)
    return StairIndex(
        {k: tuple(v) for k, v in edges.items()}, {k: tuple(v) for k, v in trans.items()},
        edge_stair, pos,
    )


@dataclass
class _Plan:
    closed: set[str] = field(default_factory=set)  # confirmed-closed stairwell ids
    reported: set[str] = field(default_factory=set)  # reported-blocked, unconfirmed
    reported_status: dict[str, set[str]] = field(default_factory=dict)
    sim_closed: dict[str, bool] = field(default_factory=dict)


class BuildingEmergencyModule:
    incident_type = IncidentType.BUILDING

    def __init__(self, weights: Mapping[str, float] | None = None) -> None:
        w = {**DEFAULT_WEIGHTS, **(dict(weights) if weights is not None
                                   else weight_section("building", DEFAULT_WEIGHTS))}
        self.w_reported_blocked_stair = float(w["w_reported_blocked_stair"])
        self._cache = SingleSlotCache()
        self._index_lock = threading.Lock()
        self._index: tuple[Graph, StairIndex] | None = None

    # ------------------------------------------------------------------------- index
    def stair_index(self, graph: Graph) -> StairIndex:
        with self._index_lock:
            if self._index is not None and self._index[0] is graph:
                return self._index[1]
        idx = build_stair_index(graph)
        with self._index_lock:
            self._index = (graph, idx)
        return idx

    @staticmethod
    def _stairs_for(idx: StairIndex, edge_ids: Iterable[str]) -> set[str]:
        return {idx.edge_stair[e] for e in edge_ids if e in idx.edge_stair}

    @staticmethod
    def _stairs_near(idx: StairIndex, lat: float, lon: float) -> set[str]:
        return {
            s for s, (la, lo) in idx.positions.items()
            if haversine(lat, lon, la, lo) <= STAIR_MATCH_METERS
        }

    def _report_stairs(self, idx: StairIndex, r) -> set[str]:
        found = self._stairs_for(idx, r.affected_edge_ids)
        return found or self._stairs_near(idx, r.latitude, r.longitude)

    # ------------------------------------------------------------------------- plan
    def _confirmed(self, ctx: IncidentContext, idx: StairIndex) -> list[tuple[set[str], Restriction]]:
        """(stairwell ids, restriction) for every confirmed closure source."""
        inc = ctx.incident.incident_id
        out: list[tuple[set[str], Restriction]] = []
        for o in ctx.overlays:  # demo Moderator-confirmed closures
            if not o.active or o.incident_id != inc or o.kind not in _CLOSURE_KINDS:
                continue
            stairs = self._stairs_for(idx, o.edge_ids)
            if stairs:
                out.append((stairs, o))
        for n in ctx.notices:  # Authorized_Official notices
            if notice_in_force(n, ctx) and n.notice_type == "road_closure":
                stairs = self._stairs_for(idx, n.edge_ids)
                if stairs:
                    out.append((stairs, Restriction(
                        restriction_id=n.notice_id, incident_id=inc, kind="stairwell_closure",
                        source_record_id=n.source_record_id, simulated=n.simulation_flag,
                    )))
        for r in ctx.reports:  # Officially_Verified reports
            if report_confirmed(r, ctx) and is_stairwell_report(r.category):
                stairs = self._report_stairs(idx, r)
                if stairs:
                    out.append((stairs, Restriction(
                        restriction_id=r.report_id, incident_id=inc, kind="stairwell_closure",
                        source_report_id=r.report_id, source_record_id=r.source_record_id,
                        simulated=r.simulated,
                    )))
        return out

    def _plan(self, ctx: IncidentContext, graph: Graph) -> _Plan:
        def build() -> _Plan:
            plan = _Plan()
            if not is_active(ctx):
                return plan
            idx = self.stair_index(graph)
            for stairs, res in self._confirmed(ctx, idx):
                for s in stairs:
                    plan.closed.add(s)
                    plan.sim_closed[s] = plan.sim_closed.get(s, False) or res.simulated
            for r in ctx.reports:
                if (report_live(r, ctx) and r.verification_status in _UNCONFIRMED
                        and is_stairwell_report(r.category)):
                    for s in self._report_stairs(idx, r):
                        plan.reported.add(s)
                        plan.reported_status.setdefault(s, set()).add(r.verification_status.value)
            plan.reported -= plan.closed  # a confirmed closure supersedes the report
            return plan

        return self._cache.get(graph, ctx, build)

    # ----------------------------------------------------------------------- protocol
    def policy_override(self, ctx: IncidentContext) -> PolicyOverride | None:
        return None

    def route_filter(self, edge: Edge, ctx: IncidentContext) -> bool:
        return True

    def uncertainty_floor(self, ctx: IncidentContext) -> UncertaintyLevel | None:
        return None

    def hard_restrictions(self, ctx: IncidentContext, graph: Graph) -> Iterable[Restriction]:
        if not is_active(ctx):
            return []
        idx = self.stair_index(graph)
        out: list[Restriction] = []
        for stairs, res in self._confirmed(ctx, idx):
            edge_ids = sorted({e for s in stairs for e in idx.edges.get(s, ())})
            out.append(Restriction(
                restriction_id=res.restriction_id, incident_id=ctx.incident.incident_id,
                kind="stairwell_closure", edge_ids=edge_ids,
                source_report_id=res.source_report_id, source_record_id=res.source_record_id,
                simulated=res.simulated or ctx.incident.simulation_flag,
            ))
        return out

    def edge_penalty(self, edge: Edge, ctx: IncidentContext, graph: Graph) -> PenaltyParts:
        if edge.transition_type != "stairwell":
            return PenaltyParts()
        plan = self._plan(ctx, graph)
        if not plan.reported:
            return PenaltyParts()
        sid = self.stair_index(graph).edge_stair.get(edge.edge_id)
        if sid in plan.reported:
            return PenaltyParts(hazard={BLOCKED_STAIR_KEY: self.w_reported_blocked_stair})
        return PenaltyParts()

    def advisories(self, ctx: IncidentContext) -> list[Advisory]:
        if not is_active(ctx):
            return []
        sim = ctx.incident.simulation_flag
        out = [Advisory(
            "Stairwell and exit conditions shown are community reports and are unverified unless "
            "marked otherwise. Conditions can change at any time. Follow official instructions "
            "from building staff and emergency responders.", None, sim,
        )]
        for r in ctx.reports:
            if report_live(r, ctx) and r.verification_status in _UNCONFIRMED and is_stairwell_report(r.category):
                out.append(Advisory(
                    f"Warning: a stairwell was reported blocked or inaccessible "
                    f"(report {r.report_id}, status {r.verification_status.value}). This is a "
                    "reported condition, not a confirmed closure. Routes through it are penalized, "
                    "not prohibited.", r.source_record_id, r.simulated,
                ))
        return out

    # --------------------------------------------------------------- display (21.3)
    def stairwell_conditions(self, ctx: IncidentContext, graph: Graph) -> list[dict[str, Any]]:
        """Reported/confirmed stairwell conditions with Verification_Status, for display."""
        idx = self.stair_index(graph)
        plan = self._plan(ctx, graph)
        out: list[dict[str, Any]] = []
        for s in sorted(plan.closed):
            out.append({"stairwell_id": s, "condition": "closure_confirmed",
                        "verification_status": VerificationStatus.OFFICIALLY_VERIFIED.value})
        for r in ctx.reports:
            if report_live(r, ctx) and r.verification_status in _UNCONFIRMED and is_stairwell_report(r.category):
                for s in sorted(self._report_stairs(idx, r)):
                    out.append({"stairwell_id": s, "condition": "reported_blocked",
                                "verification_status": r.verification_status.value,
                                "report_id": r.report_id})
        return out


MODULE = register_module(BuildingEmergencyModule(), replace=True)
