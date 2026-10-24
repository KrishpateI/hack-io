"""Demo admin endpoints (Req 40; SIMULATED). Inert (404) unless SAFEPATH_DEMO_MODE=true.

Admin endpoints need a Moderator or Authorized_Official token (401 missing/invalid, 403 other roles).
``POST /api/demo/sessions`` and ``GET /api/demo/status`` are the only unauthenticated demo routes:
the first hands the console a pre-roled token (moderator tokens only with demo mode AND the local
profile; the role is chosen by the server from ``kind``, never taken from the client). Production
deployments MUST keep demo mode off, otherwise that endpoint is a privilege-escalation path.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.adapters.ports import Principal, Role
from app.auth.deps import current_principal
from app.services.demo import DemoError, DemoService

router = APIRouter(prefix="/api/demo", tags=["demo"])

_ADMIN = frozenset({Role.MODERATOR, Role.AUTHORIZED_OFFICIAL})


def get_demo_service(request: Request, *, required: bool = True) -> DemoService | None:
    svc = getattr(request.app.state, "demo_service", None)
    if required and (svc is None or not request.app.state.settings.demo_mode):
        raise HTTPException(status_code=404, detail="demo mode is off")
    return svc


def demo_on(request: Request) -> DemoService:
    return get_demo_service(request)  # type: ignore[return-value]


def demo_admin(request: Request) -> Principal:
    """404 when demo mode is off, then 401/403 by role."""
    get_demo_service(request)
    principal = current_principal(request)
    if principal.role not in _ADMIN:
        raise HTTPException(status_code=403, detail="insufficient role for this operation")
    return principal


def _call(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except DemoError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionBody(_Body):
    kind: Literal["guest", "user", "moderator"]
    actor: Literal["A", "B", "C"] | None = None


class ActBody(_Body):
    actor: Literal["A", "B", "C", "moderator"]
    action: Literal["report", "corroborate", "dispute", "message", "confirm", "resolve"]
    content: str | None = Field(default=None, max_length=1000)
    report_id: str | None = Field(default=None, max_length=128)
    message_type: str = Field(default="assistance_request", max_length=64)


class ClosureBody(_Body):
    incident_id: str = Field(min_length=1, max_length=64)
    edge_ids: list[str] = Field(default_factory=list, max_length=200)
    geometry: dict[str, Any] | None = None
    kind: Literal["closure", "impassable_polygon"] = "closure"


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    svc = get_demo_service(request, required=False)
    if svc is None:
        return {"demo_mode": False, "simulated": True,
                "notice": "Demo mode is off. Demo controls are disabled."}
    return svc.status()


@router.post("/sessions")
def create_session(body: SessionBody, svc: DemoService = Depends(demo_on)) -> dict[str, Any]:
    return _call(svc.create_session, body.kind, body.actor)


@router.get("/scenarios")
def list_scenarios(svc: DemoService = Depends(demo_on),
                   _p: Principal = Depends(demo_admin)) -> dict[str, Any]:
    return _call(svc.list_scenarios)


@router.post("/scenarios/{scenario_id}/start")
def start(scenario_id: str, svc: DemoService = Depends(demo_on),
          _p: Principal = Depends(demo_admin)) -> dict[str, Any]:
    return _call(svc.start, scenario_id)


@router.post("/scenarios/{scenario_id}/steps/{step_id}/apply")
def apply_step(scenario_id: str, step_id: str, svc: DemoService = Depends(demo_on),
               _p: Principal = Depends(demo_admin)) -> dict[str, Any]:
    return _call(svc.apply_step, scenario_id, step_id)


@router.post("/scenarios/{scenario_id}/advance")
def advance(scenario_id: str, svc: DemoService = Depends(demo_on),
            _p: Principal = Depends(demo_admin)) -> dict[str, Any]:
    return _call(svc.advance, scenario_id)


@router.post("/scenarios/{scenario_id}/act")
def act(scenario_id: str, body: ActBody, svc: DemoService = Depends(demo_on),
        principal: Principal = Depends(demo_admin)) -> dict[str, Any]:
    return _call(svc.act, scenario_id, principal, body.actor, body.action, content=body.content,
                 report_id=body.report_id, message_type=body.message_type)


@router.post("/scenarios/{scenario_id}/reset")
def reset(scenario_id: str, svc: DemoService = Depends(demo_on),
          _p: Principal = Depends(demo_admin)) -> dict[str, Any]:
    return _call(svc.reset, scenario_id)


@router.post("/reset-all")
def reset_all(svc: DemoService = Depends(demo_on),
              _p: Principal = Depends(demo_admin)) -> dict[str, Any]:
    return _call(svc.reset_all)


@router.post("/closures", status_code=201)
def add_closure(body: ClosureBody, svc: DemoService = Depends(demo_on),
                _p: Principal = Depends(demo_admin)) -> dict[str, Any]:
    return _call(svc.add_closure, body.incident_id, edge_ids=body.edge_ids, geometry=body.geometry,
                 kind=body.kind)


@router.delete("/closures/{restriction_id}")
def remove_closure(restriction_id: str, incident_id: str, svc: DemoService = Depends(demo_on),
                   _p: Principal = Depends(demo_admin)) -> dict[str, Any]:
    return _call(svc.remove_closure, incident_id, restriction_id)
