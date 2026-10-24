"""POST /api/ai/summarize: optional, read-only AI assistance (Req 33, 13.9).

Guest and above, rate limited. The endpoint never writes anything and never changes a route,
restriction, report or Verification_Status. With AI off (the default) it returns the
deterministic template with ``ai_used = false``, so the UI works without a model.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

from app.adapters.ports import Principal, Role
from app.api.errors import envelope  # noqa: F401  (re-exported for symmetry with other routers)
from app.api.ratelimit import rate_limit
from app.auth.deps import require
from app.routing.validation import RequestValidationError
from app.services.ai import AIAssistant, build_assistant

router = APIRouter(prefix="/api/ai", tags=["ai"])

_ANY_ROLE = {Role.GUEST, Role.USER, Role.MODERATOR, Role.AUTHORIZED_OFFICIAL}


class NoticeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    notice_id: str | None = Field(default=None, max_length=128)
    text: str = Field(min_length=1, max_length=2000)
    source_record_id: str = Field(min_length=1, max_length=128)
    source_organization: str | None = Field(default=None, max_length=200)
    verification_status: str | None = Field(default=None, max_length=64)
    simulation_flag: bool = False


class RestrictionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str = Field(max_length=60)
    id: str = Field(max_length=128)
    source_record_id: str | None = Field(default=None, max_length=128)
    simulated: bool = False


class SummarizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["notice_summary", "route_flags", "report_categorize", "verification_explainer"]
    notices: list[NoticeIn] = Field(default_factory=list, max_length=20)
    hazard_flags: list[str] = Field(default_factory=list, max_length=50)
    restrictions_considered: list[RestrictionIn] = Field(default_factory=list, max_length=50)
    penalty_components: dict[str, float] = Field(default_factory=dict, max_length=20)
    sources: list[str] = Field(default_factory=list, max_length=50)
    text: str | None = Field(default=None, max_length=1000)


def get_assistant(app: FastAPI) -> AIAssistant:
    assistant = getattr(app.state, "ai_assistant", None)
    if assistant is None:
        assistant = app.state.ai_assistant = build_assistant()
    return assistant


def _bad(field: str, problem: str) -> RequestValidationError:
    return RequestValidationError(400, "validation_error", f"invalid field: {field}",
                                  [{"field": field, "problem": problem}])


@router.post("/summarize")
def summarize(request: Request, body: SummarizeRequest,
              principal: Principal = Depends(require(_ANY_ROLE)),
              _limit: None = Depends(rate_limit("ai"))) -> Any:
    assistant = get_assistant(request.app)
    try:
        if body.kind == "notice_summary":
            if not body.notices:
                raise _bad("notices", "at least one notice with a Source_Record is required")
            result = assistant.summarize_notices([n.model_dump() for n in body.notices])
        elif body.kind == "route_flags":
            result = assistant.explain_route_flags(
                body.hazard_flags, [r.model_dump() for r in body.restrictions_considered],
                body.penalty_components, body.sources)
        elif body.kind == "report_categorize":
            if not body.text or not body.text.strip():
                raise _bad("text", "report text is required")
            result = assistant.categorize_report(body.text)
        else:
            result = assistant.explain_verification()
    except ValueError as exc:  # unsourced or unverified notice
        raise _bad("notices", str(exc)) from exc
    return result.to_response()
