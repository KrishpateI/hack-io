"""Shared Hypothesis strategies for the SafePath property tests (design.md "Generators").

Import from a test module with ``from strategies import graphs, restriction_sets, ...``
(pytest puts ``tests/backend`` on ``sys.path``).

Public API (keep stable; tasks 6.14-6.30 import it):

* ``graphs(...)``            runtime ``app.routing.graph.Graph`` (small, valid, directed)
* ``graph_models(...)``      the underlying ``app.models.graph.Graph`` pydantic model
* ``restriction_sets(g)``    ``RestrictionSet`` blocking a random subset of ``g``'s edges
* ``restrictions(g)``        a ``Restriction`` overlay on a random subset of ``g``'s edges
* ``notices(...)``           ``OfficialNotice`` (all types, expiry, CBD-derived, no source record)
* ``incidents()``            ``Incident`` (all five types, boundary or none, simulation flag)
* ``reports(...)``           ``HazardReport`` (all verification statuses)
* ``coordinates()``          ``Coordinate(lat, lon, status)`` inside / boundary / outside / invalid
* ``raw_segments()``         a ``segments.jsonl`` record as written by scripts/process_streets.py
* ``incident_contexts(g)``   ``IncidentContext`` combining the above for graph ``g``

Everything is time-relative to ``NOW`` so results are deterministic.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, NamedTuple

from hypothesis import strategies as st

from app.disasters.base import IncidentContext, RestrictionSet
from app.geo import EAST, NORTH, SOUTH, WEST, haversine
from app.models.domain import (
    HazardReport,
    Incident,
    IncidentType,
    MobilityProfile,
    OfficialNotice,
    Restriction,
    RestrictionCause,
    TravelMode,
    VerificationStatus,
)
from app.models.graph import UNKNOWN, Edge, Graph as GraphModel, Node
from app.models.routing import LatLon
from app.routing.graph import Graph

__all__ = [
    "NOW", "Coordinate", "NOTICE_TYPES", "RESTRICTION_KINDS",
    "graphs", "graph_models", "build_runtime_graph", "restriction_sets", "restrictions",
    "notices", "incidents", "reports", "coordinates", "raw_segments", "incident_contexts",
]

NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
WALK_MPS = 1.4

NOTICE_TYPES = (
    "temporary_perimeter", "road_closure", "incident_destination", "shelter_in_place",
    "road_direction_restriction", "transportation_rule", "follow_instructions",
)
RESTRICTION_KINDS = (
    "closure", "impassable_polygon", "prohibited_area", "stairwell_closure", "road_direction",
    "module",
)

# Node grid: 8 x 8 cells of ~45 m, comfortably inside the extraction bbox and within snapping range.
_GRID = 8
_LAT0, _LON0, _STEP = 41.870, -87.650, 0.0004


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ================================================================== graphs
def _wkt(a: Node, b: Node) -> str:
    return f"LINESTRING ({a.lon:.7f} {a.lat:.7f}, {b.lon:.7f} {b.lat:.7f})"


def _maybe(value: st.SearchStrategy) -> st.SearchStrategy:
    """Known value or the string ``"Unknown"`` (Unknown shrinks first)."""
    return st.one_of(st.just(UNKNOWN), value)


_ELEVATION = st.floats(min_value=170.0, max_value=260.0, allow_nan=False, allow_infinity=False)
_TRAVEL_MODES = st.sampled_from([
    ["walking"], ["walking", "wheelchair"], ["walking", "wheelchair", "driving"],
    ["walking", "driving"], ["driving"],
])
_TRANSITIONS = st.sampled_from(["stairwell", "exit", "platform_stair", "ramp"])


@st.composite
def _edge_attrs(draw: st.DrawFn) -> dict[str, Any]:
    """Mixed Known/Unknown edge attributes with consistent bridge/tunnel/level flags."""
    level = draw(st.sampled_from(
        [UNKNOWN, "surface", "bridge", "tunnel", "lower-level", "elevated"]))
    bridge = draw(_maybe(st.just(level in ("bridge", "elevated"))))
    tunnel = draw(_maybe(st.just(level in ("tunnel", "lower-level"))))
    return {
        "road_type": draw(_maybe(st.sampled_from(["residential", "primary", "footway", "service"]))),
        "elevation_start": draw(_maybe(_ELEVATION)),
        "elevation_end": draw(_maybe(_ELEVATION)),
        "elevation_confidence": draw(st.sampled_from(["High", "Medium", "Low", UNKNOWN])),
        "bridge_flag": bridge,
        "tunnel_flag": tunnel,
        "infrastructure_level": level,
        "pedestrian_access": draw(_maybe(st.booleans())),
        "mobility_accessibility": draw(st.sampled_from(["accessible", "inaccessible", UNKNOWN])),
        "current_closure_status": draw(st.sampled_from(["open", UNKNOWN])),
        "hazard_flags": draw(st.lists(st.sampled_from(["steep", "narrow", "unlit"]),
                                      max_size=2, unique=True)),
        "travel_mode": draw(_TRAVEL_MODES),
    }


@st.composite
def graph_models(
    draw: st.DrawFn,
    min_nodes: int = 3,
    max_nodes: int = 12,
    *,
    multilevel: bool = False,
    connected: bool | None = None,
    one_way: bool = True,
) -> GraphModel:
    """Small random directed routing graph as the ``app.models.graph.Graph`` model.

    * 3-12 nodes (configurable, max 64) on a jittered local grid, ids ``n0..``.
    * ``connected`` True: strongly connected spanning backbone; False: no backbone (usually
      disconnected); None (default): drawn per example.
    * ``one_way`` allows extra edges that exist in one direction only.
    * ``multilevel`` adds indoor nodes (kind ``indoor``, levels floor-1/floor-2/platform) next to
      outdoor ``surface`` nodes. Different-level pairs only ever get explicit ``level_transition``
      edges, and at least one indoor<->outdoor transition pair is guaranteed.
    * Edges carry positive distance/time, Known/Unknown attributes (incl. Unknown elevation),
      consistent bridge/tunnel/lower-level flags and every mobility accessibility value.

    Always satisfies ``GraphModel.build`` invariants (no warnings, no level violations).
    """
    if not 2 <= min_nodes <= max_nodes <= _GRID * _GRID:
        raise ValueError("require 2 <= min_nodes <= max_nodes <= 64")
    n = draw(st.integers(min_nodes, max_nodes))
    cells = draw(st.lists(st.integers(0, _GRID * _GRID - 1), min_size=n, max_size=n, unique=True))
    is_connected = draw(st.booleans()) if connected is None else connected
    indoor = [False] * n
    if multilevel:
        indoor = [draw(st.booleans()) for _ in range(n)]
        indoor[0], indoor[1] = False, True  # guarantee one of each

    nodes: list[Node] = []
    for i, cell in enumerate(cells):
        lat = _LAT0 + (cell // _GRID) * _STEP
        lon = _LON0 + (cell % _GRID) * _STEP
        elev = draw(_maybe(_ELEVATION))
        if indoor[i]:
            level = draw(st.sampled_from(["floor-1", "floor-2", "platform"]))
            nodes.append(Node(
                node_id=f"n{i}", lat=lat, lon=lon, kind="indoor", level=level,
                elevation_m=elev, building_id=draw(st.sampled_from(["b1", "b2"])),
            ))
        else:
            kind = draw(st.sampled_from(["intersection", "pedestrian_junction", "building_entrance"]))
            nodes.append(Node(
                node_id=f"n{i}", lat=lat, lon=lon, kind=kind, elevation_m=elev,
                elevation_conf=draw(st.sampled_from(["High", "Medium", "Low", UNKNOWN])),
            ))

    # (from_idx, to_idx) directed pairs
    pairs: list[tuple[int, int]] = []
    if is_connected:
        for i in range(1, n):
            p = draw(st.integers(0, i - 1))
            pairs += [(p, i), (i, p)]
    if multilevel:
        pairs += [(0, 1), (1, 0)]
    extra = draw(st.lists(
        st.tuples(st.integers(0, n - 1), st.integers(0, n - 1), st.sampled_from(
            ["both", "forward"] if one_way else ["both"])),
        max_size=2 * n,
    ))
    for a, b, direction in extra:
        if a == b:
            continue
        pairs.append((a, b))
        if direction == "both":
            pairs.append((b, a))

    edges: list[Edge] = []
    for k, (a, b) in enumerate(pairs):
        na, nb = nodes[a], nodes[b]
        if na.level != nb.level:
            kind, trans = "level_transition", draw(_TRANSITIONS)
        else:
            kind, trans = ("indoor" if na.kind == "indoor" else "street"), None
        meters = haversine(na.lat, na.lon, nb.lat, nb.lon) * draw(st.sampled_from([1.0, 1.2, 1.5]))
        dist = round(max(meters, 1.0), 3)
        edges.append(Edge(
            edge_id=f"e{k}", from_node=na.node_id, to_node=nb.node_id, geometry=_wkt(na, nb),
            distance_meters=dist, estimated_travel_time_seconds=round(dist / WALK_MPS, 3),
            source="osm", data_timestamp=NOW, classification="Static", edge_kind=kind,
            transition_type=trans, **draw(_edge_attrs()),
        ))
    model = GraphModel.build(nodes, edges)
    assert not model.warnings
    return model


def build_runtime_graph(model: GraphModel, version: str = "test") -> Graph:
    """Wrap a graph model as the runtime ``app.routing.graph.Graph`` (same helper as the tests)."""
    return Graph._from_model(model, SimpleNamespace(version=version), {})


@st.composite
def graphs(draw: st.DrawFn, *args: Any, **kwargs: Any) -> Graph:
    """Runtime ``Graph`` built from ``graph_models(*args, **kwargs)`` (see its documentation)."""
    return build_runtime_graph(draw(graph_models(*args, **kwargs)))


# ================================================================== restrictions
_IDENT = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8)


@st.composite
def _causes(draw: st.DrawFn) -> RestrictionCause:
    return RestrictionCause(
        kind=draw(st.sampled_from(RESTRICTION_KINDS)),
        id=draw(_IDENT),
        source_record_id=draw(st.one_of(st.none(), _IDENT)),
        simulated=draw(st.booleans()),
    )


@st.composite
def restriction_sets(draw: st.DrawFn, graph: Graph, *, max_blocked: int | None = None) -> RestrictionSet:
    """Random (possibly empty or total) subset of ``graph``'s edges blocked, 1-3 causes each."""
    ids = list(graph.edge_ids)
    limit = len(ids) if max_blocked is None else min(max_blocked, len(ids))
    chosen = draw(st.lists(st.sampled_from(ids), unique=True, max_size=limit)) if ids else []
    blocked = {eid: tuple(draw(st.lists(_causes(), min_size=1, max_size=3))) for eid in chosen}
    return RestrictionSet(blocked, draw(st.integers(0, 50)))


