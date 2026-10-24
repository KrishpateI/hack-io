"""POST/GET /api/checkins and DELETE /api/checkins/location (Req 27, 28.1, 31.6, 31.9).

Writes allow Guest and above (401 without a token). GET works without a token (public check-ins
only); a valid token adds incident-member check-ins and the caller's own history. Public output
never contains user ids or exact coordinates.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.adapters.ports import Principal, Role
from app.api.errors import envelope
from app.api.ratelimit import rate_limit
from app.api.routers.events import get_event_bus
from app.auth.deps import current_principal, require
from app.communication.checkins import CheckInError, CheckInService
from app.routing.service import InMemoryContextProvider

router = APIRouter(prefix="/api", tags=["messages"])

_ANY_ROLE = {Role.GUEST, Role.USER, Role.MODERATOR, Role.AUTHORIZED_OFFICIAL}


def get_checkin_service(app: FastAPI) -> CheckInService:
    svc = getattr(app.state, "checkin_service", None)
    if svc is not None:
        return svc
    repo = getattr(app.state, "repository", None)
    accounts = getattr(app.state, "accounts", None)
    bus = get_event_bus(app)
    if repo is None or accounts is None or bus is None:
        raise HTTPException(status_code=503, detail="check-in service unavailable")

    def incident_exists(incident_id: str) -> bool:
        provider = getattr(app.state, "routing_context_provider", None)
        if provider is None:
            provider = app.state.routing_context_provider = InMemoryContextProvider()
        return provider.incident_exists(incident_id)

    svc = app.state.checkin_service = CheckInService(repo, bus, accounts, incident_exists)
    return svc


def _error(exc: CheckInError) -> JSONResponse:
    return JSONResponse(envelope(exc.code, exc.message, exc.fields), status_code=exc.status)


@router.post("/checkins", status_code=201)
def create_checkin(request: Request, body: dict[str, Any],
                   principal: Principal = Depends(require(_ANY_ROLE)),
                   _limit: None = Depends(rate_limit("checkin"))) -> Any:
    try:
        return get_checkin_service(request.app).create(principal, body)
    except CheckInError as exc:
        return _error(exc)


@router.get("/checkins")
def list_checkins(request: Request, incident_id: str = Query(min_length=1, max_length=64)) -> Any:
    principal: Principal | None = None
    if request.headers.get("authorization"):
        principal = current_principal(request)  # a supplied token must be valid (401)
    try:
        return get_checkin_service(request.app).list_checkins(incident_id, principal)
    except CheckInError as exc:
        return _error(exc)


@router.delete("/checkins/location")
def remove_locations(request: Request, incident_id: str | None = Query(default=None, max_length=64),
                     principal: Principal = Depends(require(_ANY_ROLE))) -> dict[str, int]:
    """Remove the caller's stored check-in locations (opt-out), optionally for one incident."""
    return {"removed": get_checkin_service(request.app).revoke_locations(principal.subject, incident_id)}
