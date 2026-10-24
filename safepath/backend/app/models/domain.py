"""Domain models: incidents, notices, hazard reports, rules, restrictions, policy decisions."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PolicyOutcome(str, Enum):
    """Exactly one of these is set on every route response (13.3, 43.3)."""

    ROUTE_OPTIONS_AVAILABLE = "ROUTE_OPTIONS_AVAILABLE"
    SHELTER_IN_PLACE = "SHELTER_IN_PLACE"
    FOLLOW_OFFICIAL_INSTRUCTIONS = "FOLLOW_OFFICIAL_INSTRUCTIONS"
    NO_VERIFIED_ROUTE_AVAILABLE = "NO_VERIFIED_ROUTE_AVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class IncidentType(str, Enum):
    FLOOD = "flood"
    EARTHQUAKE = "earthquake"
    TORNADO = "tornado"
    FIRE = "fire"
    BUILDING = "building"


class VerificationStatus(str, Enum):
    """Hazard report verification status (state machine in the design)."""

    UNVERIFIED = "Unverified"
    CORROBORATED = "Corroborated"
    OFFICIALLY_VERIFIED = "Officially_Verified"
    DISPUTED = "Disputed"
    RESOLVED = "Resolved"
    EXPIRED = "Expired"


# Alias matching the spec's term "Verification_Status".
Verification_Status = VerificationStatus


class TravelMode(str, Enum):
    WALKING = "walking"
    WHEELCHAIR = "wheelchair"
    DRIVING = "driving"


class MobilityProfile(str, Enum):
    STANDARD = "standard"
    STEP_FREE = "step_free"

    @property
    def requires_step_free(self) -> bool:
        return self is MobilityProfile.STEP_FREE


class UncertaintyLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    UNKNOWN = "Unknown"


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    incident_type: IncidentType
    geographic_boundary: dict[str, Any] | None = None  # GeoJSON
    status: str = "active"
    source: str
    created_at: datetime
    updated_at: datetime
    simulation_flag: bool = False
    scenario_id: str | None = None


NoticeType = Literal[
    "temporary_perimeter",
    "road_closure",
    "incident_destination",
    "shelter_in_place",
    "road_direction_restriction",
    "transportation_rule",
    "follow_instructions",
]


class OfficialNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notice_id: str
    incident_id: str
    notice_type: NoticeType
    text: str
    geometry: dict[str, Any] | None = None
    edge_ids: list[str] = []
    source_organization: str
    source_url: str
    source_record_id: str
    effective_at: datetime
    expires_at: datetime | None = None
    published_at: datetime
    retrieved_at: datetime
    verification_status: str
    simulation_flag: bool
    cbd_derived: bool = False


class HazardReport(BaseModel):
    """Routing-relevant fields of a hazard report."""

    model_config = ConfigDict(extra="forbid")

    report_id: str
    incident_id: str
    category: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    affected_edge_ids: list[str] = []
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    confirmations: int = Field(default=0, ge=0)
    disputes: int = Field(default=0, ge=0)
    created_at: datetime
    expires_at: datetime
    simulated: bool = False
    source_record_id: str | None = None


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    version: int
    data_classification: str
    effective_at: datetime
    expires_at: datetime | None = None
    source_record_id: str
    body: dict[str, Any]


RestrictionKind = Literal[
    "closure",
    "impassable_polygon",
    "prohibited_area",
    "stairwell_closure",
    "road_direction",
    "module",
]


class RestrictionCause(BaseModel):
    """Why an edge is blocked (design: kind, id, source_record_id, simulated)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RestrictionKind
    id: str
    source_record_id: str | None = None
    simulated: bool = False


class Restriction(BaseModel):
    """A hard restriction on a set of edges and/or a geometry."""

    model_config = ConfigDict(extra="forbid")

    restriction_id: str
    incident_id: str
    kind: RestrictionKind
    edge_ids: list[str] = []
    geometry: dict[str, Any] | None = None
    source_report_id: str | None = None
    source_record_id: str | None = None
    simulated: bool = False
    active: bool = True

    def cause(self) -> RestrictionCause:
        return RestrictionCause(
            kind=self.kind,
            id=self.restriction_id,
            source_record_id=self.source_record_id,
            simulated=self.simulated,
        )


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: PolicyOutcome
    notices: list[OfficialNotice] = []
    explanation: str
    missing_data: list[str] = []
    destination_priority: list[str] = []
