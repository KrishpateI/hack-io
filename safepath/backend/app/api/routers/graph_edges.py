"""GET /api/graph/edges/{edge_id} and /api/graph/edges/near: public, read-only edge inspection.

Backs the map road popup (Req 35.1, 35.2): the Edge attributes of the loaded routing graph, the
decoded geometry, the hard-restriction causes that currently block the edge for an optional
incident, and the public hazard reports that touch it. Missing values stay the string "Unknown".
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from shapely.geometry import Point

from app.api.routers.reports import get_report_service, public_report
from app.api.routers.routes import get_routing_service
from app.disasters.base import IncidentContext
from app.models.routing import LatLon
from app.routing.graph import Graph, _xy, decode_geometry
from app.routing.service import _module_for

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["graph"])

MAX_NEAR_RESULTS = 25


def _geometry(graph: Graph, idx: int) -> dict[str, Any]:
    e = graph.edge(idx)
    coords = decode_geometry(e.geometry)
    if coords is None:
        a, b = int(graph.edge_from[idx]), int(graph.edge_to[idx])
        coords = [(float(graph.node_lon[a]), float(graph.node_lat[a])),
                  (float(graph.node_lon[b]), float(graph.node_lat[b]))]
    return {"type": "LineString", "coordinates": [[x, y] for x, y in coords]}


def _restriction_causes(app: FastAPI, graph: Graph, incident_id: str, edge_id: str) -> dict[str, Any]:
    """Active hard-restriction causes on ``edge_id`` for the incident (same resolver as routing)."""
    out: dict[str, Any] = {"incident_id": incident_id, "available": False, "causes": []}
    try:
        service = get_routing_service(app)
        now = datetime.now(timezone.utc)
        data = service.context_provider.load(incident_id, now)
        if data is None:
            out["error"] = "incident not found"
            return out
        a = int(graph.edge_from[graph.edge_ids.index(edge_id)])
        origin = LatLon(latitude=float(graph.node_lat[a]), longitude=float(graph.node_lon[a]))
        ctx = IncidentContext(incident=data.incident, now=now, origin=origin, notices=data.notices,
                              reports=data.reports, overlays=data.overlays)
        module = _module_for(ctx)
        resolved = service.resolver.resolve(ctx, graph, [module] if module is not None else [],
                                            overlay_version=data.overlay_version)
        out["causes"] = [c.model_dump(mode="json") for c in resolved.causes(edge_id)]
        out["available"] = True
    except Exception:  # restriction data is best effort here; never fail the inspection
        log.exception("restriction lookup failed for edge %s", edge_id)
        out["error"] = "restriction data unavailable"
    return out


def _reports_touching(app: FastAPI, incident_id: str, edge_id: str) -> list[dict[str, Any]]:
    try:
        reports = get_report_service(app).list_public(incident_id)
    except Exception:
        log.exception("report lookup failed")
        return []
    return [public_report(r, app) for r in reports if edge_id in (r.affected_edge_ids or [])]


def edge_payload(app: FastAPI, graph: Graph, idx: int, incident_id: str | None) -> dict[str, Any]:
    edge = graph.edge(idx)
    body = edge.model_dump(mode="json")
    sids = edge.source_ids or {}
    name = sids.get("street_name") or sids.get("name")
    body["street_name"] = name if name else "Unknown"
    body["geometry_geojson"] = _geometry(graph, idx)
    body["restrictions"] = (_restriction_causes(app, graph, incident_id, edge.edge_id)
                            if incident_id else {"incident_id": None, "available": False, "causes": []})
    body["reports"] = _reports_touching(app, incident_id, edge.edge_id) if incident_id else []
    return body


def _loaded(request: Request) -> Graph:
    from app.routing.runtime import get_graph

    g = get_graph(request.app)
    if not g.loaded or g.num_edges == 0:
        raise HTTPException(status_code=503, detail="routing graph is not available")
    return g


# NOTE: declared before the {edge_id} route so "near" is not captured as an id.
@router.get("/graph/edges/near")
def edges_near(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
               radius: float = Query(default=25.0, gt=0, le=500), limit: int = Query(default=5, ge=1, le=MAX_NEAR_RESULTS),
               incident_id: str | None = Query(default=None, min_length=1, max_length=64)) -> dict[str, Any]:
    g = _loaded(request)
    g._ensure_tree()
    assert g._tree is not None and g._geoms is not None
    px, py = _xy(lon, lat)
    pt = Point(px, py)
    cand = g._tree.query(pt.buffer(radius))
    scored = sorted(((float(g._geoms[int(i)].distance(pt)), int(i)) for i in cand), key=lambda t: t[0])
    hits = [(d, i) for d, i in scored if d <= radius][:limit]
    return {
        "query": {"lat": lat, "lon": lon, "radius_m": radius, "incident_id": incident_id},
        "edges": [{**edge_payload(request.app, g, i, incident_id), "distance_m": round(d, 2)} for d, i in hits],
    }


@router.get("/graph/edges/{edge_id}")
def get_edge(request: Request, edge_id: str,
             incident_id: str | None = Query(default=None, min_length=1, max_length=64)) -> dict[str, Any]:
    g = _loaded(request)
    try:
        idx = g.edge_ids.index(edge_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="edge not found") from None
    return edge_payload(request.app, g, idx, incident_id)

