"""Photo upload presign and local signed-URL blob endpoints (Req 23.5, 23.9, 28, 31).

* ``POST /api/photos/presign`` (Guest+): JPEG/PNG only (415), declared size <= 10 MB (413).
* ``PUT /api/photos/upload/{key}``: local-profile target of the presigned PUT. Verifies the HMAC
  signature, expiry, max size, declared content type and image magic bytes.
* ``GET /api/photos/upload/{key}?op=get...``: only reachable with a short-lived (15 min) signed URL.
  There is no listing endpoint; photos are never public.

Disabled by ``SAFEPATH_PHOTOS_ENABLED=false`` (503). Stored images may still contain EXIF
metadata; see docs/safety-limitations.md.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.adapters.blob import LocalBlobStore
from app.adapters.factory import make_blob_store
from app.adapters.ports import BlobStore, Principal, Role
from app.api.errors import envelope
from app.api.ratelimit import rate_limit
from app.auth.deps import require

router = APIRouter(prefix="/api/photos", tags=["reports"])

MAX_PHOTO_BYTES = 10 * 1024 * 1024
GET_TTL_SECONDS = 900
UPLOAD_BASE = "/api/photos/upload"
_ANY_ROLE = {Role.GUEST, Role.USER, Role.MODERATOR, Role.AUTHORIZED_OFFICIAL}
_TYPES = {"image/jpeg": ("jpg", b"\xff\xd8\xff"), "image/png": ("png", b"\x89PNG")}
_EXT_TYPE = {"jpg": "image/jpeg", "png": "image/png"}


def photos_enabled() -> bool:
    return os.environ.get("SAFEPATH_PHOTOS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _require_enabled() -> None:
    if not photos_enabled():
        raise HTTPException(status_code=503, detail="photo uploads are disabled")


def get_blob_store(app: FastAPI) -> BlobStore:
    store = getattr(app.state, "blob_store", None)
    if store is None:
        store = make_blob_store()
        if isinstance(store, LocalBlobStore):
            store.base_url = UPLOAD_BASE
        app.state.blob_store = store
    return store


def user_prefix(subject: str) -> str:
    """Per-user key namespace that does not reveal the raw subject id."""
    return "photos/" + hashlib.sha256(subject.encode()).hexdigest()[:24] + "/"


def photo_url_for(app: FastAPI, key: str | None) -> str | None:
    """Short-lived signed GET URL, only if ``key`` is set and the photo was actually uploaded."""
    if not key or not photos_enabled():
        return None
    try:
        store = get_blob_store(app)
        reader = getattr(store, "read", None)
        if reader is not None and reader(key) is None:
            return None
        return store.presign_get(key, GET_TTL_SECONDS)
    except (ValueError, OSError):
        return None


def _err(status: int, code: str, message: str, fields: list[dict[str, str]] | None = None) -> JSONResponse:
    return JSONResponse(envelope(code, message, fields), status_code=status)


@router.post("/presign")
def presign(request: Request, body: dict[str, Any],
            principal: Principal = Depends(require(_ANY_ROLE)),
            _limit: None = Depends(rate_limit("photo_presign"))) -> Any:
    _require_enabled()
    ctype = body.get("content_type")
    if not isinstance(ctype, str) or ctype.strip().lower() not in _TYPES:
        return _err(415, "unsupported_media_type", "content_type must be image/jpeg or image/png",
                    [{"field": "content_type", "problem": "unsupported"}])
    ctype = ctype.strip().lower()
    size = body.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        return _err(400, "validation_error", "size must be a positive integer number of bytes",
                    [{"field": "size", "problem": "must be a positive integer"}])
    if size > MAX_PHOTO_BYTES:
        return _err(413, "payload_too_large", f"photo exceeds {MAX_PHOTO_BYTES} bytes",
                    [{"field": "size", "problem": f"max {MAX_PHOTO_BYTES}"}])
    key = f"{user_prefix(principal.subject)}{uuid.uuid4().hex}.{_TYPES[ctype][0]}"
    up = get_blob_store(request.app).presign_put(key, ctype, size)
    return {
        "key": up.key, "method": up.method, "upload_url": up.url, "headers": dict(up.headers),
        "expires_at": up.expires_at.isoformat(), "max_bytes": up.max_bytes,
    }


def _forbidden() -> JSONResponse:
    return _err(403, "forbidden", "invalid or expired signed URL")


@router.put("/upload/{key:path}")
async def upload(key: str, request: Request, exp: int = 0, max: int = 0, ct: str = "",  # noqa: A002
                 sig: str = "", op: str = "") -> Any:
    _require_enabled()
    store = get_blob_store(request.app)
    if not isinstance(store, LocalBlobStore) or op != "put" or ct not in _TYPES:
        return _forbidden()
    if not store.verify("put", key, exp, sig, f"{ct}|{max}"):
        return _forbidden()
    if max < 1 or max > MAX_PHOTO_BYTES:
        return _forbidden()
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != ct:
        return _err(415, "unsupported_media_type", "Content-Type does not match the presigned type")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max:
        return _err(413, "payload_too_large", f"photo exceeds {max} bytes")
    data = await request.body()
    if len(data) > max:
        return _err(413, "payload_too_large", f"photo exceeds {max} bytes")
    if not data.startswith(_TYPES[ct][1]):
        return _err(415, "unsupported_media_type", "file contents do not match the declared image type")
    if store.read(key) is not None:
        return _err(409, "conflict", "this upload target was already used")
    store.write(key, data)
    return Response(status_code=204)


@router.get("/upload/{key:path}")
def download(key: str, request: Request, exp: int = 0, sig: str = "", op: str = "") -> Any:
    _require_enabled()
    store = get_blob_store(request.app)
    if not isinstance(store, LocalBlobStore) or op != "get" or not store.verify("get", key, exp, sig):
        return _forbidden()
    data = store.read(key)
    ctype = _EXT_TYPE.get(key.rsplit(".", 1)[-1].lower())
    if data is None or ctype is None:
        return _err(404, "not_found", "photo not found")
    return Response(data, media_type=ctype, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline"})
