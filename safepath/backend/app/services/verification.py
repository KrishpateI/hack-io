"""Report verification lifecycle and moderation (Req 24.1-24.10, 28.6, 28.7, 16.3, 16.5).

State machine (design "Hazard report verification state machine")::

    Unverified/Corroborated/Disputed -> Officially_Verified   (moderate "verify" only)
    Unverified -> Corroborated        confirmations >= threshold (distinct, non-reporter)
    Unverified/Corroborated -> Disputed   disputes >= threshold
    Disputed -> Corroborated          confirmations >= threshold and disputes < threshold
    any non-terminal -> Resolved      moderate "resolve"
    any non-terminal -> Expired       now > expires_at (``expire_due`` / lazy checks)

Corroboration can never produce ``Officially_Verified``. Every change is one
``Repository.transact`` with a CAS on the report ``version``: the report update, status history
entry, vote row, overlay row + ``overlay_version`` bump (verify/resolve/expire), audit entry and
the events. A lost CAS race is re-read and retried, so concurrent changes apply sequentially in
receipt order; persistent contention surfaces as ``ConcurrentUpdate`` (HTTP 409).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.adapters.ports import Conflict, Principal, Repository, Role, TxOp
from app.auth.audit import AUDIT_TABLE
from app.communication.events import EventBus
from app.models.domain import VerificationStatus as VS
from app.models.ids import new_id
from app.models.records import AuditLogRecord, HazardReportRecord, StatusHistoryEntry
from app.services.overlays import OverlayService, PreparedChange, get_incident_row
from app.services.reports import REPORTS_TABLE, ReportService

log = logging.getLogger(__name__)

VOTES_TABLE = "report_votes"
MAX_ATTEMPTS = 8
DEFAULT_CORROBORATION_THRESHOLD = 2
DEFAULT_DISPUTE_THRESHOLD = 2

REPORT_STATUS_CHANGED = "report_status_changed"
REPORT_VOTED = "report_voted"

SIMULATED_MODERATOR_SOURCE = "Simulated moderator verification"
OFFICIAL_SOURCE = "Authorized official verification"
CORROBORATED_TEXT = "Corroborated by community \u2014 not officially verified"

_TERMINAL = {VS.RESOLVED, VS.EXPIRED}
_VERIFIABLE = {VS.UNVERIFIED, VS.CORROBORATED, VS.DISPUTED}
MODERATE_ACTIONS = ("verify", "resolve")


class ReportNotFound(LookupError):
    pass


class OwnReportVote(PermissionError):
    """A reporter tried to confirm or dispute their own report (HTTP 403)."""


class DuplicateVote(Exception):
    """The user already voted on this report (HTTP 409)."""


class InvalidTransition(Exception):
    """The requested change is not allowed from the current status (HTTP 409)."""


class ConcurrentUpdate(Exception):
    """The report changed concurrently and the change could not be applied (HTTP 409)."""


class NotPermitted(PermissionError):
    """The actor's role may not perform this change (HTTP 403)."""


class InvalidAction(ValueError):
    pass


@dataclass(frozen=True)
class VoteResult:
    report: HazardReportRecord
    status_changed: bool


@dataclass(frozen=True)
class ModerationResult:
    report: HazardReportRecord
    overlay_version: int | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _record(row: dict) -> HazardReportRecord:
    return HazardReportRecord.model_validate({k: v for k, v in row.items() if k not in ("pk", "sk")})


