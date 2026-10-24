"""MessagingService: one community channel per Incident (Req 26, 28.4, 28.5, 31.8).

Messages are always Community content: the public view carries the source label ``Community``
even when an Authorized_Official posts one. Official information only exists as Official_Notices
(``POST /api/notices``). Public views expose an anonymous alias, never the sender id, and an
approximate location only when the sender opted in; the location is coarsened to the 200 m grid
before it is stored, so the exact position is never persisted.

Message, message-index row and ``message_created`` event are written in one ``Repository.transact``.
SafePath does not dispatch emergency services; assistance-type messages carry that statement.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Callable

from app.adapters.ports import Principal, Repository, TxOp
from app.api.sanitize import sanitize_text
from app.auth.audit import AuditWriter
from app.communication.events import EventBus
from app.models.ids import new_id
from app.models.privacy import coarsen, load_alias_secret, reporter_alias
from app.models.records import MessageRecord
from app.routing.validation import RequestValidationError

log = logging.getLogger(__name__)

MESSAGES_TABLE = "messages"
MESSAGE_INDEX_TABLE = "message_index"
INCIDENTS_TABLE = "incidents"
INCIDENTS_PK = "all"

MESSAGE_TYPES: tuple[str, ...] = (
    "update", "observation", "assistance_request", "person_safe",
    "accessibility_assistance_request", "blocked_route_update",
)
ASSISTANCE_TYPES = frozenset({"assistance_request", "accessibility_assistance_request"})
MAX_CONTENT = 1000
SOURCE_LABEL = "Community"
MESSAGE_CREATED = "message_created"
MESSAGE_HIDDEN = "message_hidden"
HIDE_ACTION = "hide_message"
NO_DISPATCH_NOTICE = (
    "SafePath does not dispatch emergency services and is not connected to 911. "
    "Messages are visible only to other SafePath users. In an emergency call 911."
)
_DEV_ALIAS_SECRET = b"safepath-dev-alias-secret-not-for-production"


class IncidentNotFound(Exception):
    pass


class MessageNotFound(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bad(fields: list[dict[str, str]]) -> RequestValidationError:
    return RequestValidationError(
        400, "validation_error", "invalid fields: " + ", ".join(f["field"] for f in fields), fields)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _record(row: dict) -> MessageRecord:
    return MessageRecord.model_validate({k: v for k, v in row.items() if k not in ("pk", "sk", "version")})


def notice_for(message_type: str) -> str | None:
    return NO_DISPATCH_NOTICE if message_type in ASSISTANCE_TYPES else None


def public_message(rec: MessageRecord) -> dict[str, Any]:
    """Public JSON for a message: alias only, Community label, optional coarse location."""
    view = rec.public_view().model_dump(mode="json")
    view["source_label"] = SOURCE_LABEL
    view["classification"] = SOURCE_LABEL
    notice = notice_for(rec.message_type)
    if notice:
        view["notice"] = notice
    return view


class MessagingService:
    def __init__(self, repo: Repository, bus: EventBus | None, audit: AuditWriter | None = None,
                 clock: Callable[[], datetime] = _now,
                 alias_secret: bytes | str | None = None) -> None:
        self.repo = repo
        self.bus = bus
        self.audit = audit
        self._clock = clock
        self._alias_secret = alias_secret

    # ------------------------------------------------------------------ helpers
    def _secret(self) -> bytes | str:
        if self._alias_secret:
            return self._alias_secret
        try:
            return load_alias_secret()
        except RuntimeError:
            log.warning("SAFEPATH_ALIAS_SECRET not set; using the local development alias secret")
            return _DEV_ALIAS_SECRET

    def incident_exists(self, incident_id: str) -> bool:
        return self.repo.get(INCIDENTS_TABLE, {"pk": INCIDENTS_PK, "sk": incident_id}) is not None

    # ------------------------------------------------------------------ validation
    def validate(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise _bad([{"field": "body", "problem": "must be a JSON object"}])
        errs: list[dict[str, str]] = []

        def err(field: str, problem: str) -> None:
            errs.append({"field": field, "problem": problem})

        incident_id = payload.get("incident_id")
        if not isinstance(incident_id, str) or not incident_id.strip() or len(incident_id) > 64:
            err("incident_id", "required string of 1 to 64 characters")
        mtype = payload.get("message_type")
        if not isinstance(mtype, str) or mtype not in MESSAGE_TYPES:
            err("message_type", "unsupported value; supported values: " + ", ".join(MESSAGE_TYPES))
        content = payload.get("content")
        clean = ""
        if not isinstance(content, str):
            err("content", "required string of 1 to 1000 characters")
        elif len(content) > MAX_CONTENT:
            err("content", f"must be at most {MAX_CONTENT} characters")
        else:
            clean = sanitize_text(content).strip()
            if not clean:
                err("content", "must not be empty")
        share = payload.get("share_location", False)
        if share is None:
            share = False
        if not isinstance(share, bool):
            err("share_location", "must be true or false")
            share = False
        lat, lon = payload.get("latitude"), payload.get("longitude")
        if share:
            if not _is_number(lat) or not -90 <= lat <= 90:
                err("latitude", "required number between -90 and 90 when share_location is true")
            if not _is_number(lon) or not -180 <= lon <= 180:
                err("longitude", "required number between -180 and 180 when share_location is true")
        if errs:
            raise _bad(errs)
        location = coarsen(float(lat), float(lon)) if share else None  # exact point is discarded
        return {"incident_id": incident_id, "message_type": mtype, "content": clean,
                "share_location": share, "approximate_location": location}

    # ------------------------------------------------------------------ create
    def create(self, principal: Principal, payload: Any) -> MessageRecord:
        data = self.validate(payload)
        if not self.incident_exists(data["incident_id"]):
            raise IncidentNotFound(data["incident_id"])
        now = self._clock()
        message_id = new_id("message")
        rec = MessageRecord(
            message_id=message_id, incident_id=data["incident_id"], sender_id=principal.subject,
            alias=reporter_alias(self._secret(), principal.subject, data["incident_id"]),
            message_type=data["message_type"], content=data["content"], created_at=now,
            approximate_location=data["approximate_location"], share_location=data["share_location"],
            hidden=False, source_label=SOURCE_LABEL,
        )
        item = rec.model_dump(mode="json")
        ops = [
            TxOp(kind="put", table=MESSAGES_TABLE, if_absent=True,
                 item={**item, "pk": rec.incident_id, "sk": message_id}),
            TxOp(kind="put", table=MESSAGE_INDEX_TABLE, if_absent=True,
                 item={"pk": message_id, "sk": "", "incident_id": rec.incident_id}),
        ]
        pub = rec.public_view()
        event_payload = {"message_id": message_id, "message_type": rec.message_type,
                         "alias": rec.alias, "source_label": SOURCE_LABEL,
                         "created_at": now.isoformat(),
                         "approximate_location": list(pub.approximate_location)
                         if pub.approximate_location else None}
        if self.bus is not None:
            self.bus.commit(ops, rec.incident_id, MESSAGE_CREATED, event_payload)
        else:
            self.repo.transact(ops)
        return rec

    # ------------------------------------------------------------------ read
    def list_public(self, incident_id: str, since: str | None = None, limit: int = 100) -> list[MessageRecord]:
        """Visible (not hidden) messages of one incident, oldest first, after ``since`` (message_id)."""
        out: list[MessageRecord] = []
        cursor = since or None
        batch = max(limit, 100)
        while len(out) < limit:
            rows = self.repo.query(MESSAGES_TABLE, {"pk": incident_id}, since=cursor, limit=batch)
            if not rows:
                break
            for row in rows:
                rec = _record(row)
                if not rec.hidden:
                    out.append(rec)
                    if len(out) >= limit:
                        break
            if len(rows) < batch:
                break
            cursor = rows[-1]["sk"]
        return out

    def get(self, message_id: str) -> MessageRecord | None:
        idx = self.repo.get(MESSAGE_INDEX_TABLE, {"pk": message_id, "sk": ""})
        if idx is None:
            return None
        row = self.repo.get(MESSAGES_TABLE, {"pk": idx["incident_id"], "sk": message_id})
        return _record(row) if row else None

    # ------------------------------------------------------------------ moderation
    def hide(self, principal: Principal, message_id: str) -> MessageRecord:
        rec = self.get(message_id)
        if rec is None:
            raise MessageNotFound(message_id)
        if rec.hidden:  # idempotent
            return rec
        hidden = rec.model_copy(update={"hidden": True})
        item = hidden.model_dump(mode="json")
        ops = [TxOp(kind="put", table=MESSAGES_TABLE,
                    item={**item, "pk": rec.incident_id, "sk": message_id})]
        payload = {"message_id": message_id}
        if self.bus is not None:
            self.bus.commit(ops, rec.incident_id, MESSAGE_HIDDEN, payload)
        else:
            self.repo.transact(ops)
        if self.audit is not None:
            self.audit.moderation(principal.subject, principal.role.value, message_id, HIDE_ACTION)
        return hidden


__all__ = [
    "MessagingService", "IncidentNotFound", "MessageNotFound", "MESSAGE_TYPES", "ASSISTANCE_TYPES",
    "NO_DISPATCH_NOTICE", "SOURCE_LABEL", "public_message", "notice_for",
]
