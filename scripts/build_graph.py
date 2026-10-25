"""Step: build the routable Chicago graph G=(V,E) from OSM ways + DEM + City portal cross-check.

Output: data/processed/graph.json.gz  and  data/processed/graph_report.json

Design decisions (see docs/routing-design.md):
  * Primary topology/attributes: OpenStreetMap (has bridge/tunnel/layer/oneway/foot tags).
  * City of Chicago Data Portal centerlines are used as an authoritative cross-check:
      - attach official street name
      - detect one-way direction conflicts (conflicting driving segments are NOT routable by car)
  * Segments are undirected geometries with per-direction access flags; the routing engine expands
    them to directed edges (edge_id = "<segment_id>+" forward / "<segment_id>-" reverse).
  * Missing information is stored as null/"unknown" - never defaulted to "safe".
  * Ways tagged indoor=yes / highway=corridor / private access are excluded from the outdoor graph
    (opening hours & access are unknown). Counted in the report.
"""
from __future__ import annotations

import gzip
import json
import math
import sys
from collections import Counter, defaultdict

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, Point, shape
from shapely.strtree import STRtree

from common import BBOX, PROCESSED, RAW, ensure_dirs, now_iso, read_json, record_manifest, write_json

TO_UTM = Transformer.from_crs(4326, 26916, always_xy=True).transform

WALK_OK = {
    "footway", "steps", "pedestrian", "path", "living_street", "service", "residential",
    "unclassified", "tertiary", "tertiary_link", "secondary", "secondary_link",
}
DRIVE_OK = {
    "motorway", "motorway_link", "trunk", "trunk_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "residential", "unclassified", "service", "living_street",
}
SKIP_SERVICE = {"parking_aisle", "driveway", "drive-through"}
DEFAULT_KPH = {
    "motorway": 80, "motorway_link": 50, "trunk": 55, "trunk_link": 40, "secondary": 40,
    "secondary_link": 30, "tertiary": 35, "tertiary_link": 30, "residential": 25,
    "unclassified": 30, "service": 15, "living_street": 15,
}

warnings: list[str] = []
counts: Counter = Counter()


def load_dem():
    p = RAW / "dem_3dep.npy"
    if not p.exists():
        warnings.append("DEM missing: all elevations will be null/unknown")
        return None
    return np.load(p)


def sample_dem(dem, lon, lat):
    if dem is None:
        return None
    h, w = dem.shape
    x = (lon - BBOX["west"]) / (BBOX["east"] - BBOX["west"]) * (w - 1)
    y = (BBOX["north"] - lat) / (BBOX["north"] - BBOX["south"]) * (h - 1)
    if x < 0 or y < 0 or x > w - 1 or y > h - 1:
        return None
    x0, y0 = int(x), int(y)
    x1, y1 = min(x0 + 1, w - 1), min(y0 + 1, h - 1)
    fx, fy = x - x0, y - y0
    v = (
        dem[y0, x0] * (1 - fx) * (1 - fy)
        + dem[y0, x1] * fx * (1 - fy)
        + dem[y1, x0] * (1 - fx) * fy
        + dem[y1, x1] * fx * fy
    )
    return None if np.isnan(v) else round(float(v), 2)


def parse_layer(t):
    try:
        return int(float(t.get("layer")))
    except Exception:
        return None


def oneway_of(t):
    hw = t.get("highway")
    v = (t.get("oneway") or "").lower()
    if v in ("yes", "true", "1"):
        return "fwd"
    if v == "-1":
        return "rev"
    if v == "no":
        return "both"
    if v in ("reversible", "alternating"):
        return "unknown"
    if hw in ("motorway", "motorway_link") or t.get("junction") == "roundabout":
        return "fwd"
    return "both"


def structure_of(t, layer):
    br, tn = t.get("bridge") not in (None, "no"), t.get("tunnel") not in (None, "no")
    if tn or (layer is not None and layer < 0):
        lvl = "tunnel_or_underpass" if tn else "below_grade_layer"
    elif br or (layer is not None and layer > 0):
        lvl = "elevated_bridge" if br else "elevated_layer"
    else:
        lvl = "ground"
    return br, tn, lvl


