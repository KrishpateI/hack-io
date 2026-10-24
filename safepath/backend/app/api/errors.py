"""Error envelope, exception handlers and body-size cap (Req 28.4, 28.9, 42.2).

Envelope: ``{"error": {"code", "message", "fields": [{"field", "problem"}], "retry_after_seconds"}}``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError as FastAPIValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.adapters.ports import RepositoryUnavailable
from app.api.ratelimit import RateLimitExceeded
from app.routing.validation import RequestValidationError

log = logging.getLogger("safepath.api")

MAX_BODY_BYTES = 1_000_000  # 1 MB

_STATUS_CODES = {
    400: "validation_error", 401: "unauthenticated", 403: "forbidden", 404: "not_found",
    405: "method_not_allowed", 409: "conflict", 413: "payload_too_large",
    429: "rate_limited", 503: "service_unavailable",
}


def envelope(code: str, message: str, fields: list[dict[str, str]] | None = None,
             retry_after_seconds: int | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "fields": fields or [],
                      "retry_after_seconds": retry_after_seconds}}


def _field_name(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc if p not in ("body",)]
    return ".".join(parts) if parts else "body"


async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_CODES.get(exc.status_code, "error" if exc.status_code < 500 else "internal_error")
    message = exc.detail if isinstance(exc.detail, str) else code
    return JSONResponse(envelope(code, message), status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


async def _fastapi_validation(_: Request, exc: FastAPIValidationError) -> JSONResponse:
    fields = [{"field": _field_name(tuple(e.get("loc", ()))), "problem": str(e.get("msg", "invalid"))}
              for e in exc.errors()]
    msg = "invalid fields: " + ", ".join(f["field"] for f in fields) if fields else "invalid request"
    return JSONResponse(envelope("validation_error", msg, fields), status_code=400)


async def _route_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(exc.envelope(), status_code=exc.status)


async def _rate_limited(_: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        envelope("rate_limited", f"too many {exc.action} requests; retry later",
                 retry_after_seconds=exc.retry_after_seconds),
        status_code=429, headers={"Retry-After": str(exc.retry_after_seconds)},
    )


async def _unavailable(_: Request, exc: RepositoryUnavailable) -> JSONResponse:
    log.error("repository unavailable: %s", exc)
    return JSONResponse(envelope("service_unavailable", "service temporarily unavailable"),
                        status_code=503)


async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled error", exc_info=exc)
    return JSONResponse(envelope("internal_error", "internal server error"), status_code=500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(FastAPIValidationError, _fastapi_validation)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _route_validation)  # type: ignore[arg-type]
    app.add_exception_handler(RateLimitExceeded, _rate_limited)  # type: ignore[arg-type]
    app.add_exception_handler(RepositoryUnavailable, _unavailable)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled)


class BodySizeLimitMiddleware:
    """Pure ASGI middleware: reject bodies over ``max_bytes`` with a 413 envelope.

    Checks Content-Length first, then counts streamed bytes (chunked uploads).
    """

    def __init__(self, app: Any, max_bytes: int = MAX_BODY_BYTES,
                 path_limits: dict[str, int] | None = None):
        self.app = app
        self.max_bytes = max_bytes
        self.path_limits = path_limits or {}  # path prefix -> larger cap (photo uploads)

    async def _reject(self, send: Any, limit: int) -> None:
        body = json.dumps(envelope(
            "payload_too_large", f"request body exceeds {limit} bytes")).encode()
        await send({"type": "http.response.start", "status": 413, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.max_bytes
        for prefix, cap in self.path_limits.items():
            if scope.get("path", "").startswith(prefix):
                limit = cap
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    too_big = int(value) > limit
                except ValueError:
                    too_big = False
                if too_big:
                    await self._reject(send, limit)
                    return
        # Buffer up to the cap so oversized chunked bodies never reach the app.
        chunks: list[dict] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                chunks.append(message)
                break
            total += len(message.get("body", b""))
            if total > limit:
                await self._reject(send, limit)
                return
            chunks.append(message)
            if not message.get("more_body", False):
                break
        it = iter(chunks)

        async def replay() -> dict:
            try:
                return next(it)
            except StopIteration:
                return await receive()

        await self.app(scope, replay, send)
