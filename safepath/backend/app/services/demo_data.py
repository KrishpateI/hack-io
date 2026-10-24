"""SIMULATED demo scenario data: seeding, step application and reset (Req 37-40, 7.1, 20.5).

Scenario definitions live in ``data/demo/scenarios.json`` (static, no edge ids). :func:`seed`
resolves them against the *loaded* routing graph (the real artifact, or the SIMULATED fixture
graph when no real artifact exists; the choice is recorded) and stores the resolved scenario in the
``demo_scenarios`` table (pk ``scenarios``, sk ``scenario_id``). The Demo_Admin_Console and the
DemoService (task 11.4) drive a scenario by reading that row: ``steps`` are data, ``resolved``
holds the real edge ids, the SIMULATED flood polygon and the SIMULATED debris zones.

Everything created here is flagged ``simulation_flag``/``simulated`` and labeled SIMULATED. Nothing
claims that any real street is closed, that any real area floods, or that any real building is
unsafe; no water-depth values are produced. Records are attributed to a scenario through the
Incident's ``scenario_id`` and the deterministic ``sim-`` ids (notices and overlays are keyed by the
scenario's ``incident_id``), which is what :func:`reset_scenario` deletes by.

``seed`` is idempotent: a scenario whose incident and resolved row both exist is left untouched.
"""
from __future__ import annotations

import json

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import transform, unary_union

from app.adapters.ports import Repository, Role
from app.adapters.publisher import InProcessPublisher
from app.auth.accounts import USERS_TABLE, AccountService
from app.communication.events import EventBus
from app.models.domain import Incident, IncidentType, Restriction
from app.routing import indoor as indoor_mod
from app.routing.cost import load_weights_state
from app.routing.graph import _COS_REF, _M_PER_DEG, Graph, _xy, decode_geometry
from app.routing.service import InMemoryContextProvider, RoutingService
from app.services.incidents import INCIDENTS_PK, INCIDENTS_TABLE, IncidentService
from app.services.overlays import DuplicateRestriction, OverlayService, strip_keys

SCENARIOS_PATH = Path(__file__).resolve().parents[3] / "data" / "demo" / "scenarios.json"
DEMO_TABLE = "demo_scenarios"
DEMO_PK = "scenarios"
FIXTURE_SOURCE_ID = "SIMULATED-FIXTURE-GRAPH"
SIMULATED = "SIMULATED"
DESTINATION_LABEL = "SIMULATION DESTINATION \u2014 NOT an official evacuation shelter"

#: Tables keyed ``pk = incident_id`` that hold a scenario's records.
INCIDENT_TABLES = ("notices", "overlays", "overlay_meta", "reports", "messages", "events", "checkins")
#: Tables whose rows carry an ``incident_id`` attribute instead of keying on it.
INCIDENT_ATTR_TABLES = ("message_index", "checkin_locations")


class DemoDataError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- definitions
def load_definitions(path: Path | str | None = None) -> dict[str, Any]:
    return json.loads(Path(path or SCENARIOS_PATH).read_text(encoding="utf-8"))


def scenario_ids(defs: dict[str, Any] | None = None) -> list[str]:
    return [s["scenario_id"] for s in (defs or load_definitions())["scenarios"]]


# --------------------------------------------------------------------------------- graph
def load_demo_graph(root: Path | str | None = None) -> tuple[Graph, dict[str, Any]]:
    """Real graph artifact if loadable, else the SIMULATED fixture graph. Records which."""
    from app.routing.runtime import FIXTURE_GRAPH_ROOT, graph_root

    g = Graph.load(Path(root) if root else graph_root())
    if not g.loaded and FIXTURE_GRAPH_ROOT.is_dir():
        g = Graph.load(FIXTURE_GRAPH_ROOT)
    if not g.loaded:
        raise DemoDataError("no routing graph (real or SIMULATED fixture) could be loaded")
    return g, graph_info(g)


def graph_info(g: Graph) -> dict[str, Any]:
    fixture = bool(g.manifest and FIXTURE_SOURCE_ID in (g.manifest.source_record_ids or []))
    return {
        "used": "simulated_fixture" if fixture else "real",
        "simulated": fixture,
        "version": g.version,
        "note": ("SIMULATED fixture graph: synthetic streets, not real data." if fixture
                 else "Real graph artifact (OSM / Chicago Data Portal derived)."),
    }