def access_blocked(t, mode):
    acc = (t.get("access") or "").lower()
    specific = (t.get("foot") if mode == "walk" else t.get("motor_vehicle")) or ""
    specific = specific.lower()
    if specific in ("yes", "designated", "permissive"):
        return False
    if specific in ("no", "private"):
        return True
    return acc in ("no", "private")


def norm_core(s):
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def main() -> int:
    ensure_dirs()
    src = read_json(RAW / "osm_roads.json")
    if not src:
        print("osm_roads.json missing - run download_chicago_data.py first")
        return 1
    dem = load_dem()
    ways = [e for e in src["elements"] if e["type"] == "way" and e.get("geometry") and len(e.get("nodes", [])) >= 2]
    counts["osm_ways_input"] = len(ways)

    # --- filter ways -----------------------------------------------------------------
    kept = []
    for w in ways:
        t = w["tags"]
        hw = t.get("highway")
        if hw == "corridor" or t.get("indoor") in ("yes", "room", "area", "corridor"):
            counts["excluded_indoor_or_corridor"] += 1
            continue
        if hw == "service" and t.get("service") in SKIP_SERVICE:
            counts["excluded_service_driveway_parking"] += 1
            continue
        walk = hw in WALK_OK and not access_blocked(t, "walk") and t.get("foot") != "no"
        if hw == "cycleway":
            walk = t.get("foot") in ("yes", "designated", "permissive")
        drive = hw in DRIVE_OK and not access_blocked(t, "drive")
        if not walk and not drive:
            counts["excluded_no_public_access_or_mode"] += 1
            continue
        w["_walk"], w["_drive"] = walk, drive
        kept.append(w)
    counts["osm_ways_kept"] = len(kept)

    # --- node usage counts -> split points ----------------------------------------------
    use = Counter()
    for w in kept:
        for n in w["nodes"]:
            use[n] += 1

    node_xy: dict[int, tuple[float, float]] = {}
    segments = []
    seg_id = 0
    for w in kept:
        nids, geom = w["nodes"], w["geometry"]
        if len(nids) != len(geom):
            counts["way_node_geom_mismatch"] += 1
            continue
        for n, p in zip(nids, geom):
            node_xy[n] = (p["lon"], p["lat"])
        t = w["tags"]
        layer = parse_layer(t)
        br, tn, lvl = structure_of(t, layer)
        split_idx = [0] + [i for i in range(1, len(nids) - 1) if use[nids[i]] >= 2] + [len(nids) - 1]
        for a, b in zip(split_idx, split_idx[1:]):
            if nids[a] == nids[b] and b - a < 2:
                continue
            pts = [(geom[i]["lon"], geom[i]["lat"]) for i in range(a, b + 1)]
            ow = oneway_of(t)
            if t.get("highway") == "steps":
                wc = "inaccessible"
            elif t.get("wheelchair") in ("yes", "designated"):
                wc = "accessible"
            elif t.get("wheelchair") == "no":
                wc = "inaccessible"
            else:
                wc = "unknown"
            try:
                kph = float(str(t.get("maxspeed", "")).split()[0]) * (1.609 if "mph" in str(t.get("maxspeed", "")) else 1)
            except Exception:
                kph = None
            segments.append(
                {
                    "id": f"s{seg_id}",
                    "way": w["id"],
                    "a": nids[a],
                    "b": nids[b],
                    "pts": pts,
                    "hw": t.get("highway"),
                    "name": t.get("name"),
                    "br": br,
                    "tn": tn,
                    "layer": layer,
                    "lvl": lvl,
                    "ow": ow,
                    "walk": bool(w["_walk"]),
                    "drive": bool(w["_drive"]) and ow != "unknown",
                    "wc": wc,
                    "surface": t.get("surface"),
                    "kph": kph,
                    "tiered_osm": bool(t.get("tunnel") or t.get("bridge")),
                }
            )
            seg_id += 1
    counts["segments"] = len(segments)

    # --- lengths (projected), elevation ---------------------------------------------------
    for s in segments:
        xs, ys = zip(*[TO_UTM(x, y) for x, y in s["pts"]])
        s["len"] = round(float(sum(math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) for i in range(len(xs) - 1))), 2)
        s["_utm"] = list(zip(xs, ys))
    segments = [s for s in segments if s["len"] > 0.5 or s["a"] != s["b"]]

    # --- cross-check with City of Chicago portal centerlines -----------------------------------
    portal = read_json(PROCESSED / "portal_streets.geojson")
    matched = conflicts = named = 0
    if portal:
        pgeoms, pprops = [], []
        for f in portal["features"]:
            g = shape(f["geometry"])
            comps = list(g.geoms) if g.geom_type == "MultiLineString" else [g] if g.geom_type == "LineString" else []
            for c in comps:
                cu = LineString([TO_UTM(x, y) for x, y, *_ in c.coords])
                pgeoms.append(cu)
                pprops.append(f["properties"])
        tree = STRtree(pgeoms)
        for s in segments:
            if not s["drive"] or s["hw"] in ("motorway", "motorway_link", "service"):
                continue
            line = LineString(s["_utm"])
            if line.length < 3:
                continue
            mid = line.interpolate(0.5, normalized=True)
            idx = tree.query_nearest(mid, max_distance=18.0)
            if len(idx) == 0:
                continue
            pi = int(idx[0])
            pp = pprops[pi]
            core = norm_core(pp.get("name"))
            sname = norm_core(s["name"])
            # require name agreement (portal core street word must be inside OSM name) to avoid mis-matches
            street_core = norm_core((pp.get("name") or "").split(" ")[1] if pp.get("name") and len(pp["name"].split(" ")) > 1 else pp.get("name"))
            if not sname or not street_core or street_core not in sname:
                continue
            matched += 1
            s["official_name"] = pp.get("name")
            s["portal_id"] = pp.get("trans_id")
            s["portal_status"] = pp.get("status")
            if pp.get("tiered") == "Y":
                continue
            pl = pgeoms[pi]
            d = pl.project(mid)
            p0, p1 = pl.interpolate(max(d - 3, 0)), pl.interpolate(min(d + 3, pl.length))
            pv = (p1.x - p0.x, p1.y - p0.y)
            sv = (s["_utm"][-1][0] - s["_utm"][0][0], s["_utm"][-1][1] - s["_utm"][0][1])
            dot = pv[0] * sv[0] + pv[1] * sv[1]
            dt = pp.get("dir_travel")
            expected = {"B": "both", "F": "fwd" if dot >= 0 else "rev", "T": "rev" if dot >= 0 else "fwd"}.get(dt)
            if expected and expected != s["ow"] and s["ow"] != "unknown":
                conflicts += 1
                s["conflict"] = f"oneway osm={s['ow']} vs city_portal={expected}"
                s["drive"] = False  # conservative: do not route cars on conflicting direction data
    counts["segments_name_matched_to_city_portal"] = matched
    counts["oneway_conflicts_city_vs_osm_driving_disabled"] = conflicts

    # --- nodes + elevations ---------------------------------------------------------------------
    used = {}
    for s in segments:
        used[s["a"]] = used[s["b"]] = True
    node_ids = sorted(used)
    nidx = {n: i for i, n in enumerate(node_ids)}
    nodes = []
    for n in node_ids:
        lon, lat = node_xy[n]
        nodes.append([n, round(lon, 6), round(lat, 6), sample_dem(dem, lon, lat)])
    counts["nodes"] = len(nodes)
    counts["nodes_without_elevation"] = sum(1 for n in nodes if n[3] is None)

    # --- finalize segments + stats -----------------------------------------------------------------
    out_segments = []
    for s in segments:
        ea, eb = nodes[nidx[s["a"]]][3], nodes[nidx[s["b"]]][3]
        structural = s["br"] or s["tn"] or (s["layer"] not in (None, 0))
        if ea is None or eb is None:
            conf = "unknown"
        elif structural:
            conf = "dem_bare_earth_unreliable_for_structure"
        else:
            conf = "dem_bare_earth"
        inner = [[round(x, 6), round(y, 6)] for x, y in s["pts"][1:-1]]
        rec = {
            "id": s["id"], "way": s["way"], "a": nidx[s["a"]], "b": nidx[s["b"]], "g": inner,
            "len": s["len"], "hw": s["hw"], "name": s["name"], "br": s["br"], "tn": s["tn"],
            "layer": s["layer"], "lvl": s["lvl"], "ow": s["ow"], "walk": s["walk"], "drive": s["drive"],
            "wc": s["wc"], "kph": s["kph"], "surface": s["surface"],
            "ea": ea, "eb": eb, "econf": conf,
        }
        for k in ("official_name", "portal_id", "portal_status", "conflict"):
            if k in s:
                rec[k] = s[k]
        out_segments.append(rec)

    # connected components (walking, undirected)
    parent = list(range(len(nodes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s in out_segments:
        if s["walk"]:
            ra, rb = find(s["a"]), find(s["b"])
            if ra != rb:
                parent[ra] = rb
    comp = Counter(find(s["a"]) for s in out_segments if s["walk"])
    big = comp.most_common(1)[0] if comp else (None, 0)
    counts["walk_segments"] = sum(1 for s in out_segments if s["walk"])
    counts["drive_segments"] = sum(1 for s in out_segments if s["drive"])
    counts["walk_components"] = len(comp)
    counts["walk_largest_component_segments"] = big[1]
    counts["bridge_segments"] = sum(1 for s in out_segments if s["br"])
    counts["tunnel_segments"] = sum(1 for s in out_segments if s["tn"])
    counts["steps_segments"] = sum(1 for s in out_segments if s["hw"] == "steps")
    counts["oneway_segments"] = sum(1 for s in out_segments if s["ow"] in ("fwd", "rev"))
    counts["segments_elevation_unreliable_structure"] = sum(1 for s in out_segments if s["econf"].endswith("structure"))
    counts["segments_elevation_unknown"] = sum(1 for s in out_segments if s["econf"] == "unknown")
    walk_main = find(next(s["a"] for s in out_segments if s["walk"])) if big[0] is None else big[0]

    manifest = read_json(RAW / "manifest.json", {"datasets": {}})["datasets"]
    meta = {
        "graph_version": "chicago-" + now_iso()[:10],
        "generated_at": now_iso(),
        "bbox": BBOX,
        "crs": "EPSG:4326 (lon,lat); lengths computed in EPSG:26916",
        "walk_main_component_root": walk_main,
        "counts": dict(counts),
        "sources": {k: {kk: vv for kk, vv in v.items() if kk in ("title", "organization", "url", "license", "retrieved_at", "status", "element_count", "feature_count")} for k, v in manifest.items()},
        "elevation": read_json(RAW / "dem_3dep.meta.json", None),
        "warnings": warnings,
        "attribution": "Road network (c) OpenStreetMap contributors (ODbL); City of Chicago Data Portal; USGS 3DEP.",
        "notes": [
            "Segment geometry 'g' stores interior vertices only; endpoints come from node coordinates.",
            "ow: both|fwd|rev|unknown (digitised direction a->b). walk/drive flags are per-mode access (both directions unless ow restricts driving).",
            "econf marks whether DEM elevation is trustworthy: bare-earth DEM is NOT valid for bridge decks, tunnels, or layered roads.",
        ],
    }
    payload = {"meta": meta, "nodes": nodes, "segments": out_segments}
    out = PROCESSED / "graph.json.gz"
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=6) as f:
        json.dump(payload, f, separators=(",", ":"))
    write_json(PROCESSED / "graph_report.json", {"meta": meta}, indent=2)
    record_manifest("processed:graph", generated_at=now_iso(), counts=dict(counts), file="graph.json.gz", bytes=out.stat().st_size)
    print(json.dumps(meta["counts"], indent=2))
    print("graph.json.gz bytes:", out.stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