@st.composite
def restrictions(draw: st.DrawFn, graph: Graph, incident_id: str = "inc-1") -> Restriction:
    """A ``Restriction`` overlay on a random subset of ``graph``'s edges (no geometry)."""
    ids = list(graph.edge_ids)
    chosen = draw(st.lists(st.sampled_from(ids), unique=True, max_size=len(ids))) if ids else []
    return Restriction(
        restriction_id=draw(_IDENT), incident_id=incident_id,
        kind=draw(st.sampled_from(RESTRICTION_KINDS)), edge_ids=chosen,
        source_report_id=draw(st.one_of(st.none(), _IDENT)),
        source_record_id=draw(st.one_of(st.none(), _IDENT)),
        simulated=draw(st.booleans()), active=draw(st.booleans()),
    )


# ================================================================== geometry helpers
@st.composite
def _polygon(draw: st.DrawFn) -> dict[str, Any]:
    """Small GeoJSON polygon (lon/lat) inside the extraction area."""
    lon = draw(st.floats(WEST + 0.005, EAST - 0.005))
    lat = draw(st.floats(SOUTH + 0.005, NORTH - 0.005))
    d = draw(st.floats(0.0005, 0.004))
    ring = [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


_INSIDE_LAT = st.floats(SOUTH, NORTH, allow_nan=False)
_INSIDE_LON = st.floats(WEST, EAST, allow_nan=False)


# ================================================================== incidents / notices / reports
@st.composite
def incidents(draw: st.DrawFn, incident_type: IncidentType | None = None) -> Incident:
    """Incident of any of the five types, with or without a boundary polygon, simulated or not."""
    created = NOW - timedelta(minutes=draw(st.integers(0, 600)))
    return Incident(
        incident_id=f"inc-{draw(_IDENT)}",
        incident_type=incident_type or draw(st.sampled_from(list(IncidentType))),
        geographic_boundary=draw(st.one_of(st.none(), _polygon())),
        status=draw(st.sampled_from(["active", "resolved"])),
        source=draw(st.sampled_from(["official", "demo", "manual"])),
        created_at=created,
        updated_at=created + timedelta(minutes=draw(st.integers(0, 60))),
        simulation_flag=draw(st.booleans()),
        scenario_id=draw(st.one_of(st.none(), _IDENT)),
    )


@st.composite
def notices(
    draw: st.DrawFn,
    incident_id: str = "inc-1",
    edge_ids: tuple[str, ...] | list[str] = (),
    now: datetime = NOW,
) -> OfficialNotice:
    """OfficialNotice of any type.

    ``effective_at`` is within +-8 h of ``now`` (so not-yet-effective ones occur), ``expires_at``
    is None or 1-900 min after it (so notices expired before ``now`` occur). ``source_record_id`` may be ``""`` (missing source
    record) and ``cbd_derived`` may be True. ``edge_ids`` are a random subset of the given ids.
    """
    effective = now + timedelta(minutes=draw(st.integers(-480, 480)))
    expires = draw(st.one_of(
        st.none(), st.integers(1, 900).map(lambda m: effective + timedelta(minutes=m)),
    ))
    ids = list(edge_ids)
    return OfficialNotice(
        notice_id=f"notice-{draw(_IDENT)}", incident_id=incident_id,
        notice_type=draw(st.sampled_from(NOTICE_TYPES)), text=draw(st.text(max_size=40)),
        geometry=draw(st.one_of(st.none(), _polygon())),
        edge_ids=draw(st.lists(st.sampled_from(ids), unique=True)) if ids else [],
        source_organization=draw(st.sampled_from(["OEMC", "CDOT", "NWS"])),
        source_url="https://example.invalid/notice",
        source_record_id=draw(st.one_of(st.just(""), _IDENT)),
        effective_at=effective, expires_at=expires,
        published_at=effective - timedelta(minutes=5), retrieved_at=effective,
        verification_status=draw(st.sampled_from(["Officially_Verified", "Unverified"])),
        simulation_flag=draw(st.booleans()), cbd_derived=draw(st.booleans()),
    )


@st.composite
def reports(
    draw: st.DrawFn,
    incident_id: str = "inc-1",
    edge_ids: tuple[str, ...] | list[str] = (),
    now: datetime = NOW,
    status: VerificationStatus | None = None,
) -> HazardReport:
    """HazardReport with any verification status (or the given one), inside the extraction area."""
    created = now - timedelta(minutes=draw(st.integers(0, 300)))
    ids = list(edge_ids)
    return HazardReport(
        report_id=f"rep-{draw(_IDENT)}", incident_id=incident_id,
        category=draw(st.sampled_from(["flooding", "blocked_road", "fire", "debris", "other"])),
        latitude=draw(_INSIDE_LAT), longitude=draw(_INSIDE_LON),
        affected_edge_ids=draw(st.lists(st.sampled_from(ids), unique=True, max_size=3)) if ids else [],
        verification_status=status or draw(st.sampled_from(list(VerificationStatus))),
        confirmations=draw(st.integers(0, 10)), disputes=draw(st.integers(0, 10)),
        created_at=created, expires_at=created + timedelta(minutes=draw(st.integers(-30, 600))),
        simulated=draw(st.booleans()),
        source_record_id=draw(st.one_of(st.none(), _IDENT)),
    )


# ================================================================== coordinates
class Coordinate(NamedTuple):
    """A (lat, lon) pair plus the classification it must receive, fixed by construction.

    ``status`` is one of ``"inside"`` (includes exact boundary), ``"outside-region"`` or
    ``"invalid"`` (non-numeric, NaN/inf, bool, or out of the +-90/+-180 range).
    """

    lat: Any
    lon: Any
    status: str


_FLOAT_EDGE = dict(allow_nan=False, allow_infinity=False)


@st.composite
def coordinates(draw: st.DrawFn) -> Coordinate:
    """Inside, exact-boundary (corners/edges), outside-region, or invalid coordinates."""
    kind = draw(st.sampled_from(["inside", "boundary", "outside", "invalid"]))
    if kind == "inside":
        return Coordinate(draw(_INSIDE_LAT), draw(_INSIDE_LON), "inside")
    if kind == "boundary":
        lat = draw(st.sampled_from([SOUTH, NORTH]))
        lon = draw(st.sampled_from([WEST, EAST]))
        if draw(st.booleans()):  # on a single edge only
            lat, lon = (draw(_INSIDE_LAT), lon) if draw(st.booleans()) else (lat, draw(_INSIDE_LON))
        return Coordinate(lat, lon, "inside")
    if kind == "outside":
        # valid numbers, at least one beyond the bbox, nothing beyond +-90/+-180
        lat_out = draw(st.one_of(st.floats(-90, SOUTH - 1e-6, **_FLOAT_EDGE),
                                 st.floats(NORTH + 1e-6, 90, **_FLOAT_EDGE)))
        lon_out = draw(st.one_of(st.floats(-180, WEST - 1e-6, **_FLOAT_EDGE),
                                 st.floats(EAST + 1e-6, 180, **_FLOAT_EDGE)))
        which = draw(st.sampled_from(["lat", "lon", "both"]))
        lat = lat_out if which in ("lat", "both") else draw(_INSIDE_LAT)
        lon = lon_out if which in ("lon", "both") else draw(_INSIDE_LON)
        return Coordinate(lat, lon, "outside-region")
    bad = st.one_of(
        st.just(float("nan")), st.just(float("inf")), st.just(float("-inf")), st.none(),
        st.booleans(), st.text(max_size=6),
        st.floats(90.0001, 1e6, **_FLOAT_EDGE), st.floats(-1e6, -90.0001, **_FLOAT_EDGE),
    )
    good_lat, good_lon = draw(_INSIDE_LAT), draw(_INSIDE_LON)
    which = draw(st.sampled_from(["lat", "lon", "both"]))
    lat = draw(bad) if which in ("lat", "both") else good_lat
    lon = draw(bad) if which in ("lon", "both") else good_lon
    # A bad latitude beyond 90 is invalid, but a bad *longitude* of 91..180 is merely outside;
    # restrict the longitude fallback so classification stays unambiguous.
    if isinstance(lon, float) and math.isfinite(lon) and abs(lon) <= 180 and which != "lat":
        lon = float("nan")
    return Coordinate(lat, lon, "invalid")


# ================================================================== raw segments
_SEG_SOURCES = {
    "cdp_centerlines": ("cdp", "cdp_id"),
    "osm_streets": ("osm", "osm_id"),
    "osm_pedestrian": ("osm", "osm_id"),
}


@st.composite
def raw_segments(draw: st.DrawFn) -> dict[str, Any]:
    """One merged street segment as in ``data/processed/streets/segments.jsonl.gz``.

    Each normalizable attribute (see ``process_streets.ATTRIBUTES``) is Known, the string
    ``"Unknown"`` or *omitted entirely* (consumers must use ``.get(key, "Unknown")``). Sources
    may be CDP, OSM or both; both-source segments may carry attribute conflicts.
    """
    lon = draw(st.floats(WEST, EAST - 0.002, **_FLOAT_EDGE))
    lat = draw(st.floats(SOUTH, NORTH - 0.002, **_FLOAT_EDGE))
    npts = draw(st.integers(2, 4))
    pts = [(lon, lat)]
    for _ in range(npts - 1):
        pts.append((pts[-1][0] + draw(st.floats(0.00005, 0.0005)),
                    pts[-1][1] + draw(st.floats(-0.0004, 0.0004))))
    dist = sum(haversine(a[1], a[0], b[1], b[0]) for a, b in zip(pts, pts[1:]))
    sources = draw(st.sampled_from([
        ["cdp_centerlines"], ["osm_streets"], ["osm_pedestrian"], ["cdp_centerlines", "osm_streets"],
    ]))
    attrs: dict[str, Any] = {}
    known = {
        "name": st.sampled_from(["STATE ST", "WABASH AVE", "LAKE ST", "RANDOLPH ST"]),
        "road_type": st.sampled_from(["residential", "primary", "footway", "steps", "cdp_class_3"]),
        "oneway": st.sampled_from(["no", "forward", "reverse"]),
        "pedestrian_access": st.booleans(),
        "bridge_flag": st.booleans(),
        "tunnel_flag": st.booleans(),
        "layer": st.integers(-2, 2),
        "infrastructure_level": st.sampled_from(["surface", "lower-level", "elevated", "bridge", "tunnel"]),
        "river_crossing": st.just(True),
        "mobility_accessibility": st.sampled_from(["accessible", "inaccessible"]),
    }
    for key, strat in known.items():
        mode = draw(st.sampled_from(["known", "unknown", "omitted"]))
        if mode == "known":
            attrs[key] = draw(strat)
        elif mode == "unknown":
            attrs[key] = UNKNOWN
    conflicts: list[dict[str, Any]] = []
    if len(sources) == 2 and draw(st.booleans()):
        conflicts.append({"attribute": "oneway", "cdp": "no", "osm": "forward",
                          "retained_source": "osm_streets"})
    seg_id = f"seg-{draw(_IDENT)}"
    return {
        "segment_id": seg_id,
        "geometry": "LINESTRING (" + ", ".join(f"{x:.7f} {y:.7f}" for x, y in pts) + ")",
        "distance_meters": round(dist, 3),
        **attrs,
        "travel_mode": draw(st.sampled_from([["walking"], ["walking", "wheelchair"], ["driving"],
                                             ["walking", "wheelchair", "driving"]])),
        "sources": sources,
        "source_ids": {s: draw(_IDENT) for s in sources},
        "retained_source": sources[0],
        "attribute_sources": {k: sources[-1] for k in attrs},
        "conflicts": conflicts,
        "warnings": [f"{seg_id}: oneway conflict"] if conflicts else [],
        "crs": "EPSG:4326",
        "classification": "Static",
    }


# ================================================================== incident contexts
@st.composite
def incident_contexts(
    draw: st.DrawFn,
    graph: Graph,
    *,
    max_notices: int = 3,
    max_reports: int = 3,
    max_overlays: int = 2,
) -> IncidentContext:
    """``IncidentContext`` for ``graph``: origin/destination at graph nodes (destination optional),
    random mobility profile / mode, and notices, reports and overlays for the same incident that
    reference the graph's edge ids."""
    incident = draw(incidents())
    iid = incident.incident_id
    ids = tuple(graph.edge_ids)

    def point() -> st.SearchStrategy[LatLon]:
        if graph.num_nodes:
            return st.integers(0, graph.num_nodes - 1).map(
                lambda i: LatLon(latitude=float(graph.node_lat[i]), longitude=float(graph.node_lon[i])))
        return st.builds(LatLon, latitude=_INSIDE_LAT, longitude=_INSIDE_LON)

    return IncidentContext(
        incident=incident,
        now=NOW,
        origin=draw(point()),
        destination=draw(st.one_of(st.none(), point())),
        profile=draw(st.sampled_from(list(MobilityProfile))),
        mode=draw(st.sampled_from(list(TravelMode))),
        notices=tuple(draw(st.lists(notices(iid, ids), max_size=max_notices))),
        reports=tuple(draw(st.lists(reports(iid, ids), max_size=max_reports))),
        overlays=tuple(draw(st.lists(restrictions(graph, iid), max_size=max_overlays))),
    )
