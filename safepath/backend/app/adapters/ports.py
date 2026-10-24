"""Adapter ports (Repository, RealtimePublisher, AuthProvider, BlobStore) and shared types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Mapping, Protocol, runtime_checkable


class Conflict(Exception):
    """Conditional write failed (item exists, missing, or version mismatch)."""


class RepositoryUnavailable(Exception):
    """The backing store could not complete the operation; nothing was persisted (HTTP 503)."""


class Unauthenticated(Exception):
    """Token missing, malformed, expired or badly signed."""


class AdapterNotAvailable(NotImplementedError):
    """Requested adapter implementation is not provided in this build/profile."""


class Role(str, Enum):
    """Spec roles Guest, User, Moderator, Authorized_Official (Req 22.2)."""

    GUEST = "guest"
    USER = "user"
    MODERATOR = "moderator"
    AUTHORIZED_OFFICIAL = "authorized_official"
    # Backward-compatible aliases (same members, not new roles).
    REGISTERED = "user"
    OFFICIAL = "authorized_official"


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role
    is_guest: bool = False
    expires_at: datetime | None = None


@dataclass(frozen=True)
class GuestSession:
    token: str
    principal: Principal
    expires_at: datetime


@dataclass(frozen=True)
class Event:
    """Event envelope published after a commit."""

    id: str
    incident_id: str
    type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""


@dataclass(frozen=True)
class TxOp:
    """One operation inside Repository.transact."""

    kind: Literal["put", "update_cas", "delete"]
    table: str
    item: Mapping[str, Any] | None = None  # put
    key: Mapping[str, Any] | None = None  # update_cas / delete
    expected_version: int | None = None  # update_cas
    patch: Mapping[str, Any] | None = None  # update_cas
    if_absent: bool = False  # put


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    key: str
    method: str
    headers: Mapping[str, str]
    expires_at: datetime
    max_bytes: int


@runtime_checkable
class Repository(Protocol):
    def put(self, table: str, item: dict, *, if_absent: bool = False) -> None: ...  # raises Conflict
    def get(self, table: str, key: dict) -> dict | None: ...
    def query(self, table: str, key_cond: dict, *, index: str | None = None,
              since: str | None = None, limit: int = 100) -> list[dict]: ...
    def update_cas(self, table: str, key: dict, expected_version: int, patch: dict) -> dict: ...  # raises Conflict
    def transact(self, ops: list[TxOp]) -> None: ...  # all-or-nothing; raises RepositoryUnavailable
    def delete_where(self, table: str, predicate: Mapping) -> int: ...  # scenario reset


@runtime_checkable
class RealtimePublisher(Protocol):
    def publish(self, incident_id: str, event: Event) -> None: ...  # best-effort; never raises


@runtime_checkable
class AuthProvider(Protocol):
    def verify(self, token: str | None) -> Principal: ...  # raises Unauthenticated
    def issue_guest(self) -> GuestSession: ...  # 24 h expiry
    def role_for(self, subject: str) -> Role: ...


@runtime_checkable
class BlobStore(Protocol):
    def presign_put(self, key: str, content_type: str, max_bytes: int) -> PresignedUpload: ...
    def presign_get(self, key: str, ttl_seconds: int = 900) -> str: ...