# ------------------------------------------------------------------------------ geometry
def _unxy(x: float, y: float) -> tuple[float, float]:
    return x / (_COS_REF * _M_PER_DEG), y / _M_PER_DEG


def _to_lonlat(geom):
    return transform(lambda x, y: (x / (_COS_REF * _M_PER_DEG), y / _M_PER_DEG), geom)


def _round_geojson(obj: Any, nd: int = 6) -> Any:
    if isinstance(obj, (list, tuple)):
        return [_round_geojson(o, nd) for o in obj]
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round_geojson(v, nd) for k, v in obj.items()}
    return obj


def _lonlat_geojson(geom_m) -> dict[str, Any]:
    return _round_geojson(mapping(_to_lonlat(geom_m)))


def circle_polygon(lat: float, lon: float, radius_m: float) -> dict[str, Any]:
    x, y = _xy(lon, lat)
    return _lonlat_geojson(Point(x, y).buffer(radius_m, 24))


def _edge_line(graph: Graph, i: int) -> LineString:
    coords = decode_geometry(graph.edge(i).geometry)
    if coords is None:
        a, b = int(graph.edge_from[i]), int(graph.edge_to[i])
        coords = [(float(graph.node_lon[a]), float(graph.node_lat[a])),
                  (float(graph.node_lon[b]), float(graph.node_lat[b]))]
    return LineString([_xy(x, y) for x, y in coords])


