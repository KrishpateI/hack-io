"""Overlay store, ``overlay_version`` counter and the Repository-backed routing ContextProvider.

Tables (default ``pk``/``sk`` keys):

* ``incidents``     pk="all",          sk=incident_id
* ``notices``       pk=incident_id,    sk=notice_id
* ``overlays``      pk=incident_id,    sk=restriction_id   (``OverlayRecord``)
* ``overlay_meta``  pk=incident_id,    sk="meta"           (``overlay_version`` counter)
* ``reports``       pk=incident_id,    sk=report_id        (written by the reports service)

Every restriction change (add, remove, activate) updates the overlay row, bumps the incident's
``overlay_version`` with a CAS, and writes the Events row (``restriction_added`` /
``restriction_removed``) in ONE ``Repository.transact`` through the EventBus, so a reader that
sees a new version always sees the new overlays and the event (Req 14.7, 16.3, 30.9, 42.7).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from app.adapters.ports import Conflict, Repository, RepositoryUnavailable, TxOp
from app.communication.events import EventBus
from app.models.domain import HazardReport, Incident, OfficialNotice, Restriction, VerificationStatus
from app.models.ids import new_id
from app.models.records import OverlayRecord
from app.routing.service import ContextData, OverlayUnavailable

log = logging.getLogger("safepath.overlays")

INCIDENTS_TABLE = "incidents"
INCIDENTS_PK = "all"
NOTICES_TABLE = "notices"
OVERLAYS_TABLE = "overlays"
META_TABLE = "overlay_meta"
META_SK = "meta"
REPORTS_TABLE = "reports"

RESTRICTION_ADDED = "restriction_added"
RESTRICTION_REMOVED = "restriction_removed"

#: Upper bound on how stale the cached overlay_version read may be (Req 42.7 design: <= 2 s).
VERSION_TTL_SECONDS = 2.0
MAX_CAS_ATTEMPTS = 8
_ROW_KEYS = ("pk", "sk", "version")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def strip_keys(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in _ROW_KEYS}


class RestrictionNotFound(LookupError):
    pass


class DuplicateRestriction(ValueError):
    pass


def read_overlay_meta(repo: Repository, incident_id: str) -> tuple[int, str | None]:
    """(overlay_version, updated_at iso) for an incident; (0, None) before any change."""
    row = repo.get(META_TABLE, {"pk": incident_id, "sk": META_SK})
    if row is None:
        return 0, None
    return int(row.get("overlay_version", 0)), row.get("updated_at")


def bump_ops(repo: Repository, incident_id: str, now: datetime) -> tuple[list[TxOp], int]:
    """TxOps that increment the incident's overlay_version; returns (ops, new_version).

    The caller adds them to its own ``transact`` so the bump is atomic with the change. A lost
    CAS race surfaces as ``Conflict`` from ``transact``; callers retry from a fresh read.
    """
    row = repo.get(META_TABLE, {"pk": incident_id, "sk": META_SK})
    stamp = now.isoformat()
    if row is None:
        item = {"pk": incident_id, "sk": META_SK, "overlay_version": 1, "version": 1,
                "updated_at": stamp}
        return [TxOp(kind="put", table=META_TABLE, item=item, if_absent=True)], 1
    new = int(row.get("overlay_version", 0)) + 1
    op = TxOp(kind="update_cas", table=META_TABLE, key={"pk": incident_id, "sk": META_SK},
              expected_version=int(row.get("version", 0)),
              patch={"overlay_version": new, "updated_at": stamp})
    return [op], new


def get_incident_row(repo: Repository, incident_id: str) -> dict | None:
    """The stored incident row (shared layout: pk="all", sk=incident_id) or None."""
    return repo.get(INCIDENTS_TABLE, {"pk": INCIDENTS_PK, "sk": incident_id})


@dataclass(frozen=True)
class PreparedChange:
    """A restriction change expressed as TxOps (overlay row + overlay_version bump) plus the
    event to write, so a caller can commit it atomically with its own state change."""

    ops: list[TxOp]
    event_type: str
    payload: dict[str, Any]
    restriction_id: str
    overlay_version: int


class OverlayService:
    """Add/remove/activate restrictions for an incident, returning the new ``overlay_version``."""

    # ------------------------------------------------------- composable (no commit)
    def prepare_add_closure(
        self, incident_id: str, *, edge_ids: Iterable[str], source_report_id: str | None = None,
        simulated: bool = False, kind: str = "closure", restriction_id: str | None = None,
    ) -> PreparedChange:
        """Ops that add an active restriction and bump overlay_version; nothing is written.

        The caller puts the ops in its own ``transact`` and retries from fresh reads on Conflict.
        """
        rid = restriction_id or new_id("overlay")
        rec = OverlayRecord(
            restriction_id=rid, incident_id=incident_id, kind=kind,  # type: ignore[arg-type]
            edge_ids=list(edge_ids), geometry=None, source_report_id=source_report_id,
            source_record_id=None, simulated=simulated, active=True, status="active",
            created_at=self._clock(),
        )
        item = {**rec.model_dump(mode="json"), "pk": incident_id, "sk": rid}
        version_ops, new_version = bump_ops(self.repo, incident_id, self._clock())
        payload = {"restriction_id": rid, "overlay_version": new_version, "kind": rec.kind,
                   "edge_ids": rec.edge_ids, "simulated": simulated}
        if source_report_id:
            payload["report_id"] = source_report_id
        return PreparedChange(
            [TxOp(kind="put", table=OVERLAYS_TABLE, item=item, if_absent=True), *version_ops],
            RESTRICTION_ADDED, payload, rid, new_version)

    def prepare_remove_closure(self, incident_id: str, restriction_id: str) -> PreparedChange | None:
        """Ops that mark an active restriction removed and bump overlay_version.

        Returns None when the restriction is missing or already removed (nothing to do)."""
        row = self.repo.get(OVERLAYS_TABLE, {"pk": incident_id, "sk": restriction_id})
        if row is None:
            return None
        rec = OverlayRecord.model_validate(strip_keys(row))
        if rec.status != "active":
            return None
        now = self._clock()
        rec = rec.model_copy(update={"active": False, "status": "removed", "removed_at": now})
        item = {**rec.model_dump(mode="json"), "pk": incident_id, "sk": restriction_id}
        version_ops, new_version = bump_ops(self.repo, incident_id, now)
        payload = {"restriction_id": restriction_id, "overlay_version": new_version}
        if rec.source_report_id:
            payload["report_id"] = rec.source_report_id
        return PreparedChange([TxOp(kind="put", table=OVERLAYS_TABLE, item=item), *version_ops],
                              RESTRICTION_REMOVED, payload, restriction_id, new_version)

    def __init__(self, repo: Repository, bus: EventBus,
                 clock: Callable[[], datetime] = _now,
                 on_change: Callable[[str], None] | None = None) -> None:
        self.repo = repo
        self.bus = bus
        self._clock = clock
        self.on_change = on_change  # e.g. RepositoryContextProvider.invalidate

    # ------------------------------------------------------------------ writes
    def add_closure(
        self, incident_id: str, *, edge_ids: Iterable[str] = (), geometry: dict[str, Any] | None = None,
        kind: str = "closure", source_record_id: str | None = None,
        source_report_id: str | None = None, simulated: bool = False,
        restriction_id: str | None = None,
    ) -> int:
        """Persist a new active restriction; returns the incident's new overlay_version."""
        rid = restriction_id or new_id("overlay")
        now = self._clock()
        rec = OverlayRecord(
            restriction_id=rid, incident_id=incident_id, kind=kind,  # type: ignore[arg-type]
            edge_ids=list(edge_ids), geometry=geometry, source_report_id=source_report_id,
            source_record_id=source_record_id, simulated=simulated, active=True,
            status="active", created_at=now,
        )
        item = {**rec.model_dump(mode="json"), "pk": incident_id, "sk": rid}

        def build(version_ops: list[TxOp]) -> list[TxOp]:
            if self.repo.get(OVERLAYS_TABLE, {"pk": incident_id, "sk": rid}) is not None:
                raise DuplicateRestriction(f"restriction '{rid}' already exists")
            return [TxOp(kind="put", table=OVERLAYS_TABLE, item=item, if_absent=True), *version_ops]

        return self._change(incident_id, RESTRICTION_ADDED, rid, build, rec)

    def remove_closure(self, incident_id: str, restriction_id: str) -> int:
        """Mark a restriction removed (kept for history); returns the new overlay_version."""
        return self._set_status(incident_id, restriction_id, active=False)

    deactivate_closure = remove_closure

    def activate_closure(self, incident_id: str, restriction_id: str) -> int:
        return self._set_status(incident_id, restriction_id, active=True)

    def _set_status(self, incident_id: str, restriction_id: str, *, active: bool) -> int:
        now = self._clock()

        def build(version_ops: list[TxOp]) -> list[TxOp]:
            row = self.repo.get(OVERLAYS_TABLE, {"pk": incident_id, "sk": restriction_id})
            if row is None:
                raise RestrictionNotFound(restriction_id)
            rec = OverlayRecord.model_validate(strip_keys(row)).model_copy(update={
                "active": active, "status": "active" if active else "removed",
                "removed_at": None if active else now,
            })
            item = {**rec.model_dump(mode="json"), "pk": incident_id, "sk": restriction_id}
            return [TxOp(kind="put", table=OVERLAYS_TABLE, item=item), *version_ops]

        return self._change(incident_id, RESTRICTION_ADDED if active else RESTRICTION_REMOVED,
                            restriction_id, build, None)

    def _change(self, incident_id: str, event_type: str, restriction_id: str,
                build: Callable[[list[TxOp]], list[TxOp]], rec: OverlayRecord | None) -> int:
        for _ in range(MAX_CAS_ATTEMPTS):
            version_ops, new_version = bump_ops(self.repo, incident_id, self._clock())
            ops = build(version_ops)
            payload = {"restriction_id": restriction_id, "overlay_version": new_version}
            if rec is not None:
                payload.update(kind=rec.kind, edge_ids=rec.edge_ids, simulated=rec.simulated)
            try:
                self.bus.commit(ops, incident_id, event_type, payload)
            except Conflict:
                continue  # lost the version race; re-read and retry
            if self.on_change is not None:
                self.on_change(incident_id)
            return new_version
        raise RepositoryUnavailable("could not update overlay_version under contention")

    # ------------------------------------------------------------------- reads
    def overlay_version(self, incident_id: str) -> int:
        return read_overlay_meta(self.repo, incident_id)[0]

    def list_overlays(self, incident_id: str, *, active_only: bool = True) -> list[OverlayRecord]:
        rows = self.repo.query(OVERLAYS_TABLE, {"pk": incident_id}, limit=10_000)
        out = [OverlayRecord.model_validate(strip_keys(r)) for r in rows]
        return [o for o in out if o.status == "active"] if active_only else out


