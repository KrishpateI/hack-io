"""Property 19: missing attributes normalize to Unknown, never defaults (Req 3.4, 4.4, 4.5, 12.3).

Hypothesis tests over random raw OSM tag dictionaries and CDP property dictionaries with arbitrary
subsets of attributes present, absent or garbage; the merged segments, the graph Edges built from
them, and the elevation confidence rules.  No network.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from hypothesis import given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH / "scripts"))
sys.path.insert(0, str(SAFEPATH / "backend"))

import build_graph as bg  # noqa: E402
import import_elevation as ie  # noqa: E402
import process_streets as ps  # noqa: E402

UNKNOWN = "Unknown"
BUILT = datetime(2026, 1, 2, tzinfo=timezone.utc)
LON0, LAT0 = -87.64, 41.88
PBT = settings(max_examples=150, deadline=None)


def ll(dx_m: float, dy_m: float) -> tuple[float, float]:
    return (round(LON0 + dx_m / 82_800.0, 6), round(LAT0 + dy_m / 111_200.0, 6))


def lonlat_line(dy: float = 0.0) -> LineString:
    return LineString([ll(0, dy), ll(100, dy)])


# -- strategies --------------------------------------------------------------------------------
GARBAGE = st.sampled_from(["", " ", "maybe", "n/a", "??", "unknown", "x;y"])


def maybe(valid: st.SearchStrategy, garbage: st.SearchStrategy = GARBAGE):
    """absent | valid | garbage, encoded as a (present?, value) pair resolved by osm_tags."""
    return st.one_of(st.none(), valid, garbage)


names = st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != "")


@st.composite
def osm_tags(draw) -> dict[str, str]:
    spec = {
        "name": maybe(names, st.just("")),
        "highway": maybe(st.sampled_from(["primary", "residential", "footway", "steps", "service", "path",
                                          "pedestrian", "corridor", "motorway"]), st.just("")),
        "oneway": maybe(st.sampled_from(["yes", "no", "-1", "true", "false", "reverse"])),
        "bridge": maybe(st.sampled_from(["yes", "no", "viaduct", "movable"]), st.sampled_from(["", " "])),
        "tunnel": maybe(st.sampled_from(["yes", "no", "culvert"]), st.sampled_from(["", " "])),
        "layer": maybe(st.integers(-5, 5).map(str)),
        "foot": maybe(st.sampled_from(["yes", "no", "designated", "permissive"])),
        "access": maybe(st.sampled_from(["no", "private", "yes", "customers"])),
        "wheelchair": maybe(st.sampled_from(["yes", "no", "designated", "limited"])),
        "surface": maybe(st.just("asphalt")),  # irrelevant key: must never leak into attributes
    }
    tags: dict[str, str] = {}
    for key, strat in spec.items():
        v = draw(strat)
        if v is not None:
            tags[key] = v
    return tags


@st.composite
def cdp_props(draw) -> dict[str, str]:
    spec = {
        "trans_id": st.just("1"),
        "street_nam": maybe(names, st.sampled_from(["", " "])),
        "pre_dir": maybe(st.sampled_from(["N", "S", "E", "W"])),
        "street_typ": maybe(st.sampled_from(["ST", "AVE", "BLVD"])),
        "class": maybe(st.sampled_from(["1", "2", "4", "5", "A"]), st.sampled_from(["", " "])),
        "dir_travel": maybe(st.sampled_from(["B", "F", "T", "b", "f"]), st.sampled_from(["", "X", "?"])),
        # CDP has no such attributes; random extras must never be read as sources
        "bridge": maybe(st.just("yes")),
        "tunnel": maybe(st.just("yes")),
        "layer": maybe(st.just("-1")),
        "foot": maybe(st.just("yes")),
    }
    props: dict[str, str] = {}
    for key, strat in spec.items():
        v = draw(strat)
        if v is not None:
            props[key] = v
    return props


def bool_tag(v):
    if v is None or not v.strip():
        return UNKNOWN
    return v.strip().lower() != "no"


# -- osm attribute normalization ---------------------------------------------------------------
# Feature: safepath-evacuation-navigation, Property 19: Missing attributes normalize to Unknown, never defaults
# **Validates: Requirements 3.4, 4.4, 4.5, 12.3**
@PBT
@given(tags=osm_tags(), ped=st.booleans())
def test_osm_attrs_absent_unknown_present_preserved(tags, ped):
    a = ps._osm_attrs(dict(tags), pedestrian_source=ped)

    # name / road_type: literal value when stated, Unknown otherwise (never "" or None)
    assert a["name"] == (tags["name"] if tags.get("name") else UNKNOWN)
    assert a["road_type"] == (tags["highway"] if tags.get("highway") else UNKNOWN)

    # bridge / tunnel: absent or blank -> Unknown (never False); stated -> parsed value
    for key, attr in (("bridge", "bridge_flag"), ("tunnel", "tunnel_flag")):
        assert a[attr] == bool_tag(tags.get(key))
        if bool_tag(tags.get(key)) == UNKNOWN:
            assert a[attr] is not False and a[attr] is not None and a[attr] == UNKNOWN

    # layer: absent -> Unknown (never 0); stated integer preserved
    layer = tags.get("layer")
    if layer is None or not layer.lstrip("-").isdigit():
        assert a["layer"] == UNKNOWN
    else:
        assert a["layer"] == int(layer) and isinstance(a["layer"], int)

    # oneway: only recognised values are kept
    expected_ow = {"yes": "forward", "true": "forward", "-1": "reverse", "reverse": "reverse",
                   "no": "no", "false": "no"}.get(tags.get("oneway", ""), UNKNOWN)
    assert a["oneway"] == expected_ow

    # wheelchair: steps inaccessible by definition, otherwise only a wheelchair tag says anything
    hw = tags.get("highway", "")
    wc = tags.get("wheelchair", "")
    if hw == "steps":
        assert a["mobility_accessibility"] == "inaccessible"
    elif wc in ("yes", "designated"):
        assert a["mobility_accessibility"] == "accessible"
    elif wc == "no":
        assert a["mobility_accessibility"] == "inaccessible"
    else:
        assert a["mobility_accessibility"] == UNKNOWN

    # pedestrian_access: never defaulted from road class on a vehicle source
    foot, access = tags.get("foot", ""), tags.get("access", "")
    if foot in ("yes", "designated", "permissive"):
        assert a["pedestrian_access"] is True
    elif foot == "no" or access in ("no", "private"):
        assert a["pedestrian_access"] is False
    elif ped and hw in ("footway", "pedestrian", "steps", "corridor"):
        assert a["pedestrian_access"] is True
    else:
        assert a["pedestrian_access"] == UNKNOWN

    # every normalized attribute is a stated value or the literal Unknown, never None / ""
    for key in ("name", "road_type", "oneway", "pedestrian_access", "bridge_flag", "tunnel_flag", "layer",
                "infrastructure_level", "mobility_accessibility"):
        assert a[key] is not None and a[key] != ""


# Feature: safepath-evacuation-navigation, Property 19: Missing attributes normalize to Unknown, never defaults
# **Validates: Requirements 3.4, 4.4, 4.5, 12.3**
@PBT
@given(tags=osm_tags(), ped=st.booleans())
def test_infrastructure_level_only_where_a_source_identifies_it(tags, ped):
    a = ps._osm_attrs(dict(tags), pedestrian_source=ped)
    bridge, tunnel = bool_tag(tags.get("bridge")), bool_tag(tags.get("tunnel"))
    layer = int(tags["layer"]) if tags.get("layer", "x").lstrip("-").isdigit() else UNKNOWN
    level = a["infrastructure_level"]

    if bridge == UNKNOWN and tunnel == UNKNOWN and layer == UNKNOWN:
        assert level == UNKNOWN  # nothing identifies a level: never defaulted to surface
    if level != UNKNOWN:
        assert bridge is not UNKNOWN or tunnel is not UNKNOWN or layer != UNKNOWN
    if bridge is True and tunnel is not True:
        assert level == ("elevated" if tags["bridge"].lower() == "viaduct" else "bridge")
    if tunnel is True and bridge is not True:
        assert level == "tunnel"
    if bridge is not True and tunnel is not True and isinstance(layer, int) and layer < 0:
        assert level == "lower-level"
    if bridge is not True and tunnel is not True and isinstance(layer, int) and layer > 0:
        assert level == UNKNOWN  # an unexplained positive layer is not a stated level
    if bridge is True and tunnel is True:
        assert level == UNKNOWN


# -- merged segments (CDP only / CDP + OSM) ----------------------------------------------------
def cdp_item(props: dict[str, str]) -> ps.Item:
    return ps.Item(ps.CDP_DATASET, props.get("trans_id", "1"), lonlat_line(0), props)


def osm_item(tags: dict[str, str], dy: float = 1.0) -> ps.Item:
    return ps.Item(ps.OSM_STREETS, "10", lonlat_line(dy), tags)


# Feature: safepath-evacuation-navigation, Property 19: Missing attributes normalize to Unknown, never defaults
# **Validates: Requirements 3.4, 4.4, 4.5, 12.3**
@PBT
@given(props=cdp_props())
def test_cdp_only_segment_attributes_unknown_unless_cdp_states_them(props):
    builder, _ = ps.merge_streets([cdp_item(props)], [], [], [])
    (seg,) = builder.segments
    for attr in ("pedestrian_access", "bridge_flag", "tunnel_flag", "layer", "infrastructure_level",
                 "mobility_accessibility", "river_crossing"):
        assert seg[attr] == UNKNOWN, attr  # CDP has none of these; extras like bridge=yes are ignored
    cls = props.get("class", "").strip()
    assert seg["road_type"] == (f"cdp_class_{cls}" if cls else UNKNOWN)  # not translated to a road class
    assert seg["oneway"] == {"B": "no", "F": "forward", "T": "reverse"}.get(props.get("dir_travel", "").strip().upper(), UNKNOWN)
    assert (seg["name"] == UNKNOWN) == (not props.get("street_nam", "").strip())
    assert seg["travel_mode"] == ["driving"]


# Feature: safepath-evacuation-navigation, Property 19: Missing attributes normalize to Unknown, never defaults
# **Validates: Requirements 3.4, 4.4, 4.5, 12.3**
@PBT
@given(props=cdp_props(), tags=osm_tags())
def test_matched_segment_takes_stated_values_and_unknown_elsewhere(props, tags):
    builder, _ = ps.merge_streets([cdp_item(props)], [osm_item(tags)], [], [])
    segs = {s["segment_id"]: s for s in builder.segments}
    seg = segs["seg-cdp-1"]
    osm = ps._osm_attrs(dict(tags), pedestrian_source=False)
    for attr in ("road_type", "pedestrian_access", "bridge_flag", "tunnel_flag", "layer", "infrastructure_level",
                 "mobility_accessibility"):
        assert seg[attr] == osm[attr], attr
        assert seg["attribute_sources"][attr] == ("osm" if osm[attr] != UNKNOWN else "none")
    assert seg["river_crossing"] == UNKNOWN  # no source ever states "does not cross"
    cdp_name = ps.cdp_display_name(props)
    assert seg["name"] == (cdp_name or osm["name"])
    cdp_ow = ps._oneway_cdp(props)
    assert seg["oneway"] == (osm["oneway"] if osm["oneway"] != UNKNOWN else cdp_ow)
    for s in builder.segments:
        for attr in ps.ATTRIBUTES:
            assert s[attr] is not None and s[attr] != "", (s["segment_id"], attr)


# Feature: safepath-evacuation-navigation, Property 19: Missing attributes normalize to Unknown, never defaults
# **Validates: Requirements 3.4, 4.4, 4.5, 12.3**
@PBT
@given(tags=osm_tags())
def test_pedestrian_segments_unknown_unless_tagged(tags):
    builder, _ = ps.merge_streets([], [], [ps.Item(ps.OSM_PEDESTRIAN, "1", lonlat_line(0), tags)], [])
    (seg,) = builder.segments
    a = ps._osm_attrs(dict(tags), pedestrian_source=True)
    assert {k: seg[k] for k in a if k != "_warning"} == {k: v for k, v in a.items() if k != "_warning"}
    assert seg["river_crossing"] == UNKNOWN
    if "wheelchair" not in tags and tags.get("highway") != "steps":
        assert seg["mobility_accessibility"] == UNKNOWN


# -- edges built from segments carry Unknown through the Edge model ----------------------------
def sample_strategy():
    known = st.builds(
        ie.ElevationSample,
        st.floats(-50, 500, allow_nan=False, allow_infinity=False).map(lambda v: round(v, 3)),
        resolution_m=st.sampled_from([0.3, 1, 3, 10, 30, ie.UNKNOWN]),
    )
    return st.one_of(known, st.just(ie.UNKNOWN_SAMPLE))


# Feature: safepath-evacuation-navigation, Property 19: Missing attributes normalize to Unknown, never defaults
# **Validates: Requirements 3.4, 4.4, 4.5, 12.3**
@PBT
@given(props=cdp_props(), tags=osm_tags(), with_osm=st.booleans(), s0=sample_strategy(), s1=sample_strategy(),
       have0=st.booleans(), have1=st.booleans())
def test_edges_carry_unknown_through_to_edge_model(props, tags, with_osm, s0, s1, have0, have1):
    builder, _ = ps.merge_streets([cdp_item(props)], [osm_item(tags)] if with_osm else [], [], [])
    segs = [s for s in builder.segments if s["segment_id"] == "seg-cdp-1"]
    (seg,) = segs
    p0, p1 = ie.segment_endpoints(seg)
    elevations = {}
    if have0:
        elevations[p0] = s0
    if have1:
        elevations[p1] = s1
    _, edges, _ = bg.build_network([seg], elevations, BUILT)
    assert edges
    for e in edges:
        for attr in ("bridge_flag", "tunnel_flag", "infrastructure_level", "pedestrian_access",
                     "mobility_accessibility", "road_type"):
            assert getattr(e, attr) == seg[attr], attr
            if seg[attr] == UNKNOWN:
                assert getattr(e, attr) == UNKNOWN
        assert e.current_closure_status == UNKNOWN
        # elevation: Unknown (never 0) whenever the endpoint sample is unavailable
        fwd = e.edge_id.endswith(":f")
        first_have, second_have = (have0, have1) if fwd else (have1, have0)
        first_s, second_s = (s0, s1) if fwd else (s1, s0)
        for have, s, got in ((first_have, first_s, e.elevation_start), (second_have, second_s, e.elevation_end)):
            if not have or not s.known:
                assert got == UNKNOWN and got != 0 and got is not None
            else:
                assert got == s.meters
        if not (first_have and first_s.known and second_have and second_s.known):
            assert e.elevation_confidence == UNKNOWN
        elif ie._is_elevated_or_lower(seg):
            assert e.elevation_confidence == "Low"


# -- elevation confidence ----------------------------------------------------------------------
@st.composite
def segments_with_infra(draw):
    seg: dict = {}
    for key, strat in (
        ("infrastructure_level", st.sampled_from(["bridge", "tunnel", "elevated", "lower-level", "underground",
                                                  "surface", UNKNOWN])),
        ("bridge_flag", st.sampled_from([True, False, UNKNOWN])),
        ("tunnel_flag", st.sampled_from([True, False, UNKNOWN])),
        ("layer", st.one_of(st.integers(-3, 3), st.just(UNKNOWN))),
    ):
        if draw(st.booleans()):
            seg[key] = draw(strat)
    return seg


def is_infra(seg: dict) -> bool:
    layer = seg.get("layer")
    return (seg.get("infrastructure_level") in ("bridge", "tunnel", "elevated", "lower-level", "underground")
            or seg.get("bridge_flag") is True or seg.get("tunnel_flag") is True
            or (isinstance(layer, int) and layer < 0))


# Feature: safepath-evacuation-navigation, Property 19: Missing attributes normalize to Unknown, never defaults
# **Validates: Requirements 3.4, 4.4, 4.5, 12.3**
@PBT
@given(seg=segments_with_infra(), start=sample_strategy(), end=sample_strategy())
def test_elevation_unknown_never_zero_and_infra_confidence_low_or_unknown(seg, start, end):
    out = ie.assign_edge_elevations(seg, start, end)
    assert out["elevation_start"] == start.meters and out["elevation_end"] == end.meters
    for s, got in ((start, out["elevation_start"]), (end, out["elevation_end"])):
        if not s.known:
            assert got == UNKNOWN and got != 0 and got is not None and got is not False
    if not (start.known and end.known):
        assert out["elevation_confidence"] == UNKNOWN
    if is_infra(seg):
        # whatever the sample resolution, deck / tunnel levels never get better than Low
        assert out["elevation_confidence"] in ("Low", UNKNOWN)
    assert out["elevation_confidence"] in ("High", "Medium", "Low", UNKNOWN)
    if out["elevation_confidence"] == "High":
        assert not is_infra(seg) and seg.get("infrastructure_level") == "surface"


# Feature: safepath-evacuation-navigation, Property 19: Missing attributes normalize to Unknown, never defaults
# **Validates: Requirements 3.4, 4.4, 4.5, 12.3**
@PBT
@given(nodata=st.sets(st.integers(0, 11), max_size=12), n_segments=st.integers(1, 6), res=st.sampled_from([0.3, 1, 10]))
def test_unknown_elevation_count_matches_summary(nodata, n_segments, res):
    def fetch(url: str, timeout: float) -> bytes:
        pts = json.loads(parse_qs(urlparse(url).query)["geometry"][0])["points"]
        samples = [{"locationId": i, "value": f"{180 + p[1] - 41:.3f}", "resolution": res}
                   for i, p in enumerate(pts) if i not in nodata]
        return json.dumps({"samples": samples}).encode()

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        import gzip
        segs = [{"geometry": f"LINESTRING (-87.6 {41.80 + i * 0.01:.2f}, -87.61 {41.80 + i * 0.01:.2f})",
                 "bridge_flag": i % 2 == 0} for i in range(n_segments)]
        path = base / "streets" / "segments.jsonl.gz"
        path.parent.mkdir(parents=True)
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            for s in segs:
                fh.write(json.dumps(s) + "\n")
        summary = ie.run(segments_path=path, processed_dir=base / "proc", cache_path=None, fetcher=fetch, pause=0)
        rows = [json.loads(line) for line in gzip.open(base / "proc" / "elevation" / "node_elevations.jsonl.gz", "rt")]
        unknown_rows = sum(1 for r in rows if r["elevation_m"] == UNKNOWN)
        assert all(r["elevation_m"] == UNKNOWN or isinstance(r["elevation_m"], float) for r in rows)
        assert summary["unknown_count"] == unknown_rows
        assert summary["known_count"] + summary["unknown_count"] == summary["node_count"] == len(rows)
        counts = summary["edge_confidence_counts"]
        assert sum(counts.values()) == n_segments
        # bridge segments (even index) never exceed Low; the rest are Medium (no surface level stated) or Unknown
        assert set(counts) <= {"Low", "Medium", "Unknown"}