def _numeric(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def flood_highlight(graph: Graph, center: dict[str, float], cfg: dict[str, Any]) -> dict[str, Any]:
    """SIMULATED flood polygon from real street geometry and elevation (Req 37.3, 37.6).

    Edges with lower-level infrastructure or ``tunnel_flag`` set, or mean elevation in the lowest
    ``elevation_percentile`` percent of graph edges with known elevation, within ``radius_m`` of
    the centre. Output carries NO water-depth values.
    """
    assert graph.model is not None
    n = graph.num_edges
    elev = np.full(n, np.nan)
    lower = np.zeros(n, dtype=bool)
    street = np.zeros(n, dtype=bool)
    for i, e in enumerate(graph.model.edges):
        a, b = _numeric(e.elevation_start), _numeric(e.elevation_end)
        if a is not None and b is not None:
            elev[i] = (a + b) / 2.0
        lower[i] = e.infrastructure_level == "lower-level" or e.tunnel_flag is True
        street[i] = e.edge_kind == "street"
    known = ~np.isnan(elev)
    pct = float(cfg.get("elevation_percentile", 20))
    threshold = float(np.percentile(elev[known], pct)) if known.any() else None
    low = np.zeros(n, dtype=bool)
    if threshold is not None:
        low[known] = elev[known] <= threshold
    mid_lat = (graph.node_lat[graph.edge_from] + graph.node_lat[graph.edge_to]) / 2.0
    mid_lon = (graph.node_lon[graph.edge_from] + graph.node_lon[graph.edge_to]) / 2.0
    cx, cy = _xy(center["longitude"], center["latitude"])
    dx, dy = mid_lon * _COS_REF * _M_PER_DEG - cx, mid_lat * _M_PER_DEG - cy
    near = np.hypot(dx, dy) <= float(cfg.get("radius_m", 1500))
    sel = np.flatnonzero((low | lower) & near & street)
    label = cfg.get("label", f"{SIMULATED} flood-prone segments (modeled; no water depth shown)")
    props = {
        "simulated": True, "classification": "Simulated", "label": label,
        "basis": cfg.get("basis", ""), "edge_count": int(len(sel)),
        "elevation_percentile": pct, "mean_elevation_threshold_m": (
            round(threshold, 3) if threshold is not None else None),
    }
    geometry = None
    if len(sel):
        buf = float(cfg.get("buffer_m", 12))
        union = unary_union([_edge_line(graph, int(i)).buffer(buf) for i in sel])
        geometry = _lonlat_geojson(union.simplify(float(cfg.get("simplify_m", 3))))
    return {
        "type": "FeatureCollection",
        "features": ([{"type": "Feature", "geometry": geometry, "properties": props}]
                     if geometry else []),
        "properties": props,
        "edge_ids": sorted(graph.edge_ids[int(i)] for i in sel)[:5000],
    }


# ----------------------------------------------------------------------------- probing
_weights_state = None


def _weights():
    global _weights_state
    if _weights_state is None:
        _weights_state = load_weights_state()
    return _weights_state.require()


class _Probe:
    """Throw-away routing context used at seed time to pick edges that really matter."""

    def __init__(self, graph: Graph, incident: Incident, notices: Sequence[Any], now: datetime,
                 route_request: dict[str, Any]) -> None:
        self.graph, self.incident, self.notices, self.now = graph, incident, list(notices), now
        self.rr = route_request

    def route(self, origin: dict, dest: dict, restrictions: Sequence[Restriction] = ()):
        prov = InMemoryContextProvider()
        prov.add_incident(self.incident)
        for n in self.notices:
            prov.add_notice(n)
        for r in restrictions:
            prov.add_overlay(r)
        svc = RoutingService(lambda: self.graph, _weights, prov, clock=lambda: self.now)
        req = svc.validate({
            "incident_id": self.incident.incident_id,
            "origin": {"latitude": origin["latitude"], "longitude": origin["longitude"]},
            "destination": {"latitude": dest["latitude"], "longitude": dest["longitude"]},
            "travel_mode": self.rr.get("travel_mode", "walking"),
            "mobility_profile": self.rr.get("mobility_profile", "standard"),
            "alternatives": int(self.rr.get("alternatives", 1)),
        })
        return svc.calculate(req)


def route_edge_ids(resp) -> list[str]:
    if not resp.route_count or not resp.routes or not resp.routes[0].geometry.coordinates:
        return []
    return [p.edge_id for p in resp.routes[0].edge_penalties]


def _closure(incident_id: str, rid: str, edge_ids: list[str] | None = None,
             geometry: dict | None = None, kind: str = "closure") -> Restriction:
    return Restriction(restriction_id=rid, incident_id=incident_id, kind=kind,  # type: ignore[arg-type]
                       edge_ids=edge_ids or [], geometry=geometry, simulated=True,
                       source_record_id="SIMULATED-DEMO-SOURCE")


def pick_closure_edge(graph: Graph, probe: _Probe, origin: dict, dest: dict, before_ids: list[str],
                      restrictions: Sequence[Restriction], incident_id: str, rid: str
                      ) -> tuple[str, list[str]] | None:
    """An edge of the current best route whose closure still leaves a different route.

    Returns (edge_id, new_route_edge_ids), searching outward from the middle of the route and
    preferring edges of at least 20 m.
    """
    if not before_ids:
        return None
    mid = len(before_ids) // 2
    order = sorted(range(len(before_ids)), key=lambda i: (abs(i - mid), i))
    if len(before_ids) > 6:
        order = [i for i in order if 1 < i < len(before_ids) - 2]
    idx = {eid: i for i, eid in enumerate(graph.edge_ids)}
    long_first = sorted(order, key=lambda i: (graph.edge_dist[idx[before_ids[i]]] < 20,))
    for i in long_first[:25]:
        eid = before_ids[i]
        try:
            resp = probe.route(origin, dest, [*restrictions, _closure(incident_id, rid, [eid])])
        except Exception:  # a probe failure just rejects the candidate
            continue
        after = route_edge_ids(resp)
        if after and eid not in after and after != before_ids:
            return eid, after
    return None


# --------------------------------------------------------------------- earthquake debris
def _building_polygons(graph: Graph, route_m: LineString, margin_m: float, min_area: float = 150.0):
    """(id, props, metric polygon, distance to route) for footprints near the route."""
    minx, miny, maxx, maxy = route_m.bounds
    out = []
    for f in graph.layers.get("buildings", {}).get("features", []):
        g = f.get("geometry") or {}
        try:
            ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
            x, y = _xy(ring[0][0], ring[0][1])
        except (KeyError, IndexError, TypeError):
            continue
        if not (minx - margin_m <= x <= maxx + margin_m and miny - margin_m <= y <= maxy + margin_m):
            continue
        try:
            poly = transform(_xy, shape(g))
        except Exception:
            continue
        if poly.is_empty or not poly.is_valid or poly.area < min_area:  # skip sheds / kiosks
            continue
        pr = f.get("properties", {})
        out.append((pr.get("id") or pr.get("building_id") or "unknown", pr,
                    poly, poly.distance(route_m)))
    return out


def build_debris_zones(graph: Graph, probe: _Probe, origin: dict, dest: dict, base_resp,
                       cfg: dict[str, Any], incident_id: str) -> list[dict[str, Any]]:
    """SIMULATED debris zones adjacent to real footprints near the route (not chosen by height)."""
    coords = base_resp.routes[0].geometry.coordinates if base_resp.route_count else []
    if len(coords) < 2:
        return []
    route_m = LineString([_xy(x, y) for x, y, *_ in coords])
    ring_w = float(cfg.get("ring_width_m", 15))
    lo, hi = float(cfg.get("min_route_distance_m", 1)), float(cfg.get("max_route_distance_m", 12))
    cands = _building_polygons(graph, route_m, margin_m=hi + 5)
    fits = [c for c in cands if lo <= c[3] <= hi]
    fallback = False
    if not fits:  # nothing alongside the route: nearest footprints anywhere in the graph layer
        fallback = True
        everything = _building_polygons(graph, route_m, 2000) or _building_polygons(graph, route_m, 2000, 0.0)
        fits = sorted(everything, key=lambda c: c[3])[:10]
    length = max(route_m.length, 1.0)
    fits.sort(key=lambda c: (abs(route_m.project(c[2].centroid) / length - 0.5), str(c[0])))
    sep = float(cfg.get("min_separation_m", 80))
    wanted = int(cfg.get("zones", 2))
    chosen: list[dict[str, Any]] = []
    restrictions: list[Restriction] = []
    along: list[float] = []
    graph._ensure_tree()  # noqa: SLF001 (shared STRtree over edge geometry)
    for bid, props, poly, dist in fits:
        if len(chosen) >= wanted:
            break
        pos = route_m.project(poly.centroid)
        if any(abs(pos - a) < sep for a in along):
            continue
        ring = poly.buffer(ring_w).difference(poly).simplify(0.5)
        if ring.is_empty:
            continue
        zid = f"sim-eq-debris-{len(chosen) + 1}"
        geo = _lonlat_geojson(ring)
        trial = [*restrictions, _closure(incident_id, zid, geometry=geo, kind="impassable_polygon")]
        try:
            resp = probe.route(origin, dest, trial)
        except Exception:
            continue
        if not route_edge_ids(resp):  # a zone must not cut the origin off entirely
            continue
        hit = graph._tree.query(ring, predicate="intersects")  # noqa: SLF001
        restrictions = trial
        along.append(pos)
        chosen.append({
            "zone_id": zid, "label": f"{SIMULATED} debris zone",
            "disclaimer": cfg.get("disclaimer", ""),
            "simulated": True, "classification": "Simulated",
            "building_id": bid, "building_source": "OSM building footprint (real geometry)",
            "building_properties": {k: v for k, v in props.items()
                                    if k.lower() in ("building_type", "building_height_class", "levels")},
            "selection": ("nearest footprint to the route (no footprint beside it)" if fallback
                          else f"footprint {dist:.1f} m from the route, chosen by location"),
            "distance_to_route_m": round(float(dist), 1),
            "geometry": geo,
            "intersecting_edge_count": int(len(hit)),
            "intersecting_edge_ids": sorted(graph.edge_ids[int(i)] for i in hit)[:50],
        })
    return chosen


# ------------------------------------------------------------------------------- seeding
@dataclass
class SeedResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    graph: dict[str, Any] = field(default_factory=dict)
    details: dict[str, dict[str, Any]] = field(default_factory=dict)

    def summary_lines(self) -> list[str]:
        lines = [f"graph: {self.graph.get('used')} version={self.graph.get('version')} "
                 f"({self.graph.get('note')})"]
        for sid in [*self.created, *self.skipped]:
            d = self.details.get(sid, {})
            state = "created" if sid in self.created else "already seeded"
            lines.append(f"  [{state}] {sid} incident={d.get('incident_id')} "
                         f"notices={d.get('notices', 0)} {d.get('summary', '')}".rstrip())
        return lines


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_row(repo: Repository, scenario_id: str) -> dict | None:
    return repo.get(DEMO_TABLE, {"pk": DEMO_PK, "sk": scenario_id})


def get_scenario(repo: Repository, scenario_id: str) -> dict[str, Any] | None:
    """The stored (resolved) scenario: definition, steps, resolved ids and graph used."""
    row = _get_row(repo, scenario_id)
    return strip_keys(row) if row else None


def list_scenarios(repo: Repository) -> list[dict[str, Any]]:
    return [strip_keys(r) for r in repo.query(DEMO_TABLE, {"pk": DEMO_PK}, limit=100)]


def seed(repository: Repository, now: datetime | None = None, *, graph: Graph | None = None,
         scenarios: Iterable[str] | None = None, definitions: dict[str, Any] | None = None,
         bus: EventBus | None = None, on_change: Callable[[str], None] | None = None,
         graph_root: Path | str | None = None) -> SeedResult:
    """Create the SIMULATED demo incidents, notices, users and resolved scenario rows.

    Idempotent per scenario. ``graph`` defaults to the real artifact, falling back to the
    SIMULATED fixture graph; the one used is recorded in the result and in each stored scenario.
    """
    now = now or _now()
    defs = definitions or load_definitions()
    if graph is None:
        graph, info = load_demo_graph(graph_root)
    else:
        info = graph_info(graph)
    wanted = set(scenarios) if scenarios is not None else None
    bus = bus or EventBus(repository, InProcessPublisher(), clock=lambda: now)
    inc_svc = IncidentService(repository, bus, clock=lambda: now, on_change=on_change)
    ovl_svc = OverlayService(repository, bus, clock=lambda: now, on_change=on_change)
    accounts = AccountService(repository, clock=lambda: now)
    result = SeedResult(graph=info)

    mod = defs["moderator"]
    accounts.ensure_user(mod["user_id"], Role(mod["role"]), mod["display_name"])

    builders = {
        "flood_loop": _seed_flood, "earthquake_debris": _seed_earthquake,
        "blocked_stairwell": _seed_stairwell, "tornado_shelter": _seed_tornado,
        "fire_downtown": _seed_fire,
    }
    for sc in defs["scenarios"]:
        sid = sc["scenario_id"]
        if wanted is not None and sid not in wanted:
            continue
        inc = inc_svc.get_incident(sc["incident_id"])
        row = _get_row(repository, sid)
        if inc is not None and row is not None:
            result.skipped.append(sid)
            result.details[sid] = {"incident_id": sc["incident_id"], **row.get("seeded_counts", {}),
                                   "summary": ""}
            continue
        if inc is not None or row is not None:  # partial state from an interrupted run
            reset_scenario(repository, sid, definitions=defs, on_change=on_change)
        ctx = _Ctx(repository, now, graph, info, inc_svc, ovl_svc, accounts, sc, defs)
        try:
            resolved, summary = builders[sid](ctx)
        except BaseException:
            reset_scenario(repository, sid, definitions=defs, on_change=on_change)
            raise
        counts = {"notices": len(inc_svc.list_notices(sc["incident_id"])),
                  "overlays": len(ovl_svc.list_overlays(sc["incident_id"]))}
        stored = {
            "scenario_id": sid, "incident_id": sc["incident_id"], "name": sc["name"],
            "incident_type": sc["incident_type"], "simulation_flag": True,
            "classification": "Simulated", "label": SIMULATED, "notice": defs["notice"],
            "optional": bool(sc.get("optional", False)),
            "origin": sc.get("origin"), "destination": sc.get("destination"),
            "route_request": sc.get("route_request"), "steps": sc["steps"],
            "resolved": resolved, "graph": info, "seeded_at": now.isoformat(),
            "seeded_counts": counts, "user_ids": resolved.get("user_ids", []),
        }
        repository.put(DEMO_TABLE, {**json.loads(json.dumps(stored)), "pk": DEMO_PK, "sk": sid})
        result.created.append(sid)
        result.details[sid] = {"incident_id": sc["incident_id"], **counts, "summary": summary}
    return result


@dataclass
class _Ctx:
    repo: Repository
    now: datetime
    graph: Graph
    info: dict[str, Any]
    inc: IncidentService
    ovl: OverlayService
    accounts: AccountService
    sc: dict[str, Any]
    defs: dict[str, Any]

    @property
    def iid(self) -> str:
        return self.sc["incident_id"]

    def incident(self, boundary: dict | None = None) -> Incident:
        return self.inc.create_incident(
            IncidentType(self.sc["incident_type"]), source=f"{SIMULATED} demo scenario: {self.sc['name']}",
            geographic_boundary=boundary, simulation_flag=True, scenario_id=self.sc["scenario_id"],
            incident_id=self.iid)

    def destination_notice(self) -> Any:
        d = self.sc["destination"]
        return self.inc.create_simulated_notice(
            self.iid, notice_type="incident_destination", text=d["notice_text"],
            geometry={"type": "Point", "coordinates": [d["longitude"], d["latitude"]]})

    def probe(self, incident: Incident, notices: Sequence[Any]) -> _Probe:
        return _Probe(self.graph, incident, notices, self.now, self.sc["route_request"])


def _seed_flood(c: _Ctx) -> tuple[dict, str]:
    sc = c.sc
    incident = c.incident()
    notice = c.destination_notice()
    highlight = flood_highlight(c.graph, sc["origin"], sc["flood_highlight"])
    probe = c.probe(incident, [notice])
    base = probe.route(sc["origin"], sc["destination"])
    before = route_edge_ids(base)
    if not before:
        raise DemoDataError(f"flood scenario: no route between origin and destination ({base.explanation})")
    rid = sc["closure"]["restriction_id"]
    picked = pick_closure_edge(c.graph, probe, sc["origin"], sc["destination"], before, [], c.iid, rid)
    if picked is None:
        raise DemoDataError("flood scenario: no best-route edge can be closed while keeping a route")
    edge, after = picked
    resolved = {
        "flood_highlight": highlight,
        "destination_notice_id": notice.notice_id,
        "route_before_edge_ids": before,
        "closure": {**sc["closure"], "kind": "closure", "edge_ids": [edge], "simulated": True,
                    "route_after_edge_ids": after,
                    "text": f"{SIMULATED} verified road closure (demo moderator); not a real closure"},
    }
    return resolved, (f"closure_edge={edge} highlighted_edges={highlight['properties']['edge_count']}")


def _seed_earthquake(c: _Ctx) -> tuple[dict, str]:
    sc = c.sc
    incident = c.incident()
    notice = c.destination_notice()
    probe = c.probe(incident, [notice])
    base = probe.route(sc["origin"], sc["destination"])
    before = route_edge_ids(base)
    if not before:
        raise DemoDataError(f"earthquake scenario: no route ({base.explanation})")
    zones = build_debris_zones(c.graph, probe, sc["origin"], sc["destination"], base, sc["debris"], c.iid)
    zone_restrictions = [_closure(c.iid, z["zone_id"], geometry=z["geometry"], kind="impassable_polygon")
                         for z in zones]
    with_zones = probe.route(sc["origin"], sc["destination"], zone_restrictions)
    current = route_edge_ids(with_zones)
    rid = sc["closure"]["restriction_id"]
    picked = pick_closure_edge(c.graph, probe, sc["origin"], sc["destination"], current or before,
                               zone_restrictions, c.iid, rid)
    if picked is None:
        raise DemoDataError("earthquake scenario: no best-route edge can be closed while keeping a route")
    edge, after = picked
    resolved = {
        "destination_notice_id": notice.notice_id,
        "route_before_edge_ids": before,
        "route_with_debris_edge_ids": current,
        "debris_zones": zones,
        "closure": {**sc["closure"], "kind": "closure", "edge_ids": [edge], "simulated": True,
                    "route_after_edge_ids": after,
                    "text": f"{SIMULATED} confirmed road closure; not a real closure"},
    }
    return resolved, f"debris_zones={len(zones)} closure_edge={edge}"


def _seed_stairwell(c: _Ctx) -> tuple[dict, str]:
    sc = c.sc
    c.incident()
    spec = indoor_mod.load_spec()
    nodes, edges = indoor_mod.build_indoor_parts(spec, list(c.graph.model.nodes))  # type: ignore[union-attr]
    by_id = {n.node_id: n for n in nodes}
    starts = {p["id"]: p for p in spec["start_positions"]}
    suffix = f"-stair-{sc['stairwell']}"
    stair_nodes = {n for n in by_id if n.endswith(suffix)}
    stair_edges = sorted(e.edge_id for e in edges if e.from_node in stair_nodes or e.to_node in stair_nodes)
    if not stair_edges:
        raise DemoDataError(f"stairwell {sc['stairwell']} has no edges in the indoor graph")
    users, user_ids = [], []
    for u in sc["users"]:
        sp = starts[u["start_position"]]
        node = by_id[indoor_mod.node_id(sp["level"], sp["node"])]
        c.accounts.ensure_user(u["user_id"], Role.USER, u["display_name"])
        user_ids.append(u["user_id"])
        users.append({"key": u["key"], "user_id": u["user_id"], "display_name": u["display_name"],
                      "simulated": True, "level": sp["level"], "indoor_node_id": node.node_id,
                      "position_name": sp["name"], "latitude": node.lat, "longitude": node.lon})
    resolved = {
        "building_id": sc["building_id"], "building_name": spec["name"], "building_notice": spec["notice"],
        "users": users, "user_ids": user_ids, "moderator_user_id": c.defs["moderator"]["user_id"],
        "stairwell": sc["stairwell"],
        "closure": {**sc["closure"], "edge_ids": stair_edges, "simulated": True,
                    "text": f"{SIMULATED} stairwell {sc['stairwell']} closure (demo moderator); not a real building"},
    }
    return resolved, f"users={len(users)} stairwell_edges={len(stair_edges)}"


def _seed_tornado(c: _Ctx) -> tuple[dict, str]:
    sc = c.sc
    b = sc["boundary"]
    c.incident(boundary=circle_polygon(b["center"]["latitude"], b["center"]["longitude"], b["radius_m"]))
    return ({"expected_outcome": "SHELTER_IN_PLACE",
             "expected_text": "no verified shelter available (SIMULATED) unless the optional simulated shelter notice is added",
             "boundary_label": b["label"], "optional_shelter_notice": sc["optional_shelter_notice"]},
            "no verified shelter available (SIMULATED)")


def _seed_fire(c: _Ctx) -> tuple[dict, str]:
    sc = c.sc
    p = sc["perimeter"]
    geo = circle_polygon(p["center"]["latitude"], p["center"]["longitude"], p["radius_m"])
    c.incident()
    notice = c.inc.create_simulated_notice(c.iid, notice_type="temporary_perimeter",
                                           text=p["notice_text"], geometry=geo)
    c.destination_notice()
    return ({"perimeter_notice_id": notice.notice_id,
             "perimeter": {"type": "Feature", "geometry": geo,
                           "properties": {"simulated": True, "label": p["label"]}}},
            "SIMULATED fire perimeter")


# ----------------------------------------------------------------------------- applying steps
CLIENT_STEPS = {"activate_incident", "show_layer", "calculate_routes", "load_indoor_graph",
                "observe", "submit_report", "confirm_report", "post_message"}


def apply_step(repository: Repository, scenario_id: str, step_id: str, *,
               bus: EventBus | None = None, now: datetime | None = None,
               on_change: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Apply the server-side effect of a step (closures, debris zones, notices).

    Steps handled by clients or other services (routing, reports, messages) return
    ``{"handled_by": ...}`` untouched. Re-applying a step is harmless.
    """
    row = get_scenario(repository, scenario_id)
    if row is None:
        raise DemoDataError(f"scenario {scenario_id!r} is not seeded")
    step = next((s for s in row["steps"] if s["id"] == step_id), None)
    if step is None:
        raise DemoDataError(f"scenario {scenario_id!r} has no step {step_id!r}")
    kind, iid, resolved = step["kind"], row["incident_id"], row["resolved"]
    clock = (lambda: now) if now else _now
    bus = bus or EventBus(repository, InProcessPublisher(), clock=clock)
    ovl = OverlayService(repository, bus, clock=clock, on_change=on_change)
    versions: list[int] = []

    def add(rid: str, **kw: Any) -> None:
        try:
            versions.append(ovl.add_closure(iid, restriction_id=rid, simulated=True,
                                            source_record_id="SIMULATED-DEMO-SOURCE", **kw))
        except DuplicateRestriction:
            existing = {o.restriction_id: o for o in ovl.list_overlays(iid, active_only=False)}
            if not existing[rid].active or existing[rid].status != "active":
                versions.append(ovl.activate_closure(iid, rid))

    if kind in ("add_closure", "confirm_stairwell_closure"):
        cl = resolved[step.get("ref", "closure")]
        add(cl["restriction_id"], edge_ids=cl["edge_ids"], kind=cl.get("kind", "closure"))
    elif kind == "add_debris":
        for z in resolved["debris_zones"]:
            add(z["zone_id"], geometry=z["geometry"], kind="impassable_polygon")
    elif kind == "add_notice":
        spec = resolved[step["ref"]]
        IncidentService(repository, bus, clock=clock, on_change=on_change).create_simulated_notice(
            iid, notice_type=spec["notice_type"], text=spec["text"])
    elif kind in CLIENT_STEPS:
        return {"step": step_id, "kind": kind, "handled_by": "client_or_service", "applied": False}
    else:
        raise DemoDataError(f"unsupported step kind {kind!r}")
    return {"step": step_id, "kind": kind, "applied": True, "overlay_versions": versions,
            "simulated": True}


# --------------------------------------------------------------------------------- reset
def reset_scenario(repository: Repository, scenario_id: str, *,
                   definitions: dict[str, Any] | None = None,
                   on_change: Callable[[str], None] | None = None) -> dict[str, int]:
    """Delete every record of one scenario (incident, notices, overlays, reports, messages,
    events, its simulated users and the resolved row). Other scenarios are untouched.

    Idempotent. The shared demo moderator is kept. Re-run :func:`seed` to restore the seeded state.
    """
    defs = definitions or load_definitions()
    sc = next((s for s in defs["scenarios"] if s["scenario_id"] == scenario_id), None)
    if sc is None:
        raise DemoDataError(f"unknown scenario {scenario_id!r}")
    iid = sc["incident_id"]
    removed: dict[str, int] = {}
    # Votes are keyed by report id; demo report ids are deterministic, so stale votes would make
    # every corroboration after a reset fail as a duplicate vote.
    removed["report_votes"] = sum(
        repository.delete_where("report_votes", {"pk": row["report_id"]})
        for row in repository.query("reports", {"pk": iid}, limit=1000) if row.get("report_id"))
    for table in INCIDENT_TABLES:
        removed[table] = repository.delete_where(table, {"pk": iid})
    for table in INCIDENT_ATTR_TABLES:
        removed[table] = repository.delete_where(table, {"incident_id": iid})
    removed["incidents"] = repository.delete_where(INCIDENTS_TABLE, {"pk": INCIDENTS_PK, "sk": iid})
    removed["users"] = sum(repository.delete_where(USERS_TABLE, {"pk": u["user_id"]})
                           for u in sc.get("users", []))
    removed[DEMO_TABLE] = repository.delete_where(DEMO_TABLE, {"pk": DEMO_PK, "sk": scenario_id})
    if on_change:
        on_change(iid)
    return removed


def reset_all(repository: Repository, *, definitions: dict[str, Any] | None = None,
              on_change: Callable[[str], None] | None = None) -> dict[str, dict[str, int]]:
    defs = definitions or load_definitions()
    out = {s["scenario_id"]: reset_scenario(repository, s["scenario_id"], definitions=defs,
                                            on_change=on_change) for s in defs["scenarios"]}
    repository.delete_where(USERS_TABLE, {"pk": defs["moderator"]["user_id"]})
    return out


def is_seeded_state(repository: Repository, scenario_id: str) -> bool:
    """True when the scenario holds exactly its seeded notices and overlays (reset is a no-op)."""
    row = get_scenario(repository, scenario_id)
    if row is None:
        return False
    iid, counts = row["incident_id"], row["seeded_counts"]
    for table, key in (("notices", "notices"), ("overlays", "overlays")):
        if len(repository.query(table, {"pk": iid}, limit=10_000)) != counts[key]:
            return False
    return not any(repository.query(t, {"pk": iid}, limit=1) for t in ("reports", "messages", "checkins"))


__all__ = [
    "DEMO_TABLE", "DESTINATION_LABEL", "DemoDataError", "SeedResult", "apply_step", "get_scenario",
    "is_seeded_state", "list_scenarios", "load_definitions", "load_demo_graph", "reset_all",
    "reset_scenario", "scenario_ids", "seed",
]
