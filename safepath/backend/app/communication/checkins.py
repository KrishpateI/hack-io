"""CheckInService: safety check-ins with opt-in coarse location and bounded retention.

Privacy rules (Req 27.3, 27.4, 27.5, 31.6, 31.8, 31.9):

* Visibility defaults to ``private``; a private check-in is readable only by its owner and is
  never published to the (token-free) incident channel.
* A location is stored only when the user's privacy preference ``share_location`` is on AND the
  request sets ``share_location`` explicitly. It is rounded to the >= 200 m grid *before*
  storage, so exact coordinates are never persisted or published. It is public only when the
  check-in visibility is not ``private`` and ``location_expires_at`` has not passed.
* ``location_expires_at`` = ``created_at`` + retention (72 h). ``scrub_expired_locations`` (called
  by the Sweeper, task 9.4) removes the location while the check-in record remains.
* Public views carry only the per-incident alias, never ``user_id``.
* ``checkin_created`` is published (public view only) in the same transaction as the write, and
  only for ``public`` check-ins. ``incident-members`` check-ins are served through GET to
  authenticated principals because the realtime channel is readable without a token.

SafePath does not dispatch emergency services: "I need assistance" renders carry that notice and
separate 911 guidance (Req 27.6).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from app.adapters.ports import Conflict, Principal, Repository, TxOp
from app.communication.events import EventBus
from app.models.ids import new_id
from app.models.privacy import coarsen, reporter_alias
from app.models.records import CheckInRecord

CHECKINS_TABLE = "checkins"
LOCATION_INDEX_TABLE = "checkin_locations"  # pk "loc", sk "<expiry>#<checkin_id>"
LOCATION_INDEX_PK = "loc"
LOCATION_RETENTION = timedelta(hours=72)
VISIBILITIES = ("private", "incident-members", "public")
EVENT_CHECKIN_CREATED = "checkin_created"
EVENT_LOCATION_REMOVED = "checkin_location_removed"

NO_DISPATCH_NOTICE = (
    "SafePath does not dispatch emergency services, and this check-in does not alert "
    "responders or promise any rescue or response."
)
EMERGENCY_GUIDANCE = "For an immediate emergency, call 911 and follow official instructions."


class CheckInStatus(str, Enum):
    SAFE = "safe"
    NEED_ASSISTANCE = "need_assistance"
    CANNOT_REACH_DESTINATION = "cannot_reach_destination"
    ROUTE_BLOCKED = "route_blocked"


STATUS_LABELS: dict[CheckInStatus, str] = {
    CheckInStatus.SAFE: "I'm safe",
    CheckInStatus.NEED_ASSISTANCE: "I need assistance",
    CheckInStatus.CANNOT_REACH_DESTINATION: "I cannot reach my planned destination",
    CheckInStatus.ROUTE_BLOCKED: "My route is blocked",
}


def parse_status(raw: Any) -> CheckInStatus | None:
    """Accept the stable enum value or the exact display text; None when unknown."""
    if not isinstance(raw, str):
        return None
    for status, label in STATUS_LABELS.items():
        if raw == status.value or raw == label:
            return status
    return None


class CheckInError(Exception):
    """Validation (400) or unknown incident (404) failure."""

    def __init__(self, status: int, code: str, message: str, fields: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.fields = status, code, message, fields or []


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _strip(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in ("pk", "sk", "version")}


def render(view: Any) -> dict[str, Any]:
    """JSON-ready dict of a CheckIn public/private view plus display text and assistance notice."""
    data = view.model_dump(mode="json")
    status = parse_status(data.get("status"))
    data["status_label"] = STATUS_LABELS[status] if status else data.get("status")
    if status is CheckInStatus.NEED_ASSISTANCE:
        data["notice"] = NO_DISPATCH_NOTICE
        data["emergency_guidance"] = EMERGENCY_GUIDANCE
    return data


class CheckInService:
    def __init__(self, repo: Repository, bus: EventBus, accounts: Any,
                 incident_exists: Callable[[str], bool],
                 clock: Callable[[], datetime] = _utc_now,
                 retention: timedelta = LOCATION_RETENTION) -> None:
        self.repo = repo
        self.bus = bus
        self.accounts = accounts
        self.incident_exists = incident_exists
        self._clock = clock
        self.retention = retention

    # ---------------------------------------------------------------- validation
    def _validate(self, body: dict[str, Any]) -> tuple[str, CheckInStatus, str, tuple[float, float] | None, bool]:
        fields: list[dict[str, str]] = []
        incident_id = body.get("incident_id")
        if not isinstance(incident_id, str) or not incident_id.strip() or len(incident_id) > 64:
            fields.append({"field": "incident_id", "problem": "required, 1 to 64 characters"})
        status = parse_status(body.get("status"))
        if status is None:
            fields.append({"field": "status", "problem": "must be one of: " + ", ".join(
                f"{s.value} ({STATUS_LABELS[s]})" for s in CheckInStatus)})
        visibility = body.get("visibility", "private")
        if visibility not in VISIBILITIES:
            fields.append({"field": "visibility", "problem": "must be one of: " + ", ".join(VISIBILITIES)})
        share = body.get("share_location", False)
        if not isinstance(share, bool):
            fields.append({"field": "share_location", "problem": "must be a boolean"})
            share = False
        loc_raw = body.get("location")
        location: tuple[float, float] | None = None
        if loc_raw is not None:
            lat = loc_raw.get("latitude") if isinstance(loc_raw, dict) else None
            lon = loc_raw.get("longitude") if isinstance(loc_raw, dict) else None
            ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                     for v in (lat, lon)) and -90 <= lat <= 90 and -180 <= lon <= 180
            if ok:
                location = (float(lat), float(lon))
            else:
                fields.append({"field": "location", "problem": "latitude in [-90, 90] and longitude in [-180, 180]"})
        if share and loc_raw is None and not fields:
            fields.append({"field": "location", "problem": "required when share_location is true"})
        if fields:
            raise CheckInError(400, "validation_error",
                               "invalid fields: " + ", ".join(f["field"] for f in fields), fields)
        assert status is not None
        return incident_id, status, visibility, location, share

    # ---------------------------------------------------------------- create
    def create(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        incident_id, status, visibility, location, share = self._validate(body)
        if not self.incident_exists(incident_id):
            raise CheckInError(404, "not_found", f"incident '{incident_id}' not found")
        user = self.accounts.ensure_user(principal.subject, principal.role)
        now = self._clock()
        stored_loc: tuple[float, float] | None = None
        expires: datetime | None = None
        if share and location is not None and user.privacy_preferences.share_location:
            stored_loc = coarsen(*location)  # exact coordinates are never stored
            expires = now + self.retention
        rec = CheckInRecord(
            checkin_id=new_id("checkin"), incident_id=incident_id, user_id=principal.subject,
            alias=reporter_alias(user.alias_seed or principal.subject, principal.subject, incident_id),
            status=status.value, created_at=now, optional_location=stored_loc,
            visibility=visibility, location_expires_at=expires,
        )
        ops = [TxOp(kind="put", table=CHECKINS_TABLE, if_absent=True, item=self._item(rec))]
        if expires is not None:
            ops.append(self._index_op(rec))
        if visibility == "public":
            self.bus.commit(ops, incident_id, EVENT_CHECKIN_CREATED, render(rec.public_view(now)))
        else:
            self.repo.transact(ops)
        return {
            "checkin": render(rec.private_view()),
            "current_status": True,
            "location_shared": stored_loc is not None,
            **({"notice": NO_DISPATCH_NOTICE, "emergency_guidance": EMERGENCY_GUIDANCE}
               if status is CheckInStatus.NEED_ASSISTANCE else {}),
        }

    @staticmethod
    def _item(rec: CheckInRecord) -> dict[str, Any]:
        return {**rec.model_dump(mode="json"), "pk": rec.incident_id,
                "sk": f"{_stamp(rec.created_at)}#{rec.checkin_id}"}

    @staticmethod
    def _index_op(rec: CheckInRecord) -> TxOp:
        assert rec.location_expires_at is not None
        sk = f"{_stamp(rec.location_expires_at)}#{rec.checkin_id}"
        return TxOp(kind="put", table=LOCATION_INDEX_TABLE, if_absent=True, item={
            "pk": LOCATION_INDEX_PK, "sk": sk, "incident_id": rec.incident_id,
            "checkin_sk": f"{_stamp(rec.created_at)}#{rec.checkin_id}"})

    # ---------------------------------------------------------------- read
    def _records(self, incident_id: str) -> list[tuple[CheckInRecord, dict]]:
        rows = self.repo.query(CHECKINS_TABLE, {"pk": incident_id}, limit=5000)
        return [(CheckInRecord.model_validate(_strip(r)), r) for r in rows]

    def list_checkins(self, incident_id: str, principal: Principal | None) -> dict[str, Any]:
        """Public current statuses visible to the viewer, plus the viewer's own history."""
        if not isinstance(incident_id, str) or not incident_id.strip() or len(incident_id) > 64:
            raise CheckInError(400, "validation_error", "invalid fields: incident_id",
                               [{"field": "incident_id", "problem": "required, 1 to 64 characters"}])
        if not self.incident_exists(incident_id):
            raise CheckInError(404, "not_found", f"incident '{incident_id}' not found")
        now = self._clock()
        newest: dict[str, CheckInRecord] = {}
        mine: list[CheckInRecord] = []
        for rec, _ in self._records(incident_id):  # oldest first
            newest[rec.user_id] = rec
            if principal is not None and rec.user_id == principal.subject:
                mine.append(rec)
        visible = {"public", "shared"} | ({"incident-members"} if principal is not None else set())
        others = sorted((r for r in newest.values() if r.visibility in visible),
                        key=lambda r: (r.created_at, r.checkin_id), reverse=True)
        mine.sort(key=lambda r: (r.created_at, r.checkin_id), reverse=True)
        out: dict[str, Any] = {
            "incident_id": incident_id,
            "checkins": [render(r.public_view(now)) for r in others],
        }
        if principal is not None:
            out["mine"] = {
                "current": render(mine[0].private_view()) if mine else None,
                "history": [render(r.private_view()) for r in mine],
            }
        return out

    # ---------------------------------------------------------------- location removal
    def _drop_ops(self, rec: CheckInRecord, row: dict) -> list[TxOp]:
        ops = [TxOp(kind="update_cas", table=CHECKINS_TABLE,
                    key={"pk": row["pk"], "sk": row["sk"]},
                    expected_version=int(row.get("version", 0)),
                    patch={"optional_location": None, "location_expires_at": None})]
        if rec.location_expires_at is not None:
            ops.append(TxOp(kind="delete", table=LOCATION_INDEX_TABLE, key={
                "pk": LOCATION_INDEX_PK,
                "sk": f"{_stamp(rec.location_expires_at)}#{rec.checkin_id}"}))
        return ops

    def revoke_locations(self, user_id: str, incident_id: str | None = None) -> int:
        """Opt-out (31.9): remove the user's stored locations and tell the channel (<= 5 s)."""
        rows = self.repo.query(CHECKINS_TABLE, {"user_id": user_id}, index="user-index", limit=5000)
        removed = 0
        for row in rows:
            rec = CheckInRecord.model_validate(_strip(row))
            if rec.optional_location is None or (incident_id and rec.incident_id != incident_id):
                continue
            ops = self._drop_ops(rec, row)
            try:
                if rec.visibility in ("public", "shared"):
                    self.bus.commit(ops, rec.incident_id, EVENT_LOCATION_REMOVED,
                                    {"checkin_id": rec.checkin_id, "incident_id": rec.incident_id})
                else:
                    self.repo.transact(ops)
            except Conflict:
                continue
            removed += 1
        return removed

    def scrub_expired_locations(self, now: datetime | None = None) -> int:
        """Remove locations whose retention has ended; check-in records remain. Idempotent."""
        now = now or self._clock()
        cutoff = _stamp(now)
        scrubbed = 0
        while True:
            batch = self.repo.query(LOCATION_INDEX_TABLE, {"pk": LOCATION_INDEX_PK}, limit=200)
            due = [b for b in batch if b["sk"].split("#", 1)[0] <= cutoff]
            if not due:
                return scrubbed
            progressed = False
            for entry in due:
                row = self.repo.get(CHECKINS_TABLE, {"pk": entry["incident_id"], "sk": entry["checkin_sk"]})
                try:
                    if row is None:
                        self.repo.transact([TxOp(kind="delete", table=LOCATION_INDEX_TABLE,
                                                 key={"pk": entry["pk"], "sk": entry["sk"]})])
                    else:
                        rec = CheckInRecord.model_validate(_strip(row))
                        ops = self._drop_ops(rec, row)
                        if not any(o.table == LOCATION_INDEX_TABLE for o in ops):
                            ops.append(TxOp(kind="delete", table=LOCATION_INDEX_TABLE,
                                            key={"pk": entry["pk"], "sk": entry["sk"]}))
                        self.repo.transact(ops)
                        scrubbed += 1
                    progressed = True
                except Conflict:
                    continue
            if not progressed:
                return scrubbed
