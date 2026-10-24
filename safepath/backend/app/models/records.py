"""Persistent record models (DynamoDB tables) with PublicView / PrivateView serializers.

Public views never contain reporter/sender/user ids or exact coordinates for check-ins and
messages. Private views are for the record owner (``view_for``). Incident, OfficialNotice and
Restriction hold no personal data and are reused from ``domain`` as their own public view.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain import (
    HazardReport as RoutingHazardReport,
    Incident,
    OfficialNotice,
    Restriction,
)
from app.models.privacy import coarsen

__all__ = [
    "Incident", "OfficialNotice", "UserRecord", "UserPublic", "UserPrivate",
    "HazardReportRecord", "HazardReportPublic", "HazardReportPrivate",
    "ReportVote", "VotePublic", "VotePrivate", "MessageRecord", "MessagePublic",
    "MessagePrivate", "CheckInRecord", "CheckInPublic", "CheckInPrivate",
    "OverlayRecord", "OverlayPublic", "OverlayPrivate", "EventRecord", "EventPublic",
    "EventPrivate", "AuditLogRecord", "AuditPublic", "AuditPrivate", "RateLimitRecord",
    "view_for",
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- Users
class PrivacyPreferences(_Base):
    share_location: bool = False


class UserRecord(_Base):
    user_id: str
    display_name: str | None = None
    role: str = "user"
    created_at: datetime
    privacy_preferences: PrivacyPreferences = Field(default_factory=PrivacyPreferences)
    alias_seed: str | None = None

    @property
    def owner_id(self) -> str:
        return self.user_id

    def public_view(self, alias: str) -> "UserPublic":
        """Only a per-incident alias is ever public; never id, name or role details."""
        return UserPublic(alias=alias)

    def private_view(self) -> "UserPrivate":
        return UserPrivate(
            user_id=self.user_id, display_name=self.display_name, role=self.role,
            created_at=self.created_at, privacy_preferences=self.privacy_preferences,
        )


class UserPublic(_View):
    alias: str


class UserPrivate(_View):
    user_id: str
    display_name: str | None
    role: str
    created_at: datetime
    privacy_preferences: PrivacyPreferences


# ---------------------------------------------------------------- HazardReports
class StatusHistoryEntry(_Base):
    status: str
    at: datetime
    actor_role: str | None = None
    reason: str | None = None


class HazardReportRecord(RoutingHazardReport):
    """Routing-relevant HazardReport plus persistent/private fields."""

    reporter_id: str
    alias: str
    description: str = ""
    photo_key: str | None = None
    updated_at: datetime
    status_history: list[StatusHistoryEntry] = []
    duplicate_of: str | None = None
    version: int = Field(default=1, ge=1)
    client_report_id: str | None = None

    @property
    def owner_id(self) -> str:
        return self.reporter_id

    def public_view(self) -> "HazardReportPublic":
        return HazardReportPublic(
            report_id=self.report_id, incident_id=self.incident_id, alias=self.alias,
            category=self.category, latitude=self.latitude, longitude=self.longitude,
            affected_edge_ids=self.affected_edge_ids, description=self.description,
            verification_status=self.verification_status, confirmations=self.confirmations,
            disputes=self.disputes, created_at=self.created_at, expires_at=self.expires_at,
            updated_at=self.updated_at, duplicate_of=self.duplicate_of, simulated=self.simulated,
        )

    def private_view(self) -> "HazardReportPrivate":
        return HazardReportPrivate(
            **self.public_view().model_dump(), photo_key=self.photo_key,
            client_report_id=self.client_report_id, version=self.version,
            status_history=self.status_history,
        )


class HazardReportPublic(_View):
    report_id: str
    incident_id: str
    alias: str
    category: str
    latitude: float
    longitude: float
    affected_edge_ids: list[str]
    description: str
    verification_status: str
    confirmations: int
    disputes: int
    created_at: datetime
    expires_at: datetime
    updated_at: datetime
    duplicate_of: str | None
    simulated: bool


class HazardReportPrivate(HazardReportPublic):
    photo_key: str | None
    client_report_id: str | None
    version: int
    status_history: list[StatusHistoryEntry]


# ---------------------------------------------------------------- ReportVotes
class ReportVote(_Base):
    report_id: str
    user_id: str
    action: Literal["confirm", "dispute"]
    created_at: datetime

    @property
    def owner_id(self) -> str:
        return self.user_id

    def public_view(self) -> "VotePublic":
        return VotePublic(report_id=self.report_id, action=self.action)

    def private_view(self) -> "VotePrivate":
        return VotePrivate(report_id=self.report_id, action=self.action, created_at=self.created_at)


class VotePublic(_View):
    report_id: str
    action: str


class VotePrivate(VotePublic):
    created_at: datetime


# ---------------------------------------------------------------- Messages
class MessageRecord(_Base):
    message_id: str
    incident_id: str
    sender_id: str
    alias: str
    message_type: str
    content: str
    created_at: datetime
    approximate_location: tuple[float, float] | None = None
    share_location: bool = False  # explicit opt-in for the public location
    hidden: bool = False
    source_label: str | None = None

    @property
    def owner_id(self) -> str:
        return self.sender_id

    def public_view(self) -> "MessagePublic":
        loc = None
        if self.share_location and self.approximate_location is not None:
            loc = coarsen(*self.approximate_location)
        return MessagePublic(
            message_id=self.message_id, incident_id=self.incident_id, alias=self.alias,
            message_type=self.message_type,
            content="" if self.hidden else self.content, hidden=self.hidden,
            created_at=self.created_at, approximate_location=loc, source_label=self.source_label,
        )

    def private_view(self) -> "MessagePrivate":
        return MessagePrivate(
            **self.public_view().model_dump(exclude={"approximate_location", "content"}),
            content=self.content,
            approximate_location=(
                coarsen(*self.approximate_location) if self.approximate_location else None
            ),
            share_location=self.share_location,
        )


class MessagePublic(_View):
    message_id: str
    incident_id: str
    alias: str
    message_type: str
    content: str
    hidden: bool
    created_at: datetime
    approximate_location: tuple[float, float] | None
    source_label: str | None


class MessagePrivate(MessagePublic):
    share_location: bool


# ---------------------------------------------------------------- CheckIns
class CheckInRecord(_Base):
    checkin_id: str
    incident_id: str
    user_id: str
    alias: str
    status: str
    created_at: datetime
    optional_location: tuple[float, float] | None = None  # coarse, rounded before storage
    visibility: Literal["private", "shared"] = "private"
    location_expires_at: datetime | None = None

    @property
    def owner_id(self) -> str:
        return self.user_id

    def _location_public(self, now: datetime | None) -> bool:
        if self.visibility != "shared" or self.optional_location is None:
            return False
        if self.location_expires_at is not None and (now is None or now >= self.location_expires_at):
            return False
        return True

    def public_view(self, now: datetime | None = None) -> "CheckInPublic":
        loc = coarsen(*self.optional_location) if self._location_public(now) else None
        return CheckInPublic(
            checkin_id=self.checkin_id, incident_id=self.incident_id, alias=self.alias,
            status=self.status, created_at=self.created_at, location=loc,
        )

    def private_view(self) -> "CheckInPrivate":
        return CheckInPrivate(
            checkin_id=self.checkin_id, incident_id=self.incident_id, alias=self.alias,
            status=self.status, created_at=self.created_at,
            location=coarsen(*self.optional_location) if self.optional_location else None,
            visibility=self.visibility, location_expires_at=self.location_expires_at,
        )


class CheckInPublic(_View):
    checkin_id: str
    incident_id: str
    alias: str
    status: str
    created_at: datetime
    location: tuple[float, float] | None


class CheckInPrivate(CheckInPublic):
    visibility: str
    location_expires_at: datetime | None


# ---------------------------------------------------------------- Overlays
class OverlayRecord(Restriction):
    status: Literal["active", "removed"] = "active"
    created_at: datetime
    removed_at: datetime | None = None

    def public_view(self) -> "OverlayPublic":
        return OverlayPublic(
            restriction_id=self.restriction_id, incident_id=self.incident_id, kind=self.kind,
            edge_ids=self.edge_ids, geometry=self.geometry, source_record_id=self.source_record_id,
            simulated=self.simulated, status=self.status, created_at=self.created_at,
            removed_at=self.removed_at,
        )

    def private_view(self) -> "OverlayPrivate":
        return OverlayPrivate(**self.public_view().model_dump(), source_report_id=self.source_report_id)


class OverlayPublic(_View):
    restriction_id: str
    incident_id: str
    kind: str
    edge_ids: list[str]
    geometry: dict[str, Any] | None
    source_record_id: str | None
    simulated: bool
    status: str
    created_at: datetime
    removed_at: datetime | None


class OverlayPrivate(OverlayPublic):
    source_report_id: str | None


# ---------------------------------------------------------------- Events
class EventRecord(_Base):
    event_id: str
    incident_id: str
    type: str
    payload: dict[str, Any] = {}
    created_at: datetime
    ttl: int | None = None  # epoch seconds, 24 h retention

    def public_view(self) -> "EventPublic":
        return EventPublic(
            event_id=self.event_id, incident_id=self.incident_id, type=self.type,
            payload=_scrub(self.payload), created_at=self.created_at,
        )

    def private_view(self) -> "EventPrivate":
        return EventPrivate(**self.public_view().model_dump(), ttl=self.ttl)


_PRIVATE_KEYS = frozenset({"reporter_id", "sender_id", "user_id", "actor_id", "alias_seed",
                           "display_name", "email", "photo_key", "client_report_id"})


def _scrub(value: Any) -> Any:
    """Recursively drop private keys from event payloads."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in _PRIVATE_KEYS}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


