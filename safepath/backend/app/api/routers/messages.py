"""Incident message endpoints (Req 26, 28): POST/GET /api/messages, POST /api/messages/{id}/hide.

Reads are public and return public views only (alias, never the sender id). Writes need a token
(Guest and above) and are rate limited to 20 per minute. Messages are always labeled Community.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.adapters.ports import Principal, Role
from app.api.ratelimit import rate_limit
from app.api.routers.events import get_event_bus
from app.auth.deps import require
from app.communication.messaging import (
    ASSISTANCE_TYPES, IncidentNotFound, MessageNotFound, MessagingService, notice_for, public_message,
)

router = APIRouter(prefix="/api", tags=["messages"])

_ANY_ROLE = {Role.GUEST, Role.USER, Role.MODERATOR, Role.AUTHORIZED_OFFICIAL}
_MODERATORS = {Role.MODERATOR, Role.AUTHORIZED_OFFICIAL}


def get_messaging_service(app: FastAPI) -> MessagingService:
    svc = getattr(app.state, "messaging_service", None)
    if svc is None:
        repo = getattr(app.state, "repository", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="message store unavailable")
        svc = app.state.messaging_service = MessagingService(
            repo, get_event_bus(app), getattr(app.state, "audit", None))
    return svc


@router.post("/messages", status_code=201)
def create_message(request: Request, payload: Any = Body(...),
                   principal: Principal = Depends(require(_ANY_ROLE)),
                   _limit: None = Depends(rate_limit("message"))) -> JSONResponse:
    service = get_messaging_service(request.app)
    try:
        rec = service.create(principal, payload)
    except IncidentNotFound:
        raise HTTPException(status_code=404, detail="incident not found") from None
    body = {"message": {**public_message(rec),
                        "share_location": rec.share_location}}
    notice = notice_for(rec.message_type)
    if notice:
        body["notice"] = notice
    return JSONResponse(body, status_code=201)


@router.get("/messages")
def list_messages(request: Request, incident_id: str = Query(min_length=1, max_length=64),
                  since: str | None = Query(default=None, max_length=64),
                  limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    """Visible messages of one incident, oldest first; ``since`` is a message_id cursor."""
    service = get_messaging_service(request.app)
    if not service.incident_exists(incident_id):
        raise HTTPException(status_code=404, detail="incident not found")
    msgs = service.list_public(incident_id, since, limit)
    body: dict[str, Any] = {
        "incident_id": incident_id, "source_label": "Community",
        "messages": [public_message(m) for m in msgs],
        "last_message_id": msgs[-1].message_id if msgs else since,
    }
    if any(m.message_type in ASSISTANCE_TYPES for m in msgs):
        body["notice"] = notice_for("assistance_request")
    return body


@router.post("/messages/{message_id}/hide")
def hide_message(message_id: str, request: Request,
                 principal: Principal = Depends(require(_MODERATORS))) -> dict[str, Any]:
    service = get_messaging_service(request.app)
    try:
        rec = service.hide(principal, message_id)
    except MessageNotFound:
        raise HTTPException(status_code=404, detail="message not found") from None
    return {"message_id": rec.message_id, "incident_id": rec.incident_id, "hidden": True}
