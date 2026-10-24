"""GET /api/regions, /api/regions/chicago/metadata and /api/graph/status."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import geo
from app.routing.runtime import get_graph

router = APIRouter(prefix="/api", tags=["regions"])

BOUNDS = {"north": geo.NORTH, "south": geo.SOUTH, "west": geo.WEST, "east": geo.EAST}

# Approximate neighborhood reference points (Req 2.2); centers are approximate, not boundaries.
NEIGHBORHOODS = [
    {"name": "Loop", "lat": 41.8819, "lon": -87.6278},
    {"name": "West Loop", "lat": 41.8826, "lon": -87.6489},
    {"name": "Riverwalk", "lat": 41.8880, "lon": -87.6310},
    {"name": "Clark/Lake", "lat": 41.8858, "lon": -87.6309},
    {"name": "Millennium Park", "lat": 41.8826, "lon": -87.6226},
    {"name": "DePaul Loop", "lat": 41.8785, "lon": -87.6258},
    {"name": "UIC", "lat": 41.8708, "lon": -87.6505},
    {"name": "UIC-downtown corridor", "lat": 41.8760, "lon": -87.6400},
]


@router.get("/regions")
def list_regions() -> dict:
    return {
        "regions": [
            {
                "id": "chicago",
                "name": "Downtown Chicago",
                "bounds": BOUNDS,
                "note": "Data extraction boundary, not an official evacuation boundary",
            }
        ]
    }


@router.get("/regions/chicago/metadata")
def chicago_metadata(request: Request) -> dict:
    g = get_graph(request.app)
    return {
        "id": "chicago",
        "name": "Downtown Chicago",
        "bounds": BOUNDS,
        "crs": g.manifest.crs if g.manifest else "EPSG:4326",
        "neighborhoods": NEIGHBORHOODS,
        "neighborhoods_note": "Approximate reference points, not official boundaries",
        "graph_version": g.version,
        "graph_status": g.status,
    }


@router.get("/graph/status")
def graph_status(request: Request) -> dict:
    status = get_graph(request.app).status_dict()
    status["fallback"] = getattr(request.app.state, "graph_fallback", None)
    return status
