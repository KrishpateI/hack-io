"""Route explanation and uncertainty level (Req 15.1-15.3, 15.6, 1.7, 17.7).

Pure functions that turn the routing pipeline's per-edge ``PenaltyComponents`` and the resolved
restrictions into the explanation fields on a ``Route``. Wording rules: routes are described as
algorithm-generated, never as guaranteed or endorsed by any authority; flood exposure is
described only as "lower modeled exposure"; simulated hazards are labeled SIMULATED; community
reports are unverified warnings, never closures.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.disasters.base import Advisory, RestrictionSet
from app.models.domain import IncidentType, RestrictionCause, UncertaintyLevel
from app.models.routing import ALGORITHM_LABEL, EdgePenalty, RestrictionConsidered, Route
from app.routing.cost import PenaltyComponents, Weights

SIMULATED_TAG = "SIMULATED"

_LEVEL_ORDER = {
    UncertaintyLevel.LOW: 0,
    UncertaintyLevel.MEDIUM: 1,
    UncertaintyLevel.HIGH: 2,
}

_CAUSE_TEXT = {
    "closure": "confirmed closure",
    "impassable_polygon": "impassable area",
    "prohibited_area": "prohibited area",
    "stairwell_closure": "stairwell closure",
    "road_direction": "road direction restriction",
    "module": "disaster module restriction",
}

_BASE_LIMITATIONS = (
    "This route is algorithm-generated from available map and incident data and may be "
    "incomplete or out of date.",
    "Conditions can change quickly. Follow instructions from official responders.",
    "Attributes recorded as Unknown add a penalty; they are never treated as favorable.",
)
_FLOOD_LIMITATION = (
    "Flood routing reports lower modeled exposure only. It does not show where water is "
    "present and does not use depth measurements."
)
_REPORT_LIMITATION = (
    "Community reports are unverified warnings. They add a penalty but never close a street."
)
_SIMULATED_LIMITATION = "SIMULATED hazard data is part of this result and is not a real event."


# ------------------------------------------------------------------------ data classes
@dataclass(frozen=True)
class RouteCandidate:
    """One computed route as seen by the explainer."""

    route_id: str
    components: Sequence[PenaltyComponents]
    edge_lengths: Sequence[float]  # meters, aligned with ``components``

    @property
    def travel(self) -> float:
        return float(sum(c.travel for c in self.components))

    @property
    def hazard(self) -> float:
        return float(sum(c.hazard_total for c in self.components))

    @property
    def uncertainty(self) -> float:
        return float(sum(c.uncertainty_total for c in self.components))

    @property
    def total(self) -> float:
        return self.travel + self.hazard + self.uncertainty

    @property
    def length(self) -> float:
        return float(sum(self.edge_lengths))


@dataclass(frozen=True)
class RouteExplanation:
    """Explanation fields for one route; ``apply`` copies them onto a ``Route``."""

    restrictions_considered: list[RestrictionConsidered]
    excluded_edges_count: int
    edge_penalties: list[EdgePenalty]
    reason_summary: str
    data_timestamp: datetime | None
    sources: list[str]
    confidence_limitations: list[str]
    hazard_flags: list[str]
    uncertainty_level: UncertaintyLevel
    label: str = ALGORITHM_LABEL
    uncertain_fraction: float | None = None

    def apply(self, route: Route) -> Route:
        return route.model_copy(update={
            "restrictions_considered": self.restrictions_considered,
            "excluded_edges_count": self.excluded_edges_count,
            "edge_penalties": self.edge_penalties,
            "reason_summary": self.reason_summary,
            "sources": self.sources,
            "confidence_limitations": self.confidence_limitations,
            "hazard_flags": self.hazard_flags,
            "uncertainty_level": self.uncertainty_level,
            "label": self.label,
        })


@dataclass(frozen=True)
class RouteChange:
    """What changed between two route results after a new closure (dynamic update UI)."""

    changed: bool
    removed_edge_ids: list[str]
    causes: dict[str, list[RestrictionConsidered]]
    distance_delta_meters: float | None
    duration_delta_seconds: float | None
    text: str
    simulated: bool = False
    extra: dict[str, object] = field(default_factory=dict)


# ----------------------------------------------------------------------- uncertainty
def uncertain_fraction(candidate: RouteCandidate) -> float | None:
    """Share of route length on edges carrying any uncertainty penalty.

    None when there is no coverage information (no edges or zero total length).
    """
    total = candidate.length
    if not candidate.components or total <= 0:
        return None
    flagged = sum(
        length for c, length in zip(candidate.components, candidate.edge_lengths)
        if c.uncertainty_total > 0
    )
    return float(flagged) / total


def uncertainty_level(
    fraction: float | None,
    floor: UncertaintyLevel | None = None,
    *,
    medium_fraction: float = 0.05,
    high_fraction: float = 0.20,
) -> UncertaintyLevel:
    """Low below ``medium_fraction``, Medium below ``high_fraction``, High otherwise.

    Unknown when there is no coverage information. A module floor can raise the level but never
    lower it below the computed value.
    """
    if fraction is None:
        computed = UncertaintyLevel.UNKNOWN
    elif fraction < medium_fraction:
        computed = UncertaintyLevel.LOW
    elif fraction < high_fraction:
        computed = UncertaintyLevel.MEDIUM
    else:
        computed = UncertaintyLevel.HIGH
    if floor is None or floor is UncertaintyLevel.UNKNOWN:
        return computed
    if computed is UncertaintyLevel.UNKNOWN:
        # no coverage info: a module floor still applies, since it is the only evidence we have
        return floor
    return floor if _LEVEL_ORDER[floor] > _LEVEL_ORDER[computed] else computed


def level_for_candidate(
    candidate: RouteCandidate, weights: Weights, floor: UncertaintyLevel | None = None
) -> UncertaintyLevel:
    lv = weights.uncertainty_levels
    return uncertainty_level(
        uncertain_fraction(candidate),
        floor,
        medium_fraction=float(lv.get("medium_fraction", 0.05)),
        high_fraction=float(lv.get("high_fraction", 0.20)),
    )


# ----------------------------------------------------------------------------- helpers
def latest_data_timestamp(timestamps: Iterable[datetime | None]) -> datetime | None:
    """Latest data timestamp / overlay update applied; None when nothing was applied."""
    vals = [t for t in timestamps if t is not None]
    return max(vals) if vals else None


def restrictions_considered(restrictions: RestrictionSet) -> list[RestrictionConsidered]:
    """Distinct restriction causes (kind, id, source record, simulated), in stable order."""
    seen: dict[tuple, RestrictionConsidered] = {}
    for eid in sorted(restrictions.blocked_edges):
        for cause in restrictions.blocked_edges[eid]:
            key = (cause.kind, cause.id)
            if key not in seen:
                seen[key] = RestrictionConsidered.from_cause(cause)
    return list(seen.values())


def _fmt(x: float) -> str:
    return f"{x:.0f}"


def _delta_text(a: RouteCandidate, b: RouteCandidate) -> str:
    """Describe how ``a`` compares with ``b`` by cost component (positive = b costs more)."""
    parts = []
    for name, da in (
        ("travel", b.travel - a.travel),
        ("hazard", b.hazard - a.hazard),
        ("uncertainty", b.uncertainty - a.uncertainty),
    ):
        if abs(da) < 0.5:
            continue
        parts.append(f"{name} {'+' if da > 0 else '-'}{_fmt(abs(da))}")
    return ", ".join(parts) if parts else "no component difference"


def _dominant_reason(a: RouteCandidate, b: RouteCandidate) -> str:
    diffs = {
        "travel time": b.travel - a.travel,
        "hazard exposure": b.hazard - a.hazard,
        "data uncertainty": b.uncertainty - a.uncertainty,
    }
    name, val = max(diffs.items(), key=lambda kv: kv[1])
    if val < 0.5:
        return "similar cost on every component"
    return f"mainly lower {name}"


def reason_summary(index: int, candidates: Sequence[RouteCandidate]) -> str:
    """Why route ``index`` was produced, relative to the other routes."""
    me = candidates[index]
    if not me.components:
        return "No route was produced."
    head = (
        f"Algorithm-generated route with modeled cost {_fmt(me.total)} "
        f"(travel {_fmt(me.travel)}, hazard {_fmt(me.hazard)}, uncertainty {_fmt(me.uncertainty)})."
    )
    others = [(i, c) for i, c in enumerate(candidates) if i != index and c.components]
    if not others:
        return head + " No other options were available to compare."
    lines = []
    for i, other in others:
        label = other.route_id or f"option {i + 1}"
        if me.total <= other.total + 1e-9:
            lines.append(
                f"Chosen over {label}: modeled cost {_fmt(other.total - me.total)} lower, "
                f"{_dominant_reason(me, other)} ({_delta_text(me, other)} for {label})."
            )
        else:
            lines.append(
                f"Higher modeled cost than {label} by {_fmt(me.total - other.total)}; "
                f"offered as a distinct alternative ({_delta_text(other, me)} for this route)."
            )
    return " ".join([head, *lines])


def _limitations(
    *,
    incident_type: IncidentType | str | None,
    simulated: bool,
    has_report_penalty: bool,
    advisories: Iterable[Advisory],
    level: UncertaintyLevel,
) -> list[str]:
    out = list(_BASE_LIMITATIONS)
    if str(getattr(incident_type, "value", incident_type)) == IncidentType.FLOOD.value:
        out.append(_FLOOD_LIMITATION)
    if has_report_penalty:
        out.append(_REPORT_LIMITATION)
    if simulated:
        out.append(_SIMULATED_LIMITATION)
    if level in (UncertaintyLevel.HIGH, UncertaintyLevel.UNKNOWN):
        out.append(f"Data coverage for this route is limited (uncertainty level {level.value}).")
    for adv in advisories:
        out.append(f"{SIMULATED_TAG}: {adv.text}" if adv.simulated else adv.text)
    return out


# ------------------------------------------------------------------------- main entry
def explain_route(
    index: int,
    candidates: Sequence[RouteCandidate],
    restrictions: RestrictionSet,
    weights: Weights,
    *,
    excluded_edges_count: int | None = None,
    floor: UncertaintyLevel | None = None,
    incident_type: IncidentType | str | None = None,
    data_timestamps: Iterable[datetime | None] = (),
    source_record_ids: Iterable[str | None] = (),
    advisories: Iterable[Advisory] = (),
    simulated: bool = False,
) -> RouteExplanation:
    """Build the explanation for ``candidates[index]``.

    ``floor`` is the module's ``uncertainty_floor``. ``source_record_ids`` are the sources of
    data applied; restriction sources are added automatically.
    """
    me = candidates[index]
    considered = restrictions_considered(restrictions)
    sim = simulated or any(r.simulated for r in considered)

    sources: list[str] = []
    for sid in [*(r.source_record_id for r in considered), *source_record_ids]:
        if sid and sid not in sources:
            sources.append(sid)

    flags: list[str] = []
    for c in me.components:
        for f in c.hazard_flags:
            if f not in flags:
                flags.append(f)

    level = level_for_candidate(me, weights, floor)
    excluded = (
        len(restrictions.blocked_edges) if excluded_edges_count is None else excluded_edges_count
    )
    has_report = any(f.startswith("report_") for f in flags)
    return RouteExplanation(
        restrictions_considered=considered,
        excluded_edges_count=excluded,
        edge_penalties=[c.to_edge_penalty() for c in me.components],
        reason_summary=(f"{SIMULATED_TAG} hazards included. " if sim else "")
        + reason_summary(index, candidates),
        data_timestamp=latest_data_timestamp(data_timestamps),
        sources=sources,
        confidence_limitations=_limitations(
            incident_type=incident_type,
            simulated=sim,
            has_report_penalty=has_report,
            advisories=advisories,
            level=level,
        ),
        hazard_flags=flags,
        uncertainty_level=level,
        uncertain_fraction=uncertain_fraction(me),
    )


# ---------------------------------------------------------------------- explain change
def _describe_cause(cause: RestrictionConsidered | RestrictionCause) -> str:
    text = _CAUSE_TEXT.get(cause.kind, cause.kind)
    if cause.simulated:
        text = f"{SIMULATED_TAG} {text}"
    return f"{text} {cause.id}"


def explain_change(
    before: Route | None,
    after: Route | None,
    before_restrictions: RestrictionSet,
    after_restrictions: RestrictionSet,
) -> RouteChange:
    """Describe what changed between two route results after a new closure.

    Removed edges are the edges of the previous route that are newly blocked in
    ``after_restrictions``; each is reported with its cause.
    """
    new_blocked = {
        eid: after_restrictions.causes(eid)
        for eid in after_restrictions.blocked_edges
        if not before_restrictions.is_blocked(eid)
        or set(after_restrictions.causes(eid)) - set(before_restrictions.causes(eid))
    }
    before_edges = [p.edge_id for p in before.edge_penalties] if before else []
    removed = [e for e in before_edges if e in new_blocked and not before_restrictions.is_blocked(e)]
    causes = {e: [RestrictionConsidered.from_cause(c) for c in new_blocked[e]] for e in removed}
    simulated = any(c.simulated for cs in causes.values() for c in cs)

    before_ids = before_edges
    after_ids = [p.edge_id for p in after.edge_penalties] if after else []
    changed = before_ids != after_ids

    dist = dur = None
    if before and after and after.geometry.coordinates:
        dist = after.distance_meters - before.distance_meters
        dur = after.estimated_duration_seconds - before.estimated_duration_seconds

    prefix = f"{SIMULATED_TAG}: " if simulated else ""
    if before is None or not before_edges:
        text = prefix + "There was no earlier route to compare with."
    elif after is None or not after_ids:
        text = prefix + (
            f"A new restriction removed {len(removed)} edge(s) from the previous route and no "
            "route is currently available."
            if removed
            else "No route is currently available."
        )
    elif not removed and not changed:
        text = prefix + "The route is unchanged by the new restriction."
    else:
        uniq = sorted({_describe_cause(c) for cs in causes.values() for c in cs})
        why = f" Cause: {'; '.join(uniq)}." if uniq else ""
        move = ""
        if dist is not None and dur is not None:
            move = (
                f" The updated route is {abs(dist):.0f} m "
                f"{'longer' if dist >= 0 else 'shorter'} and about {abs(dur) / 60:.1f} min "
                f"{'longer' if dur >= 0 else 'shorter'}."
            )
        text = prefix + (
            f"{len(removed)} edge(s) of the previous route were removed by a new restriction."
            f"{why}{move} Algorithm-generated update."
        )
    return RouteChange(
        changed=changed,
        removed_edge_ids=removed,
        causes=causes,
        distance_delta_meters=dist,
        duration_delta_seconds=dur,
        text=text,
        simulated=simulated,
    )


__all__ = [
    "RouteCandidate",
    "RouteChange",
    "RouteExplanation",
    "explain_change",
    "explain_route",
    "latest_data_timestamp",
    "level_for_candidate",
    "reason_summary",
    "restrictions_considered",
    "uncertain_fraction",
    "uncertainty_level",
]

# Mapping type re-exported for callers building causes by edge id.
CausesByEdge = Mapping[str, Sequence[RestrictionCause]]
