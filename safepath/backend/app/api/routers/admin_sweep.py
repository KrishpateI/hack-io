"""POST /api/admin/sweep: run the Sweeper on demand (Moderator or Authorized_Official only)."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.adapters.ports import Principal, Role
from app.auth.deps import require
from app.services.sweeper import run_sweep

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/sweep")
async def sweep(request: Request,
                _principal: Principal = Depends(require({Role.MODERATOR, Role.AUTHORIZED_OFFICIAL}))
                ) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(run_sweep, None, app=request.app)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="storage unavailable") from None