class RepositoryContextProvider:
    """``ContextProvider`` reading incidents, notices, reports and overlays from the Repository.

    The overlay_version is cached per incident for at most ``version_ttl`` seconds (<= 2 s);
    writes made through this process call :meth:`invalidate` so they are visible immediately.
    Any Repository failure raises ``OverlayUnavailable`` so routing fails closed with 503.
    """

    def __init__(self, repo: Repository, *, clock: Callable[[], datetime] = _now,
                 version_ttl: float = VERSION_TTL_SECONDS,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        if not 0 <= version_ttl <= VERSION_TTL_SECONDS:
            raise ValueError("version_ttl must be between 0 and 2 seconds")
        self.repo = repo
        self._clock = clock
        self._ttl = version_ttl
        self._mono = monotonic
        self._lock = threading.Lock()
        self._versions: dict[str, tuple[float, int, str | None]] = {}

    def invalidate(self, incident_id: str | None = None) -> None:
        with self._lock:
            if incident_id is None:
                self._versions.clear()
            else:
                self._versions.pop(incident_id, None)

    def _meta(self, incident_id: str) -> tuple[int, str | None]:
        now = self._mono()
        with self._lock:
            hit = self._versions.get(incident_id)
            if hit is not None and now - hit[0] < self._ttl:
                return hit[1], hit[2]
        version, stamp = read_overlay_meta(self.repo, incident_id)
        with self._lock:
            self._versions[incident_id] = (now, version, stamp)
        return version, stamp

    # --------------------------------------------------------------- protocol
    def incident_exists(self, incident_id: str) -> bool:
        try:
            return self.repo.get(INCIDENTS_TABLE, {"pk": INCIDENTS_PK, "sk": incident_id}) is not None
        except RepositoryUnavailable as exc:
            raise OverlayUnavailable(str(exc)) from exc

    def load(self, incident_id: str, now: datetime) -> ContextData | None:
        try:
            row = self.repo.get(INCIDENTS_TABLE, {"pk": INCIDENTS_PK, "sk": incident_id})
            if row is None:
                return None
            # Version is read BEFORE the rows: the rows are never older than the version.
            version, stamp = self._meta(incident_id)
            notices = tuple(OfficialNotice.model_validate(strip_keys(r)) for r in
                            self.repo.query(NOTICES_TABLE, {"pk": incident_id}, limit=10_000))
            overlays = tuple(_as_restriction(o) for o in
                             (OverlayRecord.model_validate(strip_keys(r)) for r in
                              self.repo.query(OVERLAYS_TABLE, {"pk": incident_id}, limit=10_000))
                             if o.status == "active" and o.active)
            reports = self._reports(incident_id, now)
            incident = Incident.model_validate(strip_keys(row))
        except RepositoryUnavailable as exc:
            raise OverlayUnavailable(str(exc)) from exc
        except (ValueError, KeyError) as exc:  # unreadable overlay data: fail closed
            raise OverlayUnavailable(f"stored incident data invalid: {exc}") from exc
        stamps = [t for t in (_parse(stamp), *(n.retrieved_at for n in notices),
                              *(r.created_at for r in reports), incident.updated_at) if t]
        return ContextData(
            incident=incident, notices=notices, reports=reports, overlays=overlays,
            overlay_version=version, data_timestamp=max(stamps) if stamps else None,
        )

    def _reports(self, incident_id: str, now: datetime) -> tuple[HazardReport, ...]:
        rows = self.repo.query(REPORTS_TABLE, {"pk": incident_id}, limit=10_000)
        fields = set(HazardReport.model_fields)
        out: list[HazardReport] = []
        for r in rows:
            try:
                rep = HazardReport.model_validate({k: v for k, v in r.items() if k in fields})
            except ValueError:
                log.warning("skipping unreadable report %s", r.get("sk"))
                continue
            if rep.verification_status in (VerificationStatus.RESOLVED, VerificationStatus.EXPIRED):
                continue
            if rep.expires_at <= now:
                continue
            out.append(rep)
        return tuple(out)


def _as_restriction(o: OverlayRecord) -> Restriction:
    return Restriction(
        restriction_id=o.restriction_id, incident_id=o.incident_id, kind=o.kind,
        edge_ids=o.edge_ids, geometry=o.geometry, source_report_id=o.source_report_id,
        source_record_id=o.source_record_id, simulated=o.simulated, active=True,
    )


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None
