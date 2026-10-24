"""Incident and official-notice persistence (Req 11.2, 28.8, 30.7, 42.1, 42.3, 42.4).

Notices that can change routing bump the incident's ``overlay_version`` in the same transaction
as the notice row, the Events row and (for official notices) the audit entry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from app.adapters.ports import Conflict, Repository, RepositoryUnavailable, TxOp
from app.auth.audit import AUDIT_TABLE, OFFICIAL_NOTICE
from app.communication.events import EventBus
from app.models.domain import Incident, IncidentType, OfficialNotice
from app.models.ids import new_id
from app.models.records import AuditLogRecord
from app.services.overlays import (
    INCIDENTS_PK,
    INCIDENTS_TABLE,
    MAX_CAS_ATTEMPTS,
    NOTICES_TABLE,
    bump_ops,
    strip_keys,
)

SIMULATED_SOURCE_ID = "SIMULATED-DEMO-SOURCE"
SIMULATED_ORGANIZATION = "SafePath Demo (SIMULATED, not an official source)"
SIMULATED_URL = "https://safepath.invalid/simulated"

INCIDENT_CREATED = "incident_created"
NOTICE_PUBLISHED = "notice_published"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentNotFound(LookupError):
    pass


class IncidentService:
    def __init__(self, repo: Repository, bus: EventBus, clock: Callable[[], datetime] = _now,
                 on_change: Callable[[str], None] | None = None) -> None:
        self.repo = repo
        self.bus = bus
        self._clock = clock
        self.on_change = on_change

    # ---------------------------------------------------------------- incidents
    def create_incident(self, incident_type: IncidentType, *, source: str,
                        geographic_boundary: dict[str, Any] | None = None,
                        simulation_flag: bool = False, scenario_id: str | None = None,
                        incident_id: str | None = None) -> Incident:
        now = self._clock()
        inc = Incident(
            incident_id=incident_id or new_id("incident"), incident_type=incident_type,
            geographic_boundary=geographic_boundary, status="active", source=source,
            created_at=now, updated_at=now, simulation_flag=simulation_flag, scenario_id=scenario_id,
        )
        item = {**inc.model_dump(mode="json"), "pk": INCIDENTS_PK, "sk": inc.incident_id}
        self.bus.commit(
            [TxOp(kind="put", table=INCIDENTS_TABLE, item=item, if_absent=True)],
            inc.incident_id, INCIDENT_CREATED,
            {"incident_type": inc.incident_type.value, "simulation_flag": inc.simulation_flag},
        )
        if self.on_change:
            self.on_change(inc.incident_id)
        return inc

    def get_incident(self, incident_id: str) -> Incident | None:
        row = self.repo.get(INCIDENTS_TABLE, {"pk": INCIDENTS_PK, "sk": incident_id})
        return Incident.model_validate(strip_keys(row)) if row else None

    def list_incidents(self) -> list[Incident]:
        rows = self.repo.query(INCIDENTS_TABLE, {"pk": INCIDENTS_PK}, limit=1000)
        return [Incident.model_validate(strip_keys(r)) for r in rows]

    # ------------------------------------------------------------------ notices
    def create_notice(self, incident_id: str, *, notice_type: str, text: str,
                      source_organization: str, source_url: str, source_record_id: str,
                      geometry: dict[str, Any] | None = None, edge_ids: list[str] | None = None,
                      effective_at: datetime | None = None, expires_at: datetime | None = None,
                      published_at: datetime | None = None, simulation_flag: bool = False,
                      verification_status: str = "Officially_Verified",
                      audit_actor: tuple[str, str] | None = None) -> OfficialNotice:
        """Persist a notice, bump overlay_version, write the event (and audit) atomically."""
        incident = self.get_incident(incident_id)
        if incident is None:
            raise IncidentNotFound(incident_id)
        now = self._clock()
        notice = OfficialNotice(
            notice_id=new_id("notice"), incident_id=incident_id, notice_type=notice_type,  # type: ignore[arg-type]
            text=text, geometry=geometry, edge_ids=edge_ids or [],
            source_organization=source_organization, source_url=source_url,
            source_record_id=source_record_id, effective_at=effective_at or now,
            expires_at=expires_at, published_at=published_at or now, retrieved_at=now,
            verification_status=verification_status,
            simulation_flag=simulation_flag or incident.simulation_flag,
        )
        item = {**notice.model_dump(mode="json"), "pk": incident_id, "sk": notice.notice_id}
        for _ in range(MAX_CAS_ATTEMPTS):
            version_ops, version = bump_ops(self.repo, incident_id, now)
            ops = [TxOp(kind="put", table=NOTICES_TABLE, item=item, if_absent=True), *version_ops]
            if audit_actor is not None:
                ops.append(self._audit_op(audit_actor, notice.notice_id, now))
            try:
                self.bus.commit(ops, incident_id, NOTICE_PUBLISHED, {
                    "notice_id": notice.notice_id, "notice_type": notice_type,
                    "overlay_version": version, "simulation_flag": notice.simulation_flag,
                })
            except Conflict:
                continue
            if self.on_change:
                self.on_change(incident_id)
            return notice
        raise RepositoryUnavailable("could not update overlay_version under contention")

    def create_simulated_notice(self, incident_id: str, *, notice_type: str, text: str,
                                **kwargs: Any) -> OfficialNotice:
        """Demo-mode notice (task 11.4): always flagged simulated with an obviously simulated source."""
        kwargs.pop("simulation_flag", None)
        return self.create_notice(
            incident_id, notice_type=notice_type, text=text,
            source_organization=SIMULATED_ORGANIZATION, source_url=SIMULATED_URL,
            source_record_id=SIMULATED_SOURCE_ID, simulation_flag=True, **kwargs,
        )

    def list_notices(self, incident_id: str) -> list[OfficialNotice]:
        rows = self.repo.query(NOTICES_TABLE, {"pk": incident_id}, limit=10_000)
        return [OfficialNotice.model_validate(strip_keys(r)) for r in rows]

    @staticmethod
    def _audit_op(actor: tuple[str, str], notice_id: str, now: datetime) -> TxOp:
        actor_id, actor_role = actor
        rec = AuditLogRecord(
            audit_id=new_id("audit"), day=now.astimezone(timezone.utc).date().isoformat(),
            ts=now, actor_id=actor_id, actor_role=actor_role, action=OFFICIAL_NOTICE,
            target_id=notice_id,
        )
        return TxOp(kind="put", table=AUDIT_TABLE, if_absent=True,
                    item={**rec.model_dump(mode="json"), "pk": rec.day, "sk": rec.audit_id})
