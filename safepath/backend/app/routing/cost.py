"""Routing cost model.

This module currently holds the weights loader and validator (task 6.2).
The CostModel itself (task 6.5) extends this file and consumes ``Weights``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml
from fastapi import HTTPException

DEFAULT_WEIGHTS_PATH = Path(__file__).with_name("routing_weights.yaml")
WEIGHTS_FILE_NAME = "routing_weights.yaml"

OK = "ok"
INVALID_CONFIGURATION = "invalid_configuration"

# Sections whose values must all be non-negative numbers.
_NUMERIC_SECTIONS = (
    "mode_factor",
    "v_max_mps",
    "uncertainty",
    "flood",
    "earthquake",
    "fire",
    "building",
    "alternatives",
    "uncertainty_levels",
)
_REQUIRED_UNCERTAINTY_KEYS = (
    "w_unknown_attr", "w_elev_low", "w_elev_unknown", "w_profile_unknown", "w_disputed",
)


class WeightsError(Exception):
    """Invalid or unreadable weights file. Carries the file and offending field."""

    def __init__(self, file: str, field: str, message: str) -> None:
        self.file = file
        self.field = field
        self.message = message
        super().__init__(f"{file}: field '{field}': {message}")

    def to_detail(self) -> dict[str, str]:
        return {
            "error": INVALID_CONFIGURATION,
            "file": self.file,
            "field": self.field,
            "message": self.message,
        }


@dataclass(frozen=True)
class Weights:
    """Validated routing weights. Sections are plain name -> float mappings."""

    version: int
    d_min_meters: float
    uncertainty_attributes: tuple[str, ...]
    mode_factor: Mapping[str, float]
    v_max_mps: Mapping[str, float]
    uncertainty: Mapping[str, float]
    flood: Mapping[str, float]
    earthquake: Mapping[str, float]
    fire: Mapping[str, float]
    building: Mapping[str, float]
    alternatives: Mapping[str, float]
    uncertainty_levels: Mapping[str, float]
    # warning-level penalties for nearby unverified/corroborated reports (optional in the file)
    reports: Mapping[str, float] = field(
        default_factory=lambda: {"w_unverified": 20.0, "w_corroborated": 40.0}
    )


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_number(file: str, name: str, v: Any) -> float:
    if not _is_number(v):
        raise WeightsError(file, name, f"must be a number, got {v!r}")
    if v < 0:
        raise WeightsError(file, name, f"must not be negative, got {v!r}")
    return float(v)


def parse_weights(data: Any, file: str = WEIGHTS_FILE_NAME) -> Weights:
    """Validate parsed YAML data and build ``Weights``."""
    if not isinstance(data, dict):
        raise WeightsError(file, "<root>", "top level must be a mapping")

    for key in ("version", "d_min_meters", "uncertainty_attributes", *_NUMERIC_SECTIONS):
        if key not in data:
            raise WeightsError(file, key, "missing required field")

    version = data["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise WeightsError(file, "version", f"must be an integer, got {version!r}")

    d_min = _check_number(file, "d_min_meters", data["d_min_meters"])

    attrs = data["uncertainty_attributes"]
    if not isinstance(attrs, list) or not all(isinstance(a, str) and a for a in attrs):
        raise WeightsError(file, "uncertainty_attributes", "must be a list of attribute names")

    sections: dict[str, dict[str, float]] = {}
    for sec in _NUMERIC_SECTIONS:
        raw = data[sec]
        if not isinstance(raw, dict) or not raw:
            raise WeightsError(file, sec, "must be a non-empty mapping")
        sections[sec] = {str(k): _check_number(file, f"{sec}.{k}", v) for k, v in raw.items()}

    for k in _REQUIRED_UNCERTAINTY_KEYS:
        if k not in sections["uncertainty"]:
            raise WeightsError(file, f"uncertainty.{k}", "missing required field")
    if sections["uncertainty"]["w_unknown_attr"] <= 0:
        raise WeightsError(
            file, "uncertainty.w_unknown_attr",
            "must be greater than 0 so every Unknown value adds a positive penalty",
        )

    extra: dict[str, Any] = {}
    if "reports" in data:
        raw = data["reports"]
        if not isinstance(raw, dict):
            raise WeightsError(file, "reports", "must be a mapping")
        rep = {str(k): _check_number(file, f"reports.{k}", v) for k, v in raw.items()}
        for k in ("w_unverified", "w_corroborated"):
            if k not in rep:
                raise WeightsError(file, f"reports.{k}", "missing required field")
        extra["reports"] = rep

    return Weights(
        **extra,
        version=version,
        d_min_meters=d_min,
        uncertainty_attributes=tuple(attrs),
        **sections,
    )


def load_weights(path: str | Path = DEFAULT_WEIGHTS_PATH) -> Weights:
    """Read and validate a weights file. Raises ``WeightsError`` on any problem."""
    p = Path(path)
    name = p.name
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise WeightsError(name, "<file>", "weights file not found") from None
    except OSError as exc:
        raise WeightsError(name, "<file>", f"cannot read weights file: {exc}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WeightsError(name, "<file>", f"invalid YAML: {exc}") from None
    return parse_weights(data, name)


@dataclass
class WeightsState:
    """Holds the current weights or the degraded-state error.

    ``reload()`` re-validates; on failure the previous good weights are dropped
    so the routing endpoint reports 503 until the file is fixed.
    """

    path: Path = DEFAULT_WEIGHTS_PATH
    weights: Weights | None = field(default=None)
    error: WeightsError | None = field(default=None)

    def reload(self) -> "WeightsState":
        try:
            self.weights, self.error = load_weights(self.path), None
        except WeightsError as exc:
            self.weights, self.error = None, exc
        return self

    @property
    def status(self) -> str:
        return OK if self.error is None and self.weights is not None else INVALID_CONFIGURATION

    def require(self) -> Weights:
        """Return weights, or raise a 503 HTTPException naming the file and field."""
        if self.weights is None:
            err = self.error or WeightsError(self.path.name, "<file>", "weights not loaded")
            raise HTTPException(status_code=503, detail=err.to_detail())
        return self.weights


def load_weights_state(path: str | Path = DEFAULT_WEIGHTS_PATH) -> WeightsState:
    """Create a state and load it (startup). Never raises."""
    return WeightsState(path=Path(path)).reload()


# ===================================================================== CostModel (task 6.5)
# Route_Cost(e) = travel_cost(e) + disaster_hazard_penalty(e) + uncertainty_penalty(e)
# All three terms are >= 0 and length-scaled: scale(e) = max(distance, d_min) / 100.

from typing import TYPE_CHECKING, Callable  # noqa: E402

import numpy as np  # noqa: E402

from app.disasters.base import DisasterModule, IncidentContext, PenaltyParts  # noqa: E402
from app.models.domain import MobilityProfile, VerificationStatus  # noqa: E402
from app.models.graph import UNKNOWN  # noqa: E402
from app.models.routing import EdgePenalty  # noqa: E402

if TYPE_CHECKING:
    from app.routing.graph import Graph


@dataclass(frozen=True)
class PenaltyComponents:
    """Per-edge cost breakdown. ``total`` equals the cost used by the search."""

    edge_id: str
    travel: float
    hazard: Mapping[str, float] = field(default_factory=dict)
    uncertainty: Mapping[str, float] = field(default_factory=dict)
    report_ids: tuple[str, ...] = ()

    @property
    def hazard_total(self) -> float:
        return float(sum(self.hazard.values()))

    @property
    def uncertainty_total(self) -> float:
        return float(sum(self.uncertainty.values()))

    @property
    def total(self) -> float:
        return self.travel + self.hazard_total + self.uncertainty_total

    @property
    def hazard_flags(self) -> list[str]:
        return [k for k, v in self.hazard.items() if v > 0]

    def to_edge_penalty(self) -> EdgePenalty:
        return EdgePenalty(
            edge_id=self.edge_id,
            travel=self.travel,
            hazard=dict(self.hazard),
            uncertainty=dict(self.uncertainty),
            report_ids=list(self.report_ids),
        )


class CostModel:
    """Soft-step cost for one routing request (graph + weights + incident context)."""

    def __init__(
        self,
        graph: "Graph",
        weights: Weights,
        ctx: IncidentContext,
        module: DisasterModule | None = None,
    ) -> None:
        if graph.model is None:
            raise ValueError("CostModel requires a loaded graph")
        self.graph = graph
        self.weights = weights
        self.ctx = ctx
        self.module = module
        mode = ctx.mode.value
        if mode not in weights.mode_factor or mode not in weights.v_max_mps:
            raise ValueError(f"no weights configured for travel mode {mode!r}")
        self.mode = mode
        self.step_free = ctx.profile is MobilityProfile.STEP_FREE
        self.mode_factor = self.factor(mode, ctx.profile)
        self._cache: dict[int, PenaltyComponents] = {}
        self._reports_by_edge: dict[str, list] = {}
        for r in ctx.reports:
            for eid in r.affected_edge_ids:
                self._reports_by_edge.setdefault(eid, []).append(r)

    # ------------------------------------------------------------------ parameters
    def factor(self, mode: str, profile: MobilityProfile) -> float:
        f = float(self.weights.mode_factor[mode])
        if profile is MobilityProfile.STEP_FREE:
            f = max(f, float(self.weights.mode_factor.get("wheelchair", f)))
        return f

    @property
    def v_max(self) -> float:
        """Speed (m/s) for the admissible heuristic: haversine / v_max <= Route_Cost lower bound."""
        # The configured speed is raised to the fastest speed implied by the loaded graph
        # (distance / travel time) so the heuristic stays admissible even when the graph holds
        # edges faster than the configured bound (e.g. motorway edges at 24.6 m/s).
        v = max(float(self.weights.v_max_mps[self.mode]), self._graph_max_speed())
        return v / min(1.0, self.mode_factor)

    def _graph_max_speed(self) -> float:
        cached = getattr(self, "_graph_vmax", None)
        if cached is None:
            d = np.asarray(self.graph.edge_dist, dtype=float)
            t = np.asarray(self.graph.edge_time, dtype=float)
            ok = (t > 0) & (d > 0)
            cached = float(np.max(d[ok] / t[ok])) if ok.any() else 0.0
            self._graph_vmax = cached
        return cached

    def scale(self, edge_index: int) -> float:
        return max(float(self.graph.edge_dist[edge_index]), self.weights.d_min_meters) / 100.0

    # ------------------------------------------------------------------- exclusion
    def step_free_mask(self) -> np.ndarray:
        """True = usable. Under a step-free profile known-inaccessible edges are excluded;
        otherwise everything is usable. Combine with the hard-restriction mask via ``&``."""
        n = self.graph.num_edges
        if not self.step_free:
            return np.ones(n, dtype=bool)
        edges = self.graph.model.edges  # type: ignore[union-attr]
        return np.fromiter(
            (e.mobility_accessibility != "inaccessible" for e in edges), dtype=bool, count=n
        )

    def route_filter(self) -> Callable[[int], bool]:
        mask = self.step_free_mask()
        return lambda i: bool(mask[i])

    # ------------------------------------------------------------------ components
    def components(self, edge_index: int) -> PenaltyComponents:
        c = self._cache.get(edge_index)
        if c is None:
            c = self._cache[edge_index] = self._compute(edge_index)
        return c

    def _compute(self, i: int) -> PenaltyComponents:
        w = self.weights
        edge = self.graph.edge(i)
        s = self.scale(i)
        travel = float(self.graph.edge_time[i]) * self.mode_factor

        hazard: dict[str, float] = {}
        unc: dict[str, float] = {}

        if self.module is not None:
            parts: PenaltyParts = self.module.edge_penalty(edge, self.ctx, self.graph)
            for k, v in parts.hazard.items():
                hazard[k] = hazard.get(k, 0.0) + v
            for k, v in parts.uncertainty.items():
                unc[k] = unc.get(k, 0.0) + v

        u = w.uncertainty
        for attr in w.uncertainty_attributes:
            if getattr(edge, attr, UNKNOWN) == UNKNOWN:
                unc[f"unknown_{attr}"] = unc.get(f"unknown_{attr}", 0.0) + u["w_unknown_attr"] * s
        if edge.elevation_confidence == "Low":
            unc["elevation_low"] = u["w_elev_low"] * s
        elif edge.elevation_confidence == UNKNOWN:
            unc["elevation_unknown"] = u["w_elev_unknown"] * s
        if self.step_free and edge.mobility_accessibility == UNKNOWN:
            unc["profile_unknown"] = u["w_profile_unknown"] * s

        report_ids: list[str] = []
        statuses: set[VerificationStatus] = set()
        for r in self._reports_by_edge.get(edge.edge_id, ()):
            if r.verification_status in (
                VerificationStatus.UNVERIFIED,
                VerificationStatus.CORROBORATED,
                VerificationStatus.DISPUTED,
            ):
                statuses.add(r.verification_status)
                report_ids.append(r.report_id)
        if VerificationStatus.UNVERIFIED in statuses:
            hazard["report_unverified"] = hazard.get("report_unverified", 0.0) + w.reports["w_unverified"] * s
        if VerificationStatus.CORROBORATED in statuses:
            hazard["report_corroborated"] = (
                hazard.get("report_corroborated", 0.0) + w.reports["w_corroborated"] * s
            )
        if VerificationStatus.DISPUTED in statuses:
            unc["disputed_report"] = u["w_disputed"] * s

        return PenaltyComponents(
            edge_id=edge.edge_id,
            travel=travel,
            hazard=hazard,
            uncertainty=unc,
            report_ids=tuple(report_ids),
        )

    def cost(self, edge_index: int) -> float:
        return self.components(edge_index).total

    def cost_fn(self) -> Callable[[int], float]:
        """``cost_fn(edge_index)`` for ``astar``/``dijkstra``."""
        return self.cost

    def edge_penalties(self, edge_indices) -> list[EdgePenalty]:
        return [self.components(int(i)).to_edge_penalty() for i in edge_indices]
