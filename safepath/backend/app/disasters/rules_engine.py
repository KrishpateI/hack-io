"""Rules_Engine: eligibility filtering and Policy_Outcome precedence.

Pure and deterministic. Path search happens after policy evaluation, so the API has two stages:

* ``evaluate_pre_route(ctx, ...)`` applies ranks 1-5 (and defaults to rank 7).
* ``finalize(decision, route_found, isolated)`` applies rank 6 once routing has run.

``evaluate(ctx, ...)`` chains both for callers that already know the routing result.
Unverified community reports are never treated as Official_Notices: only ``ctx.notices``
(``OfficialNotice`` records with a Source_Record) can influence the outcome.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from shapely.geometry import Point, shape

from app.disasters.base import DisasterModule, IncidentContext, PolicyOverride, get_module
from app.models.domain import (
    Incident,
    IncidentType,
    OfficialNotice,
    PolicyDecision,
    PolicyOutcome,
)

EXPIRY_GRACE = timedelta(seconds=60)
UNKNOWN_SOURCE = "Unknown source"

REASON_UNKNOWN_SOURCE = "unknown_source"
REASON_CBD = "cbd_derived"
REASON_HMP = "hmp_boundary"
REASON_EXPIRED = "expired"
REASON_NOT_YET_EFFECTIVE = "not_yet_effective"
REASON_OTHER_INCIDENT = "other_incident"

NO_SHELTER_TEXT = "No verified shelter available. Follow official guidance and stay indoors."


@dataclass(frozen=True)
class ExcludedNotice:
    notice: OfficialNotice
    reason: str
    label: str | None = None  # "Unknown source" for notices lacking a Source_Record


# ---------------------------------------------------------------------------- eligibility
def _is_hmp_boundary(notice: OfficialNotice) -> bool:
    """Cook County Hazard Mitigation Plan area used as a live boundary (9.5)."""
    if notice.notice_type != "temporary_perimeter":
        return False
    blob = " ".join((notice.source_organization, notice.source_url, notice.source_record_id)).lower()
    return "hazard mitigation" in blob or "hazard_mitigation" in blob or "hmp" in blob.split()


def incident_boundary_is_live(incident: Incident) -> bool:
    """False when the boundary is missing or derives from the Cook County HMP (9.5)."""
    if not incident.geographic_boundary:
        return False
    src = incident.source.lower()
    return not ("hazard mitigation" in src or "hazard_mitigation" in src or "hmp" in src.split())


def notice_exclusion(notice: OfficialNotice, now: datetime, incident_id: str | None = None) -> ExcludedNotice | None:
    """Return why a notice is ineligible, or None when it is eligible."""
    if not (notice.source_record_id or "").strip():
        return ExcludedNotice(notice, REASON_UNKNOWN_SOURCE, UNKNOWN_SOURCE)
    if notice.cbd_derived:
        return ExcludedNotice(notice, REASON_CBD)
    if _is_hmp_boundary(notice):
        return ExcludedNotice(notice, REASON_HMP)
    if notice.expires_at is not None and notice.expires_at + EXPIRY_GRACE < now:
        return ExcludedNotice(notice, REASON_EXPIRED)
    if notice.effective_at > now:
        return ExcludedNotice(notice, REASON_NOT_YET_EFFECTIVE)
    if incident_id is not None and notice.incident_id != incident_id:
        return ExcludedNotice(notice, REASON_OTHER_INCIDENT)
    return None


def filter_notices(ctx: IncidentContext) -> tuple[list[OfficialNotice], list[ExcludedNotice]]:
    """Split ``ctx.notices`` into eligible notices and excluded ones with reasons."""
    eligible: list[OfficialNotice] = []
    excluded: list[ExcludedNotice] = []
    for n in ctx.notices:
        ex = notice_exclusion(n, ctx.now, ctx.incident.incident_id)
        (excluded if ex else eligible).append(ex or n)  # type: ignore[arg-type]
    eligible.sort(key=lambda n: (n.effective_at, n.notice_id))
    return eligible, excluded


# ----------------------------------------------------------------------------- coverage
def _contains(geometry: dict[str, Any] | None, lon: float, lat: float) -> bool:
    if not geometry:
        return False
    try:
        return bool(shape(geometry).covers(Point(lon, lat)))
    except Exception:  # malformed geometry covers nothing
        return False


def covers_origin(notice: OfficialNotice, ctx: IncidentContext) -> bool:
    """Point-in-polygon on the notice geometry; no geometry means the incident boundary."""
    lon, lat = ctx.origin.longitude, ctx.origin.latitude
    if notice.geometry:
        return _contains(notice.geometry, lon, lat)
    boundary = ctx.incident.geographic_boundary
    if boundary:
        return _contains(boundary, lon, lat) if incident_boundary_is_live(ctx.incident) else False
    # Whole-incident notice with no recorded boundary: it applies to the incident as a whole.
    return True


def _directive(notice: OfficialNotice) -> str | None:
    if notice.notice_type == "shelter_in_place":
        return "shelter"
    if notice.notice_type == "incident_destination":
        return "evacuate"
    return None


def _conflicting(covering: list[OfficialNotice]) -> list[OfficialNotice]:
    """Notices giving opposite instructions (shelter in place vs go to a destination)."""
    kinds = {_directive(n) for n in covering} - {None}
    if len(kinds) < 2:
        return []
    return [n for n in covering if _directive(n) in kinds]


def _is_unmodelable(notice: OfficialNotice, incident: Incident) -> bool:
    if notice.notice_type == "follow_instructions":
        return True
    # Fire instructions are always applied ahead of computed routes (20.4).
    return incident.incident_type == IncidentType.FIRE and notice.notice_type in (
        "transportation_rule",
        "shelter_in_place",
    )


def destination_priority(notices: Iterable[OfficialNotice]) -> list[str]:
    """Ids of incident-specific destination notices, ranked ahead of non-official ones (11.5)."""
    ranked = sorted(
        (n for n in notices if n.notice_type == "incident_destination"),
        key=lambda n: (n.effective_at, n.notice_id),
    )
    return [n.notice_id for n in ranked]


def _describe(notices: Iterable[OfficialNotice]) -> str:
    return " | ".join(f"{n.text} (Source: {n.source_organization}, {n.source_record_id})" for n in notices)


# ------------------------------------------------------------------------------ stage 1
def _module_override(ctx: IncidentContext, module: DisasterModule | None) -> PolicyOverride | None:
    if module is None:
        try:
            module = get_module(ctx.incident.incident_type)
        except KeyError:
            return None
    return module.policy_override(ctx)


def evaluate_pre_route(
    ctx: IncidentContext,
    *,
    module: DisasterModule | None = None,
    missing_data: Iterable[str] = (),
) -> PolicyDecision:
    """Apply ranks 1-5; falls through to ROUTE_OPTIONS_AVAILABLE (pending rank 6)."""
    eligible, _excluded = filter_notices(ctx)
    dest = destination_priority(eligible)
    covering = [n for n in eligible if covers_origin(n, ctx)]

    # Rank 1: conflicting instructions
    conflict = _conflicting(covering)
    if conflict:
        return PolicyDecision(
            outcome=PolicyOutcome.FOLLOW_OFFICIAL_INSTRUCTIONS,
            notices=conflict,
            explanation="Official notices give conflicting instructions for your location. "
            "Follow official instructions: " + _describe(conflict),
            destination_priority=dest,
        )

    # Rank 2: shelter in place
    shelter = [n for n in covering if n.notice_type == "shelter_in_place"]
    if shelter and not (ctx.incident.incident_type == IncidentType.FIRE):
        return PolicyDecision(
            outcome=PolicyOutcome.SHELTER_IN_PLACE,
            notices=shelter,
            explanation="Shelter in place: " + _describe(shelter),
            destination_priority=dest,
        )

    # Rank 3: instructions SafePath cannot model (including fire instructions)
    unmodelable = [n for n in covering if _is_unmodelable(n, ctx.incident)]
    if unmodelable:
        return PolicyDecision(
            outcome=PolicyOutcome.FOLLOW_OFFICIAL_INSTRUCTIONS,
            notices=unmodelable,
            explanation="Follow official instructions: " + _describe(unmodelable),
            destination_priority=dest,
        )

    # Rank 4: module policy override
    override = _module_override(ctx, module)
    if override is not None:
        official = list(shelter) or [n for n in covering if n.notice_id in override.notice_ids]
        parts = [_describe(official)] if official else []
        parts.append(override.text if (override.source_record_id or official) else f"{NO_SHELTER_TEXT} {override.text}".strip())
        return PolicyDecision(
            outcome=override.outcome,
            notices=official,
            explanation=" ".join(p for p in parts if p),
            destination_priority=dest,
        )

    # Rank 5: required data missing
    missing = sorted({str(m) for m in missing_data})
    if missing:
        return PolicyDecision(
            outcome=PolicyOutcome.INSUFFICIENT_DATA,
            explanation="Required data is missing: " + ", ".join(missing),
            missing_data=missing,
            destination_priority=dest,
        )

    # Rank 7 (default), subject to rank 6 in finalize()
    return PolicyDecision(
        outcome=PolicyOutcome.ROUTE_OPTIONS_AVAILABLE,
        explanation="Route options are available.",
        destination_priority=dest,
    )


# ------------------------------------------------------------------------------ stage 2
def finalize(
    decision: PolicyDecision,
    route_found: bool,
    isolated: Iterable[str] = (),
) -> PolicyDecision:
    """Apply rank 6. ``isolated`` lists endpoints cut off by hard restrictions
    ("origin" and/or "destination"). Higher-ranked decisions are returned unchanged."""
    if decision.outcome is not PolicyOutcome.ROUTE_OPTIONS_AVAILABLE:
        return decision
    iso = sorted(set(isolated))
    if iso or not route_found:
        who = " and ".join(iso)
        why = f"The {who} is isolated by hard restrictions." if iso else "No path exists."
        return decision.model_copy(
            update={
                "outcome": PolicyOutcome.NO_VERIFIED_ROUTE_AVAILABLE,
                "explanation": f"No verified route is available. {why}",
            }
        )
    return decision


def evaluate(
    ctx: IncidentContext,
    *,
    module: DisasterModule | None = None,
    missing_data: Iterable[str] = (),
    route_found: bool = True,
    isolated: Iterable[str] = (),
) -> PolicyDecision:
    """Single-shot evaluation of the full precedence list."""
    return finalize(
        evaluate_pre_route(ctx, module=module, missing_data=missing_data), route_found, isolated
    )
