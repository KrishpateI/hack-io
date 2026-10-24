"""Provenance models: Data_Classification, ReuseDetermination, SourceRecord (Req 10.1, 9.2)."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DataClassification(str, Enum):
    """Data_Classification assigned to every rule, zone, closure, shelter, destination, dataset."""

    HISTORICAL = "Historical"
    STATIC = "Static"
    CURRENT = "Current"
    SIMULATED = "Simulated"


VerificationStatus = Literal["verified", "unverified", "unknown", "simulated"]


class ReuseDetermination(BaseModel):
    """Public-reuse determination for a UIC source: what is approved and what is excluded."""

    model_config = ConfigDict(extra="forbid")

    approved_content: list[str]
    excluded_content: list[str]
    determined_at: datetime | None = None
    basis: str | None = None


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    organization: str
    url: str
    title: str
    published_at: date | None = None
    retrieved_at: datetime
    geographic_applicability: str
    data_classification: DataClassification
    verification_status: VerificationStatus
    page_or_section: str | None = None
    usage_restrictions: list[str] = []
    is_official_source: bool
    reuse_determination: ReuseDetermination | None = None
    check_status: Literal["ok", "unreachable", "not_located"] | None = None
    checked_at: datetime | None = None
