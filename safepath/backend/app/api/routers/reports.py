"""Hazard report endpoints: POST /api/reports and GET /api/reports (Req 23, 28.3, 16.1, 41.6).

Writes require a token (Guest and above) and are rate limited. Listing is public and returns
public views only: an anonymous alias, never the reporter id.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.adapters.ports import Principal, Role
from app.api.ratelimit import rate_limit
from app.api.routers.events import get_event_bus
from app.auth.deps import require
from app.models.records import HazardReportRecord
from app.api.sanitize import sanitize_text
from app.routing.runtime import get_graph
from app.services.reports import SOURCE_LABEL, IncidentNotFound, ReportService
from app.services.verification import (
    CORROBORATED_TEXT,
    MODERATE_ACTIONS,
    ConcurrentUpdate,
    DuplicateVote,
    InvalidTransition,
    NotPermitted,
    OwnReportVote,
    ReportNotFound,
    VerificationService,
)
from app.services.wiring import get_overlay_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["reports"])

SIMULATED_LABEL = "SIMULATED demo report"


def get_report_service(app: FastAPI) -> ReportService:
    svc = getattr(app.state, "report_service", None)
    if svc is None:
        repo = getattr(app.state, "repository", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="report store unavailable")
        svc = app.state.report_service = ReportService(
            repo, get_event_bus(app), getattr(app.state, "audit", None),
            graph_provider=lambda: get_graph(app),
        )
    return svc


def public_report(rec: HazardReportRecord, app: FastAPI | None = None) -> dict[str, Any]:
    view = rec.public_view().model_dump(mode="json")
    status = view["verification_status"]
    view["status_label"] = (CORROBORATED_TEXT if status == "Corroborated"
                            else status.replace("_", " "))
    view["source_label"] = rec.source_category or SOURCE_LABEL
    view["classification"] = "Simulated" if rec.simulated else "Community"
    if rec.simulated:
        view["simulated_label"] = SIMULATED_LABEL
    if app is not None and rec.photo_key:
        # Short-lived signed GET URL generated on read; the storage key itself is never public.
        from app.api.routers.photos import photo_url_for

        url = photo_url_for(app, rec.photo_key)
        if url:
            view["photo_url"] = url
    return view


_ANY_ROLE = {Role.GUEST, Role.USER, Role.MODERATOR, Role.AUTHORIZED_OFFICIAL}


@router.post("/reports", status_code=201)
def create_report(request: Request, payload: Any = Body(...),
                  principal: Principal = Depends(require(_ANY_ROLE)),
                  _limit: None = Depends(rate_limit("report"))) -> JSONResponse:
    service = get_report_service(request.app)
    try:
        result = service.create(principal, payload)
    except IncidentNotFound:
        raise HTTPException(status_code=404, detail="incident not found") from None
    body = result.report.private_view().model_dump(mode="json")
    body.update({k: v for k, v in public_report(result.report, request.app).items() if k not in body})
    return JSONResponse(body, status_code=201 if result.created else 200)


@router.get("/reports")
def list_reports(request: Request, incident_id: str = Query(min_length=1, max_length=64),
                 include_inactive: bool = Query(default=False)) -> dict[str, Any]:
    service = get_report_service(request.app)
    try:
        get_verification_service(request.app).expire_due(incident_id)
    except HTTPException:
        pass
    except Exception:  # expiry sweep is best effort; listing still filters lazily
        log.exception("expiry sweep failed")
    reports = service.list_public(incident_id, include_inactive=include_inactive)
    return {"incident_id": incident_id, "reports": [public_report(r, request.app) for r in reports]}


# ------------------------------------------------------------------ verification (task 9.3)
def get_verification_service(app: FastAPI) -> VerificationService:
    svc = getattr(app.state, "verification_service", None)
    if svc is None:
        bus = get_event_bus(app)
        if bus is None:
            raise HTTPException(status_code=503, detail="report store unavailable")
        svc = app.state.verification_service = VerificationService(
            get_report_service(app), bus, get_overlay_service(app))
    return svc


def _detail_view(svc: VerificationService, rec: HazardReportRecord,
                 app: FastAPI | None = None) -> dict[str, Any]:
    view = public_report(rec, app)
    view["version"] = rec.version
    view["status_history"] = svc.history_view(rec)
    return view


def _vote(request: Request, report_id: str, principal: Principal, kind: str) -> dict[str, Any]:
    svc = get_verification_service(request.app)
    try:
        result = svc.vote(principal, report_id, kind)
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="report not found") from None
    except OwnReportVote:
        raise HTTPException(status_code=403, detail="you cannot vote on your own report") from None
    except DuplicateVote:
        raise HTTPException(status_code=409, detail="you have already voted on this report") from None
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ConcurrentUpdate:
        raise HTTPException(status_code=409, detail="report changed concurrently; retry") from None
    return _detail_view(svc, result.report, request.app)


@router.get("/reports/{report_id}")
def get_report(request: Request, report_id: str) -> dict[str, Any]:
    svc = get_verification_service(request.app)
    try:
        rec = svc.get_report(report_id)
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="report not found") from None
    return _detail_view(svc, rec, request.app)


@router.post("/reports/{report_id}/corroborate")
def corroborate_report(request: Request, report_id: str,
                       principal: Principal = Depends(require(_ANY_ROLE)),
                       _limit: None = Depends(rate_limit("vote"))) -> dict[str, Any]:
    return _vote(request, report_id, principal, "confirm")


@router.post("/reports/{report_id}/dispute")
def dispute_report(request: Request, report_id: str,
                   principal: Principal = Depends(require(_ANY_ROLE)),
                   _limit: None = Depends(rate_limit("vote"))) -> dict[str, Any]:
    return _vote(request, report_id, principal, "dispute")


@router.post("/reports/{report_id}/moderate")
def moderate_report(request: Request, report_id: str, payload: Any = Body(...),
                    principal: Principal = Depends(
                        require({Role.MODERATOR, Role.AUTHORIZED_OFFICIAL}))) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    action = payload.get("action")
    if action not in MODERATE_ACTIONS:
        raise HTTPException(status_code=400, detail="action must be one of: " + ", ".join(MODERATE_ACTIONS))
    reason = payload.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
        raise HTTPException(status_code=400, detail="reason must be a string of at most 500 characters")
    expected = payload.get("expected_version")
    if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool)):
        raise HTTPException(status_code=400, detail="expected_version must be an integer")
    svc = get_verification_service(request.app)
    try:
        result = svc.moderate(principal, report_id, action,
                              reason=sanitize_text(reason.strip()) if reason else None,
                              expected_version=expected)
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="report not found") from None
    except NotPermitted as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except (InvalidTransition, ConcurrentUpdate) as exc:
        raise HTTPException(status_code=409, detail=str(exc) or "report changed concurrently") from None
    view = _detail_view(svc, result.report, request.app)
    view["overlay_version"] = result.overlay_version
    return view
