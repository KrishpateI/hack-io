"""EventBus: publish-after-commit event envelope on the Repository and RealtimePublisher ports.

The Events row is written in the same ``Repository.transact`` as the state change. Only after
the transaction commits is the event published; publish failures are logged and never fail the
request (clients recover through ``GET /api/events?since=``). Events are ULID-identified, so
lexicographic order equals creation order (Req 25.1, 25.3, 25.6, 30.9).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from app.adapters.ports import Event, RealtimePublisher, Repository, TxOp
from app.models.ids import new_id
from app.models.records import EventRecord

log = logging.getLogger(__name__)

EVENTS_TABLE = "events"
EVENT_TTL = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_event(rec: EventRecord) -> Event:
    pub = rec.public_view()
    return Event(id=pub.event_id, incident_id=pub.incident_id, type=pub.type,
                 payload=pub.payload, created_at=pub.created_at.isoformat())


def event_to_dict(event: Event) -> dict[str, Any]:
    """JSON envelope sent to clients (WebSocket, SSE and catch-up share this shape)."""
    return {"event_id": event.id, "incident_id": event.incident_id, "type": event.type,
            "payload": dict(event.payload), "created_at": event.created_at}


class EventBus:
    def __init__(self, repo: Repository, publisher: RealtimePublisher,
                 clock: Callable[[], datetime] = _now) -> None:
        self.repo = repo
        self.publisher = publisher
        self._clock = clock
        self.publish_failures = 0

    def build(self, incident_id: str, type: str, payload: Mapping[str, Any] | None = None) -> EventRecord:
        now = self._clock()
        return EventRecord(
            event_id=new_id("event"), incident_id=incident_id, type=type,
            payload=dict(payload or {}), created_at=now,
            ttl=int((now + EVENT_TTL).timestamp()),
        )

    def event_op(self, record: EventRecord) -> TxOp:
        """TxOp that stores the Events row; include it in the caller's own ``transact``."""
        item = record.model_dump(mode="json")
        return TxOp(kind="put", table=EVENTS_TABLE, if_absent=True,
                    item={**item, "pk": record.incident_id, "sk": record.event_id})

    def publish(self, record: EventRecord) -> bool:
        """Best-effort publish of a committed event. Never raises."""
        try:
            self.publisher.publish(record.incident_id, _public_event(record))
            return True
        except Exception:
            self.publish_failures += 1
            log.exception("realtime publish failed for event %s", record.event_id)
            return False

    def commit(self, ops: Sequence[TxOp], incident_id: str, type: str,
               payload: Mapping[str, Any] | None = None) -> EventRecord:
        """Atomically apply ``ops`` plus the Events row, then publish.

        Persistence errors (Conflict, RepositoryUnavailable) propagate and nothing is published.
        """
        record = self.build(incident_id, type, payload)
        self.repo.transact([*ops, self.event_op(record)])
        self.publish(record)
        return record

    def since(self, incident_id: str, since: str | None = None, limit: int = 100) -> list[Event]:
        """Public views of events stored after ``since`` (event_id), oldest first."""
        rows = self.repo.query(EVENTS_TABLE, {"pk": incident_id}, since=since or None, limit=limit)
        now = int(self._clock().timestamp())
        out: list[Event] = []
        for row in rows:
            data = {k: v for k, v in row.items() if k not in ("pk", "sk", "version")}
            rec = EventRecord.model_validate(data)
            if rec.ttl is not None and rec.ttl < now:
                continue
            out.append(_public_event(rec))
        return out
