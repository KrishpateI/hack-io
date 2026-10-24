"""GET /api/sources: search and filter the Source_Catalog (Req 10.2, 10.7)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.provenance import SourceRecord
from app.services import sources as svc

router = APIRouter(prefix="/api", tags=["sources"])


@router.get("/sources", response_model=list[SourceRecord])
def list_sources(
    q: str | None = None,
    data_classification: str | None = None,
    verification_status: str | None = None,
) -> list[SourceRecord]:
    try:
        return svc.search_sources(
            svc.load_catalog(), q, data_classification, verification_status
        )
    except svc.InvalidFilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
