"""POST /api/routes/calculate (Req 13, 43).

Read-only and public: it accepts no write and exposes only algorithm-generated results, so no
authentication is required (writes elsewhere are authenticated). Errors use the shared envelope.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, FastAPI, Request

from app.models.routing import RouteResponse
from app.routing.runtime import get_graph
from app.routing.service import InMemoryContextProvider, RoutingService

router = APIRouter(prefix="/api", tags=["routes"])


def get_routing_service(app: FastAPI) -> RoutingService:
    """Return ``app.state.routing_service``, building it on first use.

    Set ``app.state.routing_context_provider`` (task 8.5 installs the Repository-backed one)
    or replace ``app.state.routing_service`` outright to change the data source.
    """
    svc = getattr(app.state, "routing_service", None)
    if svc is None:
        provider = getattr(app.state, "routing_context_provider", None)
        if provider is None:
            provider = app.state.routing_context_provider = InMemoryContextProvider()
        svc = app.state.routing_service = RoutingService(
            graph_provider=lambda: get_graph(app),
            weights_provider=lambda: app.state.routing_weights.require(),
            context_provider=provider,
        )
    return svc


@router.post("/routes/calculate", response_model=RouteResponse)
def calculate_route(request: Request, payload: Any = Body(...)) -> RouteResponse:
    service = get_routing_service(request.app)
    return service.calculate(service.validate(payload))