class VerificationService:
    def __init__(self, reports: ReportService, bus: EventBus, overlays: OverlayService,
                 clock: Callable[[], datetime] = _now,
                 corroboration_threshold: int = DEFAULT_CORROBORATION_THRESHOLD,
                 dispute_threshold: int = DEFAULT_DISPUTE_THRESHOLD) -> None:
        self.reports = reports
        self.repo: Repository = reports.repo
        self.bus = bus
        self.overlays = overlays
        self._clock = clock
        self.corroboration_threshold = corroboration_threshold
        self.dispute_threshold = dispute_threshold

    # ------------------------------------------------------------------ reads
    def find_row(self, report_id: str) -> dict:
        rows = self.repo.query(REPORTS_TABLE, {"report_id": report_id}, index="report-id", limit=1)
        if not rows:
            raise ReportNotFound(report_id)
        return rows[0]

    def get_report(self, report_id: str) -> HazardReportRecord:
        rec = _record(self.find_row(report_id))
        if rec.expires_at <= self._clock() and rec.verification_status not in _TERMINAL:
            rec = rec.model_copy(update={"verification_status": VS.EXPIRED})
        return rec

    def history_view(self, rec: HazardReportRecord) -> list[dict[str, Any]]:
        """Status history for public display: actor alias and role, never ids."""
        return [h.model_dump(mode="json") for h in rec.status_history]

    # ------------------------------------------------------------------ helpers
    def _audit_op(self, principal: Principal, action: str, target_id: str, now: datetime) -> TxOp:
        rec = AuditLogRecord(
            audit_id=new_id("audit"), day=now.astimezone(timezone.utc).date().isoformat(), ts=now,
            actor_id=principal.subject, actor_role=principal.role.value, action=action,
            target_id=target_id)
        return TxOp(kind="put", table=AUDIT_TABLE, if_absent=True,
                    item={**rec.model_dump(mode="json"), "pk": rec.day, "sk": rec.audit_id})

    def _commit(self, ops: list[TxOp], incident_id: str, events: list[tuple[str, dict]]) -> None:
        """One transact for ops + all event rows; publish only after it commits."""
        records = [self.bus.build(incident_id, t, p) for t, p in events]
        self.repo.transact([*ops, *(self.bus.event_op(r) for r in records)])
        for r in records:
            self.bus.publish(r)

    @staticmethod
    def _cas_op(row: dict, patch: dict) -> TxOp:
        return TxOp(kind="update_cas", table=REPORTS_TABLE,
                    key={"pk": row["pk"], "sk": row["sk"]},
                    expected_version=int(row.get("version", 0)), patch=patch)

    def _history(self, row: dict, entry: StatusHistoryEntry) -> list[dict]:
        return [*row.get("status_history", []), entry.model_dump(mode="json")]

    def _is_expired(self, rec: HazardReportRecord, now: datetime) -> bool:
        return rec.verification_status == VS.EXPIRED or (
            rec.verification_status not in _TERMINAL and rec.expires_at <= now)

    # ------------------------------------------------------------------ votes
    def vote(self, principal: Principal, report_id: str, kind: str) -> VoteResult:
        """Record a ``confirm`` or ``dispute`` vote. 403 own report, 409 repeat or inactive."""
        if kind not in ("confirm", "dispute"):
            raise InvalidAction(kind)
        for _ in range(MAX_ATTEMPTS):
            row = self.find_row(report_id)
            rec = _record(row)
            now = self._clock()
            if rec.reporter_id == principal.subject:
                raise OwnReportVote(report_id)
            if self.repo.get(VOTES_TABLE, {"pk": report_id, "sk": principal.subject}) is not None:
                raise DuplicateVote(report_id)
            if self._is_expired(rec, now) or rec.verification_status in _TERMINAL:
                raise InvalidTransition(f"report is {rec.verification_status.value}")

            conf = rec.confirmations + (kind == "confirm")
            disp = rec.disputes + (kind == "dispute")
            old = rec.verification_status
            new = self._next_status(old, conf, disp)
            patch: dict[str, Any] = {"confirmations": conf, "disputes": disp,
                                     "updated_at": now.isoformat()}
            changed = new != old
            if changed:
                patch["verification_status"] = new.value
                patch["status_history"] = self._history(row, StatusHistoryEntry(
                    status=new.value, at=now, actor_role=principal.role.value,
                    previous_status=old.value, actor_alias=self.reports.alias_for(
                        principal.subject, rec.incident_id),
                    reason=("corroboration threshold reached" if new == VS.CORROBORATED
                            else "dispute threshold reached")))
            ops = [
                TxOp(kind="put", table=VOTES_TABLE, if_absent=True, item={
                    "pk": report_id, "sk": principal.subject, "report_id": report_id,
                    "voter_id": principal.subject, "vote": kind, "at": now.isoformat()}),
                self._cas_op(row, patch),
                self._audit_op(principal, f"report_{kind}", report_id, now),
            ]
            payload = {"report_id": report_id, "verification_status": new.value,
                       "previous_status": old.value, "confirmations": conf, "disputes": disp}
            try:
                self._commit(ops, rec.incident_id,
                             [(REPORT_STATUS_CHANGED if changed else REPORT_VOTED, payload)])
            except Conflict:
                continue  # lost a race (or a duplicate vote); re-read and re-evaluate
            return VoteResult(_record(self.find_row(report_id)), changed)
        raise ConcurrentUpdate(report_id)

    def _next_status(self, old: VS, conf: int, disp: int) -> VS:
        c_ok = conf >= self.corroboration_threshold
        d_ok = disp >= self.dispute_threshold
        if old == VS.UNVERIFIED:
            if d_ok:
                return VS.DISPUTED
            return VS.CORROBORATED if c_ok else old
        if old == VS.CORROBORATED:
            return VS.DISPUTED if d_ok else old
        if old == VS.DISPUTED:
            return VS.CORROBORATED if (c_ok and not d_ok) else old
        return old  # Officially_Verified, Resolved, Expired never change by voting

    # --------------------------------------------------------------- moderation
    def moderate(self, principal: Principal, report_id: str, action: str, *,
                 reason: str | None = None, expected_version: int | None = None) -> ModerationResult:
        if action not in MODERATE_ACTIONS:
            raise InvalidAction(action)
        if principal.role not in (Role.MODERATOR, Role.AUTHORIZED_OFFICIAL):
            raise NotPermitted("only a Moderator or Authorized_Official may moderate reports")
        for _ in range(MAX_ATTEMPTS):
            row = self.find_row(report_id)
            rec = _record(row)
            if expected_version is not None and rec.version != expected_version:
                raise ConcurrentUpdate(f"report version is {rec.version}, expected {expected_version}")
            now = self._clock()
            old = rec.verification_status
            if self._is_expired(rec, now) or old in _TERMINAL:
                raise InvalidTransition(f"report is {'Expired' if self._is_expired(rec, now) else old.value}")
            incident = get_incident_row(self.repo, rec.incident_id)
            demo = bool(incident and incident.get("simulation_flag", False)) or rec.simulated
            alias = self.reports.alias_for(principal.subject, rec.incident_id)

            patch: dict[str, Any] = {"updated_at": now.isoformat()}
            change: PreparedChange | None = None
            if action == "verify":
                if old not in _VERIFIABLE:
                    raise InvalidTransition(f"cannot verify a report that is {old.value}")
                if principal.role == Role.MODERATOR and not demo:
                    raise NotPermitted("only an Authorized_Official can verify a real incident report")
                new = VS.OFFICIALLY_VERIFIED
                source = (SIMULATED_MODERATOR_SOURCE if principal.role == Role.MODERATOR
                          else OFFICIAL_SOURCE)
                patch["source_category"] = source
                if rec.affected_edge_ids:
                    change = self.overlays.prepare_add_closure(
                        rec.incident_id, edge_ids=rec.affected_edge_ids, source_report_id=report_id,
                        simulated=demo)
                    patch["restriction_id"] = change.restriction_id
                default_reason = source
            else:  # resolve
                new = VS.RESOLVED
                if rec.restriction_id:
                    change = self.overlays.prepare_remove_closure(rec.incident_id, rec.restriction_id)
                default_reason = "resolved"
            patch["verification_status"] = new.value
            patch["status_history"] = self._history(row, StatusHistoryEntry(
                status=new.value, at=now, actor_role=principal.role.value, actor_alias=alias,
                previous_status=old.value, reason=reason or default_reason))

            ops = [self._cas_op(row, patch),
                   self._audit_op(principal, f"moderation:{action}", report_id, now)]
            events: list[tuple[str, dict]] = []
            if change is not None:
                ops.extend(change.ops)
                events.append((change.event_type, change.payload))
            events.append((REPORT_STATUS_CHANGED, {
                "report_id": report_id, "verification_status": new.value,
                "previous_status": old.value, "source_category": patch.get("source_category"),
                "simulated": demo and action == "verify",
                **({"overlay_version": change.overlay_version} if change else {})}))
            try:
                self._commit(ops, rec.incident_id, events)
            except Conflict:
                continue
            if change is not None and self.overlays.on_change is not None:
                self.overlays.on_change(rec.incident_id)
            return ModerationResult(_record(self.find_row(report_id)),
                                    change.overlay_version if change else None)
        raise ConcurrentUpdate(report_id)

    # ------------------------------------------------------------------- expiry
    def expire_due(self, incident_id: str) -> int:
        """Mark reports past ``expires_at`` Expired and remove their closure (Req 24.5).

        Returns the number of reports expired. Safe to call concurrently."""
        count = 0
        now = self._clock()
        for row in self.repo.query(REPORTS_TABLE, {"pk": incident_id}, limit=10_000):
            rec = _record(row)
            if rec.verification_status in _TERMINAL or rec.expires_at > now:
                continue
            for _ in range(MAX_ATTEMPTS):
                row = self.repo.get(REPORTS_TABLE, {"pk": row["pk"], "sk": row["sk"]}) or row
                rec = _record(row)
                if rec.verification_status in _TERMINAL:
                    break
                change = (self.overlays.prepare_remove_closure(incident_id, rec.restriction_id)
                          if rec.restriction_id else None)
                patch = {
                    "verification_status": VS.EXPIRED.value, "updated_at": now.isoformat(),
                    "status_history": self._history(row, StatusHistoryEntry(
                        status=VS.EXPIRED.value, at=now, actor_role="system",
                        previous_status=rec.verification_status.value, reason="expired"))}
                ops = [self._cas_op(row, patch)]
                events: list[tuple[str, dict]] = []
                if change is not None:
                    ops.extend(change.ops)
                    events.append((change.event_type, change.payload))
                events.append((REPORT_STATUS_CHANGED, {
                    "report_id": rec.report_id, "verification_status": VS.EXPIRED.value,
                    "previous_status": rec.verification_status.value}))
                try:
                    self._commit(ops, incident_id, events)
                except Conflict:
                    continue
                if change is not None and self.overlays.on_change is not None:
                    self.overlays.on_change(incident_id)
                count += 1
                break
        return count
