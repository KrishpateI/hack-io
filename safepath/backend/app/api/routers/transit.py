"""GET /api/transit/alerts: optional live CTA alerts with stale fallback (Req 5.2, 5.3, 5.4)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.services.transit import TransitAlertsService, join_stations

router = APIRouter(prefix="/api", tags=["transit"])


def get_transit_service(app: Any) -> TransitAlertsService:
    svc = getattr(app.state, "transit_alerts_service", None)
    if svc is None:
        svc = app.state.transit_alerts_service = TransitAlertsService(getattr(app.state, "repository", None))
    return svc


@router.get("/transit/alerts")
def transit_alerts(request: Request) -> dict[str, Any]:
    """Current CTA alerts, or ``available: false`` with 'Live transit alerts are unavailable'.

    ``station_alerts`` associates route alerts with stations served; entrance safety stays Unknown.
    """
    result = get_transit_service(request.app).get_alerts()
    station_alerts: dict[str, Any] = {}
    if result["alerts"]:
        try:
            from app.routing.runtime import get_graph

            layer = (get_graph(request.app).layers or {}).get("cta_stations") or {}
            station_alerts = join_stations(result["alerts"], layer.get("features", []))
        except Exception:
            station_alerts = {}
    return {**result, "station_alerts": station_alerts}
