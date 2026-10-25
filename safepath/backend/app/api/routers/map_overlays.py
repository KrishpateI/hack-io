"""GET /api/incidents/{incident_id}/map-overlays: public, read-only map overlay snapshot.

Delivers the geometry behind the map legend's hazard and restriction entries for one incident
(map-legend-layers-fix, design D1): active edge restrictions as lines, area restrictions, SIMULATED
fire perimeter notices and, for SIMULATED incidents only, the scenario flood area and debris zones.
Works with demo mode off. Never writes and is not read by routing. ``Cache-Control: no-store``
because the payload reflects live overlay state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.models.domain import Incident
from app.routing.graph import Graph
from app.services.map_overlays import build_map_overlays
from app.services.overlays import get_incident_row, strip_keys

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["incidents"])

CACHE_CONTROL = "no-store"


# ------------------------------------------------------------------------- response model
class MapOverlayFeature(BaseModel):
    """GeoJSON Feature. ``properties`` is allow-listed by the service (no private or depth keys)."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["Feature"]
    geometry: dict[str, Any] = Field(description="GeoJSON geometry (LineString, Polygon or MultiPolygon)")
    properties: dict[str, Any]


class MapOverlayFeatureCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["FeatureCollection"]
    features: list[MapOverlayFeature]


class MapOverlayHazards(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flood: MapOverlayFeatureCollection
    debris: MapOverlayFeatureCollection
    fire: MapOverlayFeatureCollection


class MapOverlayLimitations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing_edge_geometry: int = Field(ge=0)
    graph_loaded: bool


class MapOverlays(BaseModel):
    """Everything the map needs to draw hazards and restrictions for one incident."""
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    simulation_flag: bool
    overlay_version: int
    generated_at: str
    disclaimer: str
    restrictions: MapOverlayFeatureCollection
    hazards: MapOverlayHazards
    limitations: MapOverlayLimitations


# ---------------------------------------------------------------------------------- deps
def _repository(request: Request) -> Any:
    repo = getattr(request.app.state, "repository", None)
    if repo is None:
        raise HTTPException(503, "storage unavailable")
    return repo


def _graph_or_none(request: Request) -> Graph | None:
    """The loaded routing graph, or None. A missing graph never fails the request."""
    from app.routing.runtime import get_graph

    try:
        g = get_graph(request.app)
    except Exception:  # hazards and area restrictions are still served without a graph
        log.exception("routing graph unavailable for map overlays")
        return None
    if g is None or not getattr(g, "loaded", False) or g.num_edges == 0:
        return None
    return g


# --------------------------------------------------------------------------------- route
@router.get("/incidents/{incident_id}/map-overlays", response_model=MapOverlays)
def get_map_overlays(
    request: Request, response: Response,
    incident_id: str = Path(min_length=1, max_length=64),
) -> dict[str, Any]:
    repo = _repository(request)
    row = get_incident_row(repo, incident_id)  # RepositoryUnavailable -> 503 envelope
    if row is None:
        raise HTTPException(404, f"incident '{incident_id}' not found")
    incident = Incident.model_validate(strip_keys(row))
    body = build_map_overlays(repo, _graph_or_none(request), incident, datetime.now(timezone.utc))
    response.headers["Cache-Control"] = CACHE_CONTROL
    return body
