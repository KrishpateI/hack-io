"""Lazy app-wide accessors for the incident and overlay services (built from ``app.state``)."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.api.routers.events import get_event_bus
from app.services.incidents import IncidentService
from app.services.overlays import OverlayService


def _on_change(app: Any):
    provider = getattr(app.state, "routing_context_provider", None)
    return getattr(provider, "invalidate", None)


def get_incident_service(app: Any) -> IncidentService:
    svc = getattr(app.state, "incident_service", None)
    if svc is None:
        bus = get_event_bus(app)
        if bus is None:
            raise HTTPException(503, "storage unavailable")
        svc = app.state.incident_service = IncidentService(bus.repo, bus, on_change=_on_change(app))
    return svc


def get_overlay_service(app: Any) -> OverlayService:
    svc = getattr(app.state, "overlay_service", None)
    if svc is None:
        bus = get_event_bus(app)
        if bus is None:
            raise HTTPException(503, "storage unavailable")
        svc = app.state.overlay_service = OverlayService(bus.repo, bus, on_change=_on_change(app))
    return svc
