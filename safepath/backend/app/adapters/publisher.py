"""In-process realtime publisher: fan-out to subscribed callbacks and a bounded backlog."""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Callable

from app.adapters.ports import Event

log = logging.getLogger(__name__)
Handler = Callable[[Event], None]


class InProcessPublisher:
    """Best-effort publisher. ``publish`` never raises; handler errors are logged."""

    def __init__(self, backlog: int = 500) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._published: list[tuple[str, Event]] = []
        self._backlog = backlog
        self._lock = threading.Lock()

    def subscribe(self, incident_id: str, handler: Handler) -> Callable[[], None]:
        """Register a handler; returns an unsubscribe function."""
        with self._lock:
            self._subs[incident_id].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._subs.get(incident_id, []):
                    self._subs[incident_id].remove(handler)

        return unsubscribe

    @property
    def published(self) -> list[tuple[str, Event]]:
        with self._lock:
            return list(self._published)

    def publish(self, incident_id: str, event: Event) -> None:
        try:
            with self._lock:
                self._published.append((incident_id, event))
                del self._published[: -self._backlog]
                handlers = list(self._subs.get(incident_id, []))
        except Exception:  # pragma: no cover - defensive
            log.exception("publisher bookkeeping failed")
            return
        for h in handlers:
            try:
                h(event)
            except Exception:
                log.exception("realtime handler failed for incident %s", incident_id)
