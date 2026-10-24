"""Realtime endpoints: catch-up (GET /api/events), WebSocket and SSE (local profile).

Incident event streams carry public views only and are readable without a token. A token, when
supplied (``Authorization: Bearer`` or ``?token=``), must be valid; otherwise the subscribe is
rejected (401 for HTTP, close code 1008 for WebSocket).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.adapters.factory import make_publisher
from app.adapters.ports import AdapterNotAvailable, Event, Unauthenticated
from app.communication.events import EventBus, event_to_dict

log = logging.getLogger(__name__)
router = APIRouter(tags=["events"])

SSE_KEEPALIVE_SECONDS = 15.0


def get_event_bus(app: Any) -> EventBus | None:
    """Lazily build the app-wide EventBus from ``app.state`` repository and publisher."""
    st = app.state
    bus = getattr(st, "event_bus", None)
    if bus is not None:
        return bus
    repo = getattr(st, "repository", None)
    if repo is None:
        return None
    publisher = getattr(st, "publisher", None)
    if publisher is None:
        try:
            publisher = make_publisher()
        except AdapterNotAvailable:
            return None
        st.publisher = publisher
    st.event_bus = EventBus(repo, publisher)
    return st.event_bus


def _check_token(app: Any, token: str | None) -> None:
    if not token:
        return
    provider = getattr(app.state, "auth", None)
    if provider is None:
        raise Unauthenticated("authentication service unavailable")
    provider.verify(token)


def _bearer(header: str) -> str | None:
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def _subscribe(app: Any, incident_id: str):
    """Subscribe an asyncio queue to the incident; returns (queue, unsubscribe)."""
    publisher = getattr(app.state, "publisher", None)
    if publisher is None or not hasattr(publisher, "subscribe"):
        raise HTTPException(status_code=503, detail="realtime channel unavailable")
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)

    def put(event: Event) -> None:
        if not queue.full():
            queue.put_nowait(event)

    def handler(event: Event) -> None:  # called on the publishing thread
        try:
            loop.call_soon_threadsafe(put, event)
        except RuntimeError:  # loop closed
            pass

    return queue, publisher.subscribe(incident_id, handler)


@router.get("/api/events")
def catch_up(request: Request, incident_id: str = Query(min_length=1, max_length=64),
             since: str | None = Query(default=None, max_length=64),
             limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    """Public events of one incident stored after ``since`` (an event_id), oldest first."""
    bus = get_event_bus(request.app)
    if bus is None:
        raise HTTPException(status_code=503, detail="event store unavailable")
    events = bus.since(incident_id, since, limit)
    return {"incident_id": incident_id, "events": [event_to_dict(e) for e in events],
            "last_event_id": events[-1].id if events else since}


@router.websocket("/ws/incidents/{incident_id}")
async def ws_incident(websocket: WebSocket, incident_id: str) -> None:
    token = websocket.query_params.get("token") or _bearer(websocket.headers.get("authorization", ""))
    try:
        _check_token(websocket.app, token)
    except Unauthenticated:
        await websocket.close(code=1008)
        return
    get_event_bus(websocket.app)  # ensures a publisher exists
    try:
        queue, unsubscribe = _subscribe(websocket.app, incident_id)
    except HTTPException:
        await websocket.close(code=1011)
        return
    await websocket.accept()
    try:
        async def reader() -> None:  # detect client disconnects
            while True:
                await websocket.receive_text()

        read_task = asyncio.create_task(reader())
        try:
            while True:
                get_task = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({get_task, read_task}, return_when=asyncio.FIRST_COMPLETED)
                if get_task in done:
                    await websocket.send_json(event_to_dict(get_task.result()))
                if read_task in done:
                    get_task.cancel()
                    break
        finally:
            read_task.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        unsubscribe()


@router.get("/sse/incidents/{incident_id}")
async def sse_incident(request: Request, incident_id: str,
                       token: str | None = Query(default=None, max_length=2048)) -> StreamingResponse:
    try:
        _check_token(request.app, token or _bearer(request.headers.get("authorization", "")))
    except Unauthenticated as exc:
        raise HTTPException(status_code=401, detail=str(exc) or "authentication required",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    get_event_bus(request.app)
    queue, unsubscribe = _subscribe(request.app, incident_id)

    async def stream():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(event_to_dict(event))}\n\n"
        finally:
            unsubscribe()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
