"""GET /api/health: liveness, loaded graph version and active profile."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

from app.routing.runtime import get_graph

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    graph = get_graph(request.app)
    # Fall back to the configured placeholder only when no graph is loaded.
    version = graph.version or settings.graph_version
    return {
        "status": "ok",
        "graph_version": version,
        "graph_status": graph.status,
        "graph_fallback": getattr(request.app.state, "graph_fallback", None),
        "profile": settings.profile,
        "routing": request.app.state.routing_weights.status,
        "time": datetime.now(timezone.utc).isoformat(),
    }
