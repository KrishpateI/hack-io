"""ReportService: hazard report creation and listing (Req 23, 28.3, 16.1, 41.6).

Reports never create closures by themselves: they start ``Unverified`` and only the
verification lifecycle (task 9.3) can promote them. Creation validates the payload, associates the
report with graph edges within 25 m (empty when the graph is unavailable, never invented), links
duplicates, honours the ``client_report_id`` idempotency key and writes the report, an optional
history entry and the ``report_created`` event in one ``Repository.transact``.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.adapters.ports import Conflict, Principal, Repository, TxOp
from app.api.sanitize import sanitize_text
from app.auth.audit import AuditWriter
from app.communication.events import EventBus
from app.geo import CoordinateStatus, classify_coordinate, haversine
from app.models.domain import VerificationStatus
from app.models.ids import report_id_for
from app.models.privacy import load_alias_secret, reporter_alias
from app.models.records import HazardReportRecord, StatusHistoryEntry
from app.services.overlays import get_incident_row
from app.routing.validation import RequestValidationError

log = logging.getLogger(__name__)

REPORTS_TABLE = "reports"

CATEGORIES: tuple[str, ...] = (
    "blocked road", "flooding", "fallen debris", "fire or smoke", "blocked building exit",
    "inaccessible stairwell", "transit closure", "person needing assistance", "other",
)
MAX_DESCRIPTION = 1000
EDGE_RADIUS_M = 25.0
DUPLICATE_RADIUS_M = 50.0
DUPLICATE_WINDOW = timedelta(minutes=60)
DEFAULT_EXPIRY = timedelta(hours=4)
SOURCE_LABEL = "Community report"
CREATE_ACTION = "report_created"
_PHOTO_KEY_RE = re.compile(r"^[A-Za-z0-9/_.\-]{1,256}$")
_DEV_ALIAS_SECRET = b"safepath-dev-alias-secret-not-for-production"
_INACTIVE = {VerificationStatus.RESOLVED, VerificationStatus.EXPIRED}


class IncidentNotFound(Exception):
    pass


@dataclass(frozen=True)
class CreateResult:
    report: HazardReportRecord
    created: bool  # False when an idempotent resubmission returned the stored report


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bad(fields: list[dict[str, str]], status: int = 400) -> RequestValidationError:
    return RequestValidationError(
        status, "validation_error", "invalid fields: " + ", ".join(f["field"] for f in fields), fields)


def _row_to_record(row: dict) -> HazardReportRecord:
    return HazardReportRecord.model_validate({k: v for k, v in row.items() if k not in ("pk", "sk")})


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class ReportService:
    def __init__(self, repo: Repository, bus: EventBus | None,
                 audit: AuditWriter | None = None,
                 graph_provider: Callable[[], Any] | None = None,
                 clock: Callable[[], datetime] = _now,
                 alias_secret: bytes | str | None = None) -> None:
        self.repo = repo
        self.bus = bus
        self.audit = audit
        self._graph_provider = graph_provider
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

    def _graph(self) -> Any | None:
        if self._graph_provider is None:
            return None
        try:
            g = self._graph_provider()
        except Exception:  # graph is optional for reports
            log.exception("graph unavailable for report edge association")
            return None
        return g if g is not None and getattr(g, "loaded", False) else None

    def alias_for(self, subject: str, incident_id: str) -> str:
        return reporter_alias(self._secret(), subject, incident_id)

    def _incident(self, incident_id: str) -> dict:
        row = get_incident_row(self.repo, incident_id)
        if row is None:
            raise IncidentNotFound(incident_id)
        return row

    def nearby_edge_ids(self, lat: float, lon: float, radius_m: float = EDGE_RADIUS_M) -> list[str]:
        """Street edge ids whose geometry lies within ``radius_m`` of the point (sorted)."""
        g = self._graph()
        if g is None or g.num_edges == 0:
            return []
        from shapely.geometry import Point

        from app.routing.graph import _xy

        g._ensure_tree()
        pt = Point(*_xy(lon, lat))
        out: list[str] = []
        for i in g._tree.query(pt.buffer(radius_m)):
            i = int(i)
            if g.edge(i).edge_kind != "street":
                continue
            if g._geoms[i].distance(pt) <= radius_m:
                out.append(g.edge_ids[i])
        return sorted(out)

    def indoor_feature_edges(self, feature_id: str) -> tuple[list[str], tuple[float, float] | None]:
        """Indoor edge ids of a stairwell or exit feature (both directions, all floors), plus a
        representative position. Empty when the graph is unavailable or the feature is unknown."""
        g = self._graph()
        if g is None:
            return [], None
        fid = feature_id[len("stair-"):] if feature_id.startswith("stair-") else feature_id
        stair = f"sim-indoor-t-stair-{fid}-"
        exits = {f"sim-indoor-t-{feature_id}-out", f"sim-indoor-t-{feature_id}-in"}
        ids: list[str] = []
        pos: tuple[float, float] | None = None
        for idx, eid in enumerate(g.edge_ids):
            e = g.edge(idx)
            if e.edge_kind != "level_transition":
                continue
            if (e.transition_type == "stairwell" and eid.startswith(stair)) or eid in exits:
                ids.append(eid)
                if pos is None:
                    n = int(g.edge_from[idx])
                    pos = (float(g.node_lat[n]), float(g.node_lon[n]))
        return sorted(ids), pos

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
        category = payload.get("category")
        if not isinstance(category, str) or category not in CATEGORIES:
            err("category", "unsupported value; supported values: " + ", ".join(CATEGORIES))
        desc = payload.get("description", "")
        if desc is None:
            desc = ""
        if not isinstance(desc, str):
            err("description", "must be a string")
            desc = ""
        elif len(desc) > MAX_DESCRIPTION:
            err("description", f"must be at most {MAX_DESCRIPTION} characters")
        client_id = payload.get("client_report_id")
        if client_id is not None and (not isinstance(client_id, str) or not 1 <= len(client_id) <= 128):
            err("client_report_id", "must be a string of 1 to 128 characters")
        photo = payload.get("photo_key")
        if photo is not None and (not isinstance(photo, str) or not _PHOTO_KEY_RE.match(photo)):
            err("photo_key", "must be an upload key of letters, digits and / _ . -")
        feature = payload.get("indoor_feature_id")
        if feature is not None and (not isinstance(feature, str) or not 1 <= len(feature) <= 64):
            err("indoor_feature_id", "must be a string of 1 to 64 characters")

        lat, lon = payload.get("latitude"), payload.get("longitude")
        has_coords = lat is not None or lon is not None
        if has_coords or feature is None:
            if not _is_number(lat):
                err("latitude", "required number")
            if not _is_number(lon):
                err("longitude", "required number")
            if _is_number(lat) and _is_number(lon):
                status = classify_coordinate(lat, lon)
                if status is CoordinateStatus.INVALID:
                    err("latitude", "coordinates out of range")
                elif status is CoordinateStatus.OUTSIDE_REGION and feature is None:
                    err("latitude", "outside the supported Chicago extraction area")
        if errs:
            raise _bad(errs)
        return {
            "incident_id": incident_id, "category": category,
            "description": sanitize_text(desc.strip()) if desc else "",
            "latitude": float(lat) if _is_number(lat) else None,
            "longitude": float(lon) if _is_number(lon) else None,
            "client_report_id": client_id, "photo_key": photo, "indoor_feature_id": feature,
        }

    # ------------------------------------------------------------------ create
    def create(self, principal: Principal, payload: Any) -> CreateResult:
        data = self.validate(payload)
        if data["photo_key"] is not None:
            from app.api.routers.photos import user_prefix  # lazy: avoids a router import cycle

            key = data["photo_key"]
            if not key.startswith(user_prefix(principal.subject)) or ".." in key:
                raise _bad([{"field": "photo_key", "problem": "must be an upload key issued to you"}])
        report_id = report_id_for(principal.subject, data["client_report_id"])
        if data["client_report_id"] is not None:
            existing = self.repo.get(REPORTS_TABLE, {"pk": data["incident_id"], "sk": report_id})
            if existing is not None:
                return CreateResult(_row_to_record(existing), False)

        incident = self._incident(data["incident_id"])
        lat, lon = data["latitude"], data["longitude"]
        edge_ids: list[str] = []
        if data["indoor_feature_id"] is not None:
            edge_ids, pos = self.indoor_feature_edges(data["indoor_feature_id"])
            if pos is None:
                raise _bad([{"field": "indoor_feature_id", "problem": "unknown indoor feature"}])
            if lat is None or lon is None:
                lat, lon = pos
        elif lat is not None and lon is not None:
            edge_ids = self.nearby_edge_ids(lat, lon)
        assert lat is not None and lon is not None

        now = self._clock()
        duplicate_of = self._find_duplicate(data["incident_id"], data["category"], lat, lon, now)
        record = HazardReportRecord(
            report_id=report_id, incident_id=data["incident_id"], category=data["category"],
            latitude=lat, longitude=lon, affected_edge_ids=edge_ids,
            verification_status=VerificationStatus.UNVERIFIED, confirmations=0, disputes=0,
            created_at=now, expires_at=now + DEFAULT_EXPIRY,
            simulated=bool(incident.get("simulation_flag", False)),
            reporter_id=principal.subject,
            alias=reporter_alias(self._secret(), principal.subject, data["incident_id"]),
            description=data["description"], photo_key=data["photo_key"], updated_at=now,
            status_history=[StatusHistoryEntry(
                status=VerificationStatus.UNVERIFIED.value, at=now,
                actor_role=principal.role.value, reason="created")],
            duplicate_of=duplicate_of, version=1, client_report_id=data["client_report_id"],
        )
        item = record.model_dump(mode="json")
        op = TxOp(kind="put", table=REPORTS_TABLE, if_absent=True, item={**item, "pk": record.incident_id, "sk": report_id})
        payload_out = {"report_id": report_id, "category": record.category,
                       "verification_status": record.verification_status.value,
                       "latitude": lat, "longitude": lon,
                       "affected_edge_ids": edge_ids, "duplicate_of": duplicate_of,
                       "simulated": record.simulated}
        try:
            if self.bus is not None:
                self.bus.commit([op], record.incident_id, CREATE_ACTION, payload_out)
            else:
                self.repo.transact([op])
        except Conflict:  # concurrent idempotent resubmission won the race
            existing = self.repo.get(REPORTS_TABLE, {"pk": data["incident_id"], "sk": report_id})
            if existing is None:
                raise
            return CreateResult(_row_to_record(existing), False)
        if self.audit is not None:
            try:
                self.audit.record(principal.subject, principal.role.value, CREATE_ACTION, report_id)
            except Exception:
                log.exception("audit write failed for %s", report_id)
        return CreateResult(record, True)

    def _find_duplicate(self, incident_id: str, category: str, lat: float, lon: float,
                        now: datetime) -> str | None:
        """Oldest earlier same-category report within 50 m and 60 min (never itself a duplicate)."""
        rows = self.repo.query(REPORTS_TABLE, {"pk": incident_id}, limit=1000)
        best: HazardReportRecord | None = None
        for row in rows:
            r = _row_to_record(row)
            if r.category != category or r.verification_status in _INACTIVE or r.duplicate_of:
                continue
            if now - r.created_at > DUPLICATE_WINDOW:
                continue
            if haversine(lat, lon, r.latitude, r.longitude) > DUPLICATE_RADIUS_M:
                continue
            if best is None or r.created_at < best.created_at:
                best = r
        return best.report_id if best else None

    # ------------------------------------------------------------------ listing
    def list_public(self, incident_id: str, include_inactive: bool = False,
                    limit: int = 500) -> list[HazardReportRecord]:
        """Reports of an incident, newest first. Resolved and Expired (including lazily expired)
        reports are excluded unless ``include_inactive``."""
        now = self._clock()
        out: list[HazardReportRecord] = []
        for row in self.repo.query(REPORTS_TABLE, {"pk": incident_id}, limit=limit):
            r = _row_to_record(row)
            inactive = r.verification_status in _INACTIVE or r.expires_at <= now
            if inactive and not include_inactive:
                continue
            if r.expires_at <= now and r.verification_status not in _INACTIVE:
                r = r.model_copy(update={"verification_status": VerificationStatus.EXPIRED})
            out.append(r)
        out.sort(key=lambda r: (r.created_at, r.report_id), reverse=True)
        return out
