"""Helpers shared by the earthquake, tornado and fire modules.

Geometry work happens in the graph's local metric projection so distances are in meters.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from app.disasters.base import IncidentContext
from app.models.domain import HazardReport, OfficialNotice, VerificationStatus
from app.routing.graph import Graph, _xy
from app.routing.restrictions import NOTICE_EXPIRY_GRACE, _to_projected

GUIDANCE_PATH = Path(__file__).resolve().parents[3] / "data" / "sources" / "official_guidance.json"

ACTIVE = "active"


# --------------------------------------------------------------------------- weights
def weight_section(name: str, defaults: Mapping[str, float]) -> dict[str, float]:
    """Read a routing-weights section, falling back to ``defaults`` when unreadable."""
    try:
        from app.routing.cost import load_weights

        return {**defaults, **dict(getattr(load_weights(), name))}
    except Exception:
        return dict(defaults)


# -------------------------------------------------------------------------- guidance
@lru_cache(maxsize=4)
def _load_guidance(path: str) -> tuple[dict[str, Any], ...]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return tuple(g for g in data.get("guidance", []) if isinstance(g, dict))
    except (OSError, ValueError):
        return ()


def official_guidance(hazard: str, path: Path | str | None = None) -> list[dict[str, Any]]:
    """Guidance records for a hazard from official_guidance.json (empty when unavailable)."""
    return [g for g in _load_guidance(str(path or GUIDANCE_PATH)) if g.get("hazard") == hazard]


def guidance_text(g: Mapping[str, Any]) -> str:
    notes = " ".join(str(n) for n in g.get("notes", []))
    return (
        f"Official guidance: {g.get('title', '')}. {notes} "
        f"(Source: {g.get('source_url', 'unknown')}, retrieved {g.get('retrieved_at', 'unknown')})"
    ).strip()


# ------------------------------------------------------------------------- records
def is_active(ctx: IncidentContext) -> bool:
    return str(ctx.incident.status).lower() == ACTIVE


def notice_in_force(n: OfficialNotice, ctx: IncidentContext) -> bool:
    """Active, same incident, not CBD-derived, with a Source_Record."""
    if n.incident_id != ctx.incident.incident_id or n.cbd_derived or not n.source_record_id:
        return False
    if n.effective_at > ctx.now:
        return False
    return n.expires_at is None or ctx.now <= n.expires_at + NOTICE_EXPIRY_GRACE


def report_live(r: HazardReport, ctx: IncidentContext) -> bool:
    return (
        r.incident_id == ctx.incident.incident_id
        and r.verification_status not in (VerificationStatus.RESOLVED, VerificationStatus.EXPIRED)
        and r.expires_at > ctx.now
    )


def report_confirmed(r: HazardReport, ctx: IncidentContext) -> bool:
    return report_live(r, ctx) and r.verification_status == VerificationStatus.OFFICIALLY_VERIFIED


# ------------------------------------------------------------------------- geometry
def project(geojson: Mapping | None) -> BaseGeometry | None:
    return _to_projected(geojson) if geojson else None


def project_point(lat: float, lon: float) -> Point:
    x, y = _xy(lon, lat)
    return Point(x, y)


def edges_within(graph: Graph, geom: BaseGeometry, meters: float) -> set[str]:
    """Edge ids whose line lies within ``meters`` of a projected geometry (0 = intersects)."""
    if not graph.loaded or graph.num_edges == 0 or geom is None or geom.is_empty:
        return set()
    graph._ensure_tree()
    assert graph._tree is not None
    hits = graph._tree.query(geom, predicate="dwithin", distance=float(meters))
    return {graph.edge_ids[int(i)] for i in hits}


class SingleSlotCache:
    """Remembers the result for the latest (graph, ctx) pair, compared by identity."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple[Any, Any] | None = None
        self._value: Any = None

    def get(self, graph: Any, ctx: Any, build: Callable[[], Any]) -> Any:
        with self._lock:
            if self._key is not None and self._key[0] is graph and self._key[1] is ctx:
                return self._value
        value = build()
        with self._lock:
            self._key, self._value = (graph, ctx), value
        return value
