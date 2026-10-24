"""Generate the SIMULATED fixture graph artifact (tasks 3.14; Req 7.4, 7.5, 45.6, 45.7).

The fixture is a small, synthetic, Chicago-like street grid around the Loop. It is NOT real
street data, NOT surveyed, and must never be used to give real routing guidance. Every node id,
edge id, source id and classification says so. It exists so development, CI and demos still work
when real downloads (Chicago Data Portal, OSM, 3DEP) are unavailable.

Usage (from ``safepath/``)::

    py scripts/make_fixture_graph.py            # writes tests/fixtures/graph + fallbacks.json
    py scripts/make_fixture_graph.py --force    # replace the fixture artifact

Output is deterministic (fixed timestamps), so the version id is stable across runs.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SAFEPATH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAFEPATH / "backend"))

from app.geo import (  # noqa: E402
    EAST, HIGH_RISE, LOW_RISE, MID_RISE, NORTH, SOUTH, UNKNOWN, WEST, haversine,
)
from app.models.graph import Edge, Graph, Node  # noqa: E402
from app.services.graph_artifact import write_artifact  # noqa: E402

FIXTURE_ROOT = SAFEPATH / "tests" / "fixtures" / "graph"
FALLBACKS_PATH = SAFEPATH / "data" / "processed" / "fallbacks.json"

FIXTURE_SOURCE = "SIMULATED-FIXTURE-GRAPH"
FALLBACK_KEY = "synthetic_fixture_graph"
BUILT_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
SIM_NOTICE = (
    "SIMULATED synthetic fixture. Not real street data; do not use for real navigation."
)

COLS = [-87.6505, -87.6489, -87.6400, -87.6310, -87.6278, -87.6258, -87.6226]
# Rows south -> north. Row 6 is the Riverwalk (south bank); row 7 the north bank.
ROWS = [41.8708, 41.8760, 41.8785, 41.8819, 41.8826, 41.8858, 41.8880, 41.8905]
RIVER_ROW = 6  # the river lies between ROWS[6] and ROWS[7]
BRIDGE_COLS = {2, 3, 6}

NAMED = {  # (row, col) -> node id, one per required neighborhood
    (0, 0): "fx-uic",
    (1, 2): "fx-uic-downtown-corridor",
    (2, 5): "fx-depaul-loop",
    (3, 4): "fx-loop",
    (4, 1): "fx-west-loop",
    (4, 6): "fx-millennium-park",
    (5, 3): "fx-clark-lake",
    (6, 3): "fx-riverwalk",
}
NEIGHBORHOOD_LABELS = {
    "fx-uic": "UIC", "fx-uic-downtown-corridor": "UIC-downtown corridor",
    "fx-depaul-loop": "DePaul Loop", "fx-loop": "Loop", "fx-west-loop": "West Loop",
    "fx-millennium-park": "Millennium Park", "fx-clark-lake": "Clark/Lake",
    "fx-riverwalk": "Riverwalk",
}
START_NODE = "fx-riverwalk"
DEST_NODE = "fx-sim-destination"
DEST_COORD = (41.8920, -87.6278)  # (lat, lon), north of the river

# Special segments (by grid endpoints, west->east or south->north).
TUNNEL_SEG = ((2, 2), (2, 3))        # replaces the surface street with a tunnel edge
ONE_WAY_ROW = 5                       # eastbound one-way for vehicles, cols 2..6
LOWER_LEVEL_SEG = ((5, 2), (5, 3))   # extra lower-level (Lower-Wacker-like) vehicle edge


def _nid(r: int, c: int) -> str:
    return NAMED.get((r, c), f"fx-sim-r{r}c{c}")


def _wkt(coords: list[tuple[float, float]]) -> str:
    return "LINESTRING(" + ", ".join(f"{lon} {lat}" for lon, lat in coords) + ")"


def _build_nodes() -> list[Node]:
    nodes: list[Node] = []
    for r, lat in enumerate(ROWS):
        for c, lon in enumerate(COLS):
            k = r * len(COLS) + c
            # Deterministic mix of Known and Unknown elevations (synthetic values, NAVD 88-like).
            if k % 6 == 5:
                elev, conf = UNKNOWN, UNKNOWN
            else:
                elev, conf = round(176.0 + 0.35 * r + 0.1 * c, 2), "Medium"
            nodes.append(Node(node_id=_nid(r, c), lat=lat, lon=lon, kind="intersection",
                              elevation_m=elev, elevation_conf=conf))
    nodes.append(Node(node_id=DEST_NODE, lat=DEST_COORD[0], lon=DEST_COORD[1],
                      kind="pedestrian_junction", elevation_m=177.5, elevation_conf="Low"))
    return nodes


class _EdgeBuilder:
    def __init__(self, nodes: list[Node]) -> None:
        self.by_id = {n.node_id: n for n in nodes}
        self.edges: list[Edge] = []

    def add(self, a: str, b: str, *, modes: list[str], infra: str = "surface",
            bridge=False, tunnel=False, via: tuple[float, float] | None = None,
            road_type: str | None = None, speed: float = 1.4) -> None:
        na, nb = self.by_id[a], self.by_id[b]
        pts = [(na.lon, na.lat)] + ([(via[1], via[0])] if via else []) + [(nb.lon, nb.lat)]
        dist = sum(haversine(p[1], p[0], q[1], q[0]) for p, q in zip(pts, pts[1:]))
        i = len(self.edges)
        low_conf = infra != "surface"
        if na.elevation_m == UNKNOWN or nb.elevation_m == UNKNOWN:
            e0 = e1 = UNKNOWN
            econf = UNKNOWN
        else:
            e0, e1 = na.elevation_m, nb.elevation_m
            econf = "Low" if low_conf else "Medium"
        if tunnel:
            e0 = e1 = UNKNOWN  # tunnels: elevation not trustworthy
            econf = UNKNOWN
        # Attribute knowledge varies deterministically so Unknown handling is exercised.
        known = i % 4 != 3
        self.edges.append(Edge(
            edge_id=f"fx-sim-e{i:03d}", from_node=a, to_node=b, geometry=_wkt(pts),
            distance_meters=round(dist, 2),
            estimated_travel_time_seconds=round(dist / speed, 2),
            travel_mode=modes, source=FIXTURE_SOURCE,
            source_ids={"fixture": f"{FIXTURE_SOURCE}:{i:03d}"},
            road_type=(road_type or ("arterial" if i % 3 == 0 else "local")) if known else UNKNOWN,
            elevation_start=e0, elevation_end=e1, elevation_confidence=econf,
            bridge_flag=True if bridge else (False if known else UNKNOWN),
            tunnel_flag=True if tunnel else (False if known else UNKNOWN),
            infrastructure_level=infra if (infra != "surface" or known) else UNKNOWN,
            pedestrian_access=("driving" not in modes or len(modes) > 1) if known else UNKNOWN,
            mobility_accessibility=("accessible" if "wheelchair" in modes else "inaccessible")
            if known else UNKNOWN,
            current_closure_status=UNKNOWN, hazard_flags=[],
            data_timestamp=BUILT_AT, classification="Simulated",
        ))


def _build_edges(nodes: list[Node]) -> list[Edge]:
    b = _EdgeBuilder(nodes)
    all_modes = ["walking", "wheelchair", "driving"]
    ped_modes = ["walking", "wheelchair"]

    def pair(a: str, c: str, **kw) -> None:
        b.add(a, c, **kw)
        b.add(c, a, **kw)

    for r in range(len(ROWS)):
        for c in range(len(COLS) - 1):  # east-west
            a, d = _nid(r, c), _nid(r, c + 1)
            if ((r, c), (r, c + 1)) == TUNNEL_SEG:
                via = ((ROWS[r] + 0.0004), (COLS[c] + COLS[c + 1]) / 2)
                pair(a, d, modes=["driving"], infra="tunnel", tunnel=True, via=via,
                     speed=8.0)
            elif r == RIVER_ROW:  # Riverwalk: pedestrian-only, lower than street level
                pair(a, d, modes=ped_modes, infra="lower-level", road_type="riverwalk")
            elif r == ONE_WAY_ROW and c >= 2:
                b.add(a, d, modes=all_modes)  # eastbound for everyone
                b.add(d, a, modes=ped_modes)  # westbound pedestrians only (one-way for cars)
            else:
                pair(a, d, modes=all_modes)
    for r in range(len(ROWS) - 1):  # north-south
        for c in range(len(COLS)):
            a, d = _nid(r, c), _nid(r + 1, c)
            if r == RIVER_ROW:  # river crossing: only the flagged bridges exist
                if c in BRIDGE_COLS:
                    pair(a, d, modes=all_modes, infra="bridge", bridge=True)
                continue
            pair(a, d, modes=all_modes)
    lo_a, lo_b = (_nid(*LOWER_LEVEL_SEG[0]), _nid(*LOWER_LEVEL_SEG[1]))
    pair(lo_a, lo_b, modes=["driving"], infra="lower-level", road_type="lower-level", speed=8.0)
    # Simulated destination area hangs off the north bank.
    pair(_nid(RIVER_ROW + 1, 4), DEST_NODE, modes=all_modes)
    return b.edges


def _rect(lon: float, lat: float, dlon: float, dlat: float) -> list[list[list[float]]]:
    return [[[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat], [lon, lat + dlat],
             [lon, lat]]]


def _build_layers() -> dict[str, dict]:
    meta = {"simulated": True, "source": FIXTURE_SOURCE, "classification": "Simulated",
            "notice": SIM_NOTICE}
    sizes = [  # (building_id, lon, lat, height_m or None, class)
        ("fx-sim-bldg-1", -87.6300, 41.8835, 18.0, LOW_RISE),
        ("fx-sim-bldg-2", -87.6275, 41.8840, 55.0, MID_RISE),
        ("fx-sim-bldg-3", -87.6395, 41.8812, 190.0, HIGH_RISE),
        ("fx-sim-bldg-4", -87.6255, 41.8800, None, UNKNOWN),
        ("fx-sim-bldg-5", -87.6480, 41.8770, None, UNKNOWN),
    ]
    feats = []
    for bid, lon, lat, h, cls in sizes:
        props = {"building_id": bid, "name": f"SIMULATED building {bid[-1]}",
                 "Building_Height_Class": cls, **meta}
        if h is not None:  # only source-present attributes are recorded
            props["height_m"] = h
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "Polygon", "coordinates": _rect(lon, lat, 0.0009, 0.0006)}})
    buildings = {"type": "FeatureCollection", "name": "SIMULATED buildings (fixture)",
                 "properties": meta, "features": feats}

    by_id = {n.node_id: n for n in _build_nodes()}
    place_feats = []
    for nid, label in NEIGHBORHOOD_LABELS.items():
        n = by_id[nid]
        place_feats.append({"type": "Feature", "geometry": {"type": "Point",
                            "coordinates": [n.lon, n.lat]},
                            "properties": {"role": "neighborhood", "node_id": nid,
                                           "name": f"SIMULATED {label} node", **meta}})
    s = by_id[START_NODE]
    place_feats.append({"type": "Feature", "geometry": {"type": "Point",
                        "coordinates": [s.lon, s.lat]},
                        "properties": {"role": "start", "node_id": START_NODE,
                                       "name": "SIMULATED Riverwalk start point", **meta}})
    dlat, dlon = DEST_COORD
    place_feats.append({"type": "Feature", "geometry": {"type": "Polygon",
                        "coordinates": _rect(dlon - 0.0006, dlat - 0.0004, 0.0012, 0.0008)},
                        "properties": {"role": "destination", "node_id": DEST_NODE,
                                       "name": "SIMULATED Destination Area (not a real assembly area)",
                                       **meta}})
    places = {"type": "FeatureCollection", "name": "SIMULATED fixture places",
              "properties": meta, "features": place_feats}
    boundary = {"type": "FeatureCollection", "name": "SIMULATED fixture extent",
                "properties": meta, "features": [{"type": "Feature", "properties": dict(meta),
                "geometry": {"type": "Polygon", "coordinates": _rect(WEST, SOUTH, EAST - WEST, NORTH - SOUTH)}}]}
    return {"buildings": buildings, "fixture_places": places, "extraction_boundary": boundary}


def build_fixture() -> tuple[Graph, dict[str, dict]]:
    nodes = _build_nodes()
    return Graph.build(nodes, _build_edges(nodes)), _build_layers()


def update_fallbacks(path: Path, version: str, root: Path) -> None:
    """Read-merge-write ``fallbacks.json`` touching only this script's key (atomic replace)."""
    data: dict = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object; refusing to overwrite it")
    try:
        shown = root.relative_to(SAFEPATH).as_posix()
    except ValueError:
        shown = str(root)
    data[FALLBACK_KEY] = {
        "source_id": FIXTURE_SOURCE,
        "classification": "Simulated",
        "fallback": "Synthetic Chicago-like street grid used when real street downloads are unavailable.",
        "limitation": (
            "SIMULATED. Street geometry, bridges, tunnels, one-way flags, elevations and buildings "
            "are invented for development, CI and demos. They do not represent real Chicago "
            "streets and must not be used for real navigation or evacuation guidance."
        ),
        "artifact_root": shown,
        "graph_version": version,
        "reproduce": "python scripts/make_fixture_graph.py --force  (run from safepath/ with the backend venv active)",
        "requirements": ["7.4", "7.5", "45.6", "45.7"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fallbacks-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def make_fixture(root: Path = FIXTURE_ROOT, fallbacks_path: Path | None = FALLBACKS_PATH,
                 force: bool = False) -> str:
    """Write the fixture artifact under ``root`` and record the fallback. Returns version id."""
    graph, layers = build_fixture()
    graphs_dir = Path(root) / "graphs"
    if force and graphs_dir.exists():
        shutil.rmtree(graphs_dir)
    # Deterministic content => deterministic version; reuse it when already present.
    existing = sorted(p.name for p in graphs_dir.glob("v*")) if graphs_dir.exists() else []
    if existing and not force:
        version = existing[-1]
    else:
        version = write_artifact(root, graph, layers, built_at=BUILT_AT,
                                 source_record_ids=[FIXTURE_SOURCE])
    if fallbacks_path is not None:
        update_fallbacks(Path(fallbacks_path), version, Path(root))
    return version


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=FIXTURE_ROOT)
    ap.add_argument("--fallbacks", type=Path, default=FALLBACKS_PATH)
    ap.add_argument("--force", action="store_true", help="replace an existing fixture artifact")
    args = ap.parse_args(argv)
    print(f"{SIM_NOTICE}\nfixture graph version: "
          f"{make_fixture(args.out, args.fallbacks, args.force)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
