"""Audit log writer on the Repository port (Req 22.4, 28.7, 31.7, 42.3, 42.4).

Records who (actor id and role), what (action, target) and when. Table ``audit`` is keyed by
``pk = day`` and ``sk = audit_id`` (ULID, so time ordered within a day).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.adapters.ports import Repository
from app.models.ids import new_id
from app.models.records import AuditLogRecord

AUDIT_TABLE = "audit"

# Action names.
ROLE_ESCALATION_REJECTED = "role_escalation_rejected"
ROLE_CHANGE = "role_change"
MODERATION = "moderation"
OFFICIAL_NOTICE = "official_notice"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuditWriter:
    def __init__(self, repo: Repository, clock: Callable[[], datetime] = _now):
        self.repo = repo
        self._clock = clock

    def record(self, actor_id: str, actor_role: str, action: str,
               target_id: str | None = None) -> AuditLogRecord:
        ts = self._clock()
        rec = AuditLogRecord(
            audit_id=new_id("audit"), day=ts.astimezone(timezone.utc).date().isoformat(),
            ts=ts, actor_id=actor_id, actor_role=actor_role, action=action, target_id=target_id,
        )
        item = rec.model_dump(mode="json")
        self.repo.put(AUDIT_TABLE, {**item, "pk": rec.day, "sk": rec.audit_id}, if_absent=True)
        return rec

    def entries(self, day: str, limit: int = 100) -> list[AuditLogRecord]:
        rows = self.repo.query(AUDIT_TABLE, {"pk": day}, limit=limit)
        return [AuditLogRecord.model_validate({k: v for k, v in r.items() if k not in ("pk", "sk")})
                for r in rows]

    # Convenience wrappers for the audited operation kinds.
    def role_change(self, actor_id: str, actor_role: str, target_id: str) -> AuditLogRecord:
        return self.record(actor_id, actor_role, ROLE_CHANGE, target_id)

    def moderation(self, actor_id: str, actor_role: str, target_id: str, action: str = "") -> AuditLogRecord:
        return self.record(actor_id, actor_role, f"{MODERATION}:{action}" if action else MODERATION,
                           target_id)

    def official_notice(self, actor_id: str, actor_role: str, notice_id: str) -> AuditLogRecord:
        return self.record(actor_id, actor_role, OFFICIAL_NOTICE, notice_id)
