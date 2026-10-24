"""Incidents and official notices (Req 11.2, 28.8, 30.7, 42.1, 42.3, 42.4, 42.7).

* ``GET /api/incidents``, ``GET /api/incidents/{id}``, ``GET /api/notices?incident_id=`` are public.
* ``POST /api/incidents`` requires Moderator or Authorized_Official.
* ``POST /api/notices`` requires Authorized_Official ONLY (Moderators get 403) and is audited.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.adapters.ports import Principal, Role
from app.api.sanitize import sanitize_text
from app.auth.deps import require
from app.models.domain import Incident, IncidentType, NoticeType, OfficialNotice
from app.services.incidents import IncidentNotFound
from app.services.wiring import get_incident_service

router = APIRouter(prefix="/api", tags=["incidents"])

MAX_COORD_POINTS = 20_000


def _clean(value: str, field: str) -> str:
    out = sanitize_text(value).strip()
    if not out:
        raise ValueError(f"{field} must not be empty")
    return out


def _check_geometry(geom: dict[str, Any] | None) -> dict[str, Any] | None:
    if geom is None:
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype not in ("Polygon", "MultiPolygon", "LineString", "MultiLineString") or not isinstance(coords, list) or not coords:
        raise ValueError("geometry must be a GeoJSON Polygon, MultiPolygon, LineString or MultiLineString")
    count = 0

    def walk(node: Any) -> None:
        nonlocal count
        if isinstance(node, list) and len(node) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in node):
            lon, lat = float(node[0]), float(node[1])
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError("geometry coordinate out of range")
            count += 1
            if count > MAX_COORD_POINTS:
                raise ValueError("geometry has too many points")
        elif isinstance(node, list) and node:
            for child in node:
                walk(child)
        else:
            raise ValueError("geometry coordinates are malformed")

    walk(coords)
    return {"type": gtype, "coordinates": coords}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentCreate(_Body):
    incident_type: IncidentType
    source: str = Field(min_length=1, max_length=200)
    geographic_boundary: dict[str, Any] | None = None
    simulation_flag: bool = False
    scenario_id: str | None = Field(default=None, max_length=100)

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        return _clean(v, "source")

    @field_validator("scenario_id")
    @classmethod
    def _scenario(cls, v: str | None) -> str | None:
        return _clean(v, "scenario_id") if v is not None else None

    @field_validator("geographic_boundary")
    @classmethod
    def _boundary(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        v = _check_geometry(v)
        if v is not None and v["type"] not in ("Polygon", "MultiPolygon"):
            raise ValueError("geographic_boundary must be a Polygon or MultiPolygon")
        return v


class NoticeCreate(_Body):
    incident_id: str = Field(min_length=1, max_length=100)
    notice_type: NoticeType
    text: str = Field(min_length=1, max_length=4000)
    geometry: dict[str, Any] | None = None
    edge_ids: list[str] = Field(default_factory=list, max_length=5000)
    source_organization: str = Field(min_length=1, max_length=200)
    source_url: str = Field(min_length=1, max_length=2000)
    source_record_id: str = Field(min_length=1, max_length=200)
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    published_at: datetime | None = None
    simulation_flag: bool = False

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        return _clean(v, "text")

    @field_validator("source_organization", "source_record_id")
    @classmethod
    def _plain(cls, v: str) -> str:
        return _clean(v, "value")

    @field_validator("source_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or any(c in v for c in "<>\"' "):
            raise ValueError("source_url must be an http(s) URL")
        return v

    @field_validator("geometry")
    @classmethod
    def _geometry(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return _check_geometry(v)

    @field_validator("edge_ids")
    @classmethod
    def _edges(cls, v: list[str]) -> list[str]:
        return [_clean(e, "edge_ids") for e in v]

    @field_validator("effective_at", "expires_at", "published_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @model_validator(mode="after")
    def _order(self) -> "NoticeCreate":
        if self.expires_at and self.effective_at and self.expires_at <= self.effective_at:
            raise ValueError("expires_at must be after effective_at")
        return self


@router.get("/incidents", response_model=list[Incident])
def list_incidents(request: Request) -> list[Incident]:
    return get_incident_service(request.app).list_incidents()


@router.get("/incidents/{incident_id}", response_model=Incident)
def get_incident(incident_id: str, request: Request) -> Incident:
    inc = get_incident_service(request.app).get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, f"incident '{incident_id}' not found")
    return inc


@router.post("/incidents", response_model=Incident, status_code=201)
def create_incident(
    body: IncidentCreate, request: Request,
    principal: Principal = Depends(require({Role.MODERATOR, Role.AUTHORIZED_OFFICIAL})),
) -> Incident:
    return get_incident_service(request.app).create_incident(
        body.incident_type, source=body.source, geographic_boundary=body.geographic_boundary,
        simulation_flag=body.simulation_flag, scenario_id=body.scenario_id,
    )


@router.post("/notices", response_model=OfficialNotice, status_code=201)
def create_notice(
    body: NoticeCreate, request: Request,
    principal: Principal = Depends(require({Role.AUTHORIZED_OFFICIAL})),
) -> OfficialNotice:
    svc = get_incident_service(request.app)
    try:
        return svc.create_notice(
            body.incident_id, notice_type=body.notice_type, text=body.text,
            source_organization=body.source_organization, source_url=body.source_url,
            source_record_id=body.source_record_id, geometry=body.geometry, edge_ids=body.edge_ids,
            effective_at=body.effective_at, expires_at=body.expires_at,
            published_at=body.published_at, simulation_flag=body.simulation_flag,
            audit_actor=(principal.subject, principal.role.value),
        )
    except IncidentNotFound:
        raise HTTPException(404, f"incident '{body.incident_id}' not found") from None


@router.get("/notices", response_model=list[OfficialNotice])
def list_notices(request: Request, incident_id: str = Query(min_length=1)) -> list[OfficialNotice]:
    svc = get_incident_service(request.app)
    if svc.get_incident(incident_id) is None:
        raise HTTPException(404, f"incident '{incident_id}' not found")
    return svc.list_notices(incident_id)
