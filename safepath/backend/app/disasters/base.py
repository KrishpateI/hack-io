"""Disaster module protocol, registry and the hard-restriction data types.

Hard restrictions (``hard_restrictions`` -> ``RestrictionSet`` -> ``EdgeMask``) and soft
penalties (``edge_penalty`` -> ``PenaltyParts``) are produced by separate methods called from
different pipeline stages, so a module cannot move an edge between categories (Req 14.2).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np

from app.models.domain import (
    HazardReport,
    Incident,
    IncidentType,
    MobilityProfile,
    OfficialNotice,
    PolicyOutcome,
    Restriction,
    RestrictionCause,
    TravelMode,
    UncertaintyLevel,
)
from app.models.graph import Edge
from app.models.routing import LatLon

if TYPE_CHECKING:
    from app.routing.graph import Graph


# --------------------------------------------------------------------------- value types
@dataclass(frozen=True)
class IncidentContext:
    incident: Incident
    now: datetime
    origin: LatLon
    destination: LatLon | None = None
    profile: MobilityProfile = MobilityProfile.STANDARD
    mode: TravelMode = TravelMode.WALKING
    notices: tuple[OfficialNotice, ...] = ()  # active, eligible, non-CBD
    reports: tuple[HazardReport, ...] = ()  # non-Resolved, non-Expired
    overlays: tuple[Restriction, ...] = ()  # confirmed closures, impassable polygons

    def __post_init__(self) -> None:
        # accept lists from callers but keep the context immutable
        for name in ("notices", "reports", "overlays"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


def _non_negative(d: Mapping[str, float], what: str) -> dict[str, float]:
    out = {str(k): float(v) for k, v in d.items()}
    for k, v in out.items():
        if not v >= 0.0:  # also rejects NaN
            raise ValueError(f"{what} part {k!r} must be >= 0, got {v}")
    return out


@dataclass(frozen=True)
class PenaltyParts:
    """Named hazard and uncertainty penalty parts for one edge; every part is >= 0."""

    hazard: Mapping[str, float] = field(default_factory=dict)
    uncertainty: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "hazard", _non_negative(self.hazard, "hazard"))
        object.__setattr__(self, "uncertainty", _non_negative(self.uncertainty, "uncertainty"))

    @property
    def hazard_total(self) -> float:
        return float(sum(self.hazard.values()))

    @property
    def uncertainty_total(self) -> float:
        return float(sum(self.uncertainty.values()))

    @property
    def total(self) -> float:
        return self.hazard_total + self.uncertainty_total


@dataclass(frozen=True)
class PolicyOverride:
    """A module-level policy outcome (e.g. tornado SHELTER_IN_PLACE)."""

    outcome: PolicyOutcome
    text: str
    source_record_id: str | None = None
    notice_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Advisory:
    """Module advisory text with its Source_Record (None means unknown source)."""

    text: str
    source_record_id: str | None = None
    simulated: bool = False


# ------------------------------------------------------------------------ restrictions
class EdgeMask(np.ndarray):
    """Boolean array aligned with ``graph.edges``; True = edge usable, False = blocked."""

    def __new__(cls, allowed: np.ndarray | Iterable[bool]) -> "EdgeMask":
        arr = np.asarray(allowed, dtype=bool)
        if arr.ndim != 1:
            raise ValueError("EdgeMask must be one-dimensional")
        return arr.view(cls)

    @classmethod
    def all_allowed(cls, num_edges: int) -> "EdgeMask":
        return cls(np.ones(num_edges, dtype=bool))

    @property
    def blocked_count(self) -> int:
        return int((~np.asarray(self)).sum())


@dataclass(frozen=True)
class RestrictionSet:
    """Edges blocked by hard restrictions, with causes, at a given overlay version."""

    blocked_edges: Mapping[str, tuple[RestrictionCause, ...]] = field(default_factory=dict)
    overlay_version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "blocked_edges", {str(k): tuple(v) for k, v in self.blocked_edges.items()}
        )

    def is_blocked(self, edge_id: str) -> bool:
        return edge_id in self.blocked_edges

    def causes(self, edge_id: str) -> tuple[RestrictionCause, ...]:
        return self.blocked_edges.get(edge_id, ())

    def mask(self, graph: "Graph") -> EdgeMask:
        """Mask aligned with graph edge indices; blocked edges False, everything else True."""
        allowed = np.ones(graph.num_edges, dtype=bool)
        if self.blocked_edges:
            index = {eid: i for i, eid in enumerate(graph.edge_ids)}
            for eid in self.blocked_edges:
                i = index.get(eid)
                if i is not None:
                    allowed[i] = False
        return EdgeMask(allowed)

    def merged(self, other: "RestrictionSet") -> "RestrictionSet":
        merged: dict[str, tuple[RestrictionCause, ...]] = dict(self.blocked_edges)
        for eid, causes in other.blocked_edges.items():
            existing = merged.get(eid, ())
            merged[eid] = existing + tuple(c for c in causes if c not in existing)
        return RestrictionSet(merged, max(self.overlay_version, other.overlay_version))


# ----------------------------------------------------------------------------- protocol
@runtime_checkable
class DisasterModule(Protocol):
    incident_type: IncidentType

    def policy_override(self, ctx: IncidentContext) -> PolicyOverride | None: ...

    def hard_restrictions(self, ctx: IncidentContext, graph: "Graph") -> Iterable[Restriction]: ...

    def edge_penalty(self, edge: Edge, ctx: IncidentContext, graph: "Graph") -> PenaltyParts: ...

    def route_filter(self, edge: Edge, ctx: IncidentContext) -> bool: ...

    def uncertainty_floor(self, ctx: IncidentContext) -> UncertaintyLevel | None: ...

    def advisories(self, ctx: IncidentContext) -> list[Advisory]: ...


# ----------------------------------------------------------------------------- registry
_REGISTRY: dict[IncidentType, DisasterModule] = {}


def register_module(module: DisasterModule, *, replace: bool = False) -> DisasterModule:
    key = IncidentType(module.incident_type)
    if key in _REGISTRY and not replace:
        raise ValueError(f"disaster module already registered for {key.value}")
    _REGISTRY[key] = module
    return module


def get_module(incident_type: IncidentType | str) -> DisasterModule:
    try:
        return _REGISTRY[IncidentType(incident_type)]
    except KeyError:
        raise KeyError(f"no disaster module registered for {incident_type!s}") from None


def registered_modules() -> dict[IncidentType, DisasterModule]:
    return dict(_REGISTRY)


def unregister_module(incident_type: IncidentType | str) -> None:
    _REGISTRY.pop(IncidentType(incident_type), None)