class EventPublic(_View):
    event_id: str
    incident_id: str
    type: str
    payload: dict[str, Any]
    created_at: datetime


class EventPrivate(EventPublic):
    ttl: int | None


# ---------------------------------------------------------------- AuditLog
class AuditLogRecord(_Base):
    audit_id: str
    day: str
    ts: datetime
    actor_id: str
    actor_role: str
    action: str
    target_id: str | None = None

    def public_view(self) -> "AuditPublic":
        """Public audit view exposes only what happened, not who did it."""
        return AuditPublic(audit_id=self.audit_id, ts=self.ts, actor_role=self.actor_role,
                           action=self.action, target_id=self.target_id)

    def private_view(self) -> "AuditPrivate":
        return AuditPrivate(**self.public_view().model_dump(), day=self.day, actor_id=self.actor_id)


class AuditPublic(_View):
    audit_id: str
    ts: datetime
    actor_role: str
    action: str
    target_id: str | None


class AuditPrivate(AuditPublic):
    day: str
    actor_id: str


# ---------------------------------------------------------------- RateLimits
class RateLimitRecord(_Base):
    """Internal only: no public/private view is ever served."""

    subject_action: str  # PK "subject#action"
    window_start: int
    count: int = Field(default=0, ge=0)
    ttl: int | None = None


# ---------------------------------------------------------------- helper
def view_for(record: Any, principal_subject: str | None, **kwargs: Any) -> BaseModel:
    """PrivateView for the owner, otherwise PublicView."""
    owner = getattr(record, "owner_id", None)
    if owner is not None and principal_subject is not None and principal_subject == owner:
        return record.private_view()
    return record.public_view(**kwargs)
