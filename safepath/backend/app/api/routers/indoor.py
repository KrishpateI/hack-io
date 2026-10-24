"""GET /api/indoor/building: the SIMULATED fictional demo building as GeoJSON (Req 21, 39)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.routing.indoor import indoor_geojson

router = APIRouter(prefix="/api/indoor", tags=["indoor"])


@router.get("/building")
def indoor_building() -> dict[str, Any]:
    """Public, read-only. Every feature and the collection carry the SIMULATED notice."""
    return indoor_geojson()
