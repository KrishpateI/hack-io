"""Route request/response models (Req 13.3, 15.1, 43.1-43.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain import (
    MobilityProfile,
    OfficialNotice,
    PolicyOutcome,
    RestrictionCause,
    TravelMode,
    UncertaintyLevel,
)

ALGORITHM_LABEL = "algorithm-generated"


class LatLon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float
    longitude: float


class RouteRequest(BaseModel):
    """Validated route request. Build via ``routing.validation.validate_route_request``."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    origin: LatLon
    destination: LatLon
    travel_mode: TravelMode
    mobility_profile: MobilityProfile
    alternatives: int = Field(default=1, ge=1, le=3)
    origin_indoor_node_id: str | None = None


class RestrictionConsidered(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    id: str
    source_record_id: str | None = None
    simulated: bool = False

    @classmethod
    def from_cause(cls, cause: RestrictionCause) -> "RestrictionConsidered":
        return cls(
            kind=cause.kind,
            id=cause.id,
            source_record_id=cause.source_record_id,
            simulated=cause.simulated,
        )


class EdgePenalty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str
    travel: float = Field(ge=0)
    hazard: dict[str, float] = {}
    uncertainty: dict[str, float] = {}
    report_ids: list[str] = []


class LineStringGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["LineString"] = "LineString"
    coordinates: list[list[float]] = []


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str
    route_type: str = "disaster-adjusted"
    geometry: LineStringGeometry = Field(default_factory=LineStringGeometry)
    distance_meters: float = Field(ge=0)
    estimated_duration_seconds: float = Field(ge=0)
    hazard_flags: list[str] = []
    excluded_edges_count: int = Field(ge=0)
    uncertainty_level: UncertaintyLevel
    policy_outcome: PolicyOutcome
    restrictions_considered: list[RestrictionConsidered] = []
    edge_penalties: list[EdgePenalty] = []
    reason_summary: str = ""
    sources: list[str] = []
    confidence_limitations: list[str] = []
    calculated_at: datetime
    status: str = "ok"
    label: str = ALGORITHM_LABEL


class RouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_outcome: PolicyOutcome
    explanation: str
    status: str = "ok"
    calculated_at: datetime
    graph_version: str | None = None
    data_timestamp: datetime | None = None
    route_count: int = Field(default=0, ge=0)
    label: str = ALGORITHM_LABEL
    routes: list[Route] = []
    notices: list[OfficialNotice] = []
    missing_data: list[str] = []
    sources: list[str] = []
