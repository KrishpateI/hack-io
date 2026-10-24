from datetime import date, datetime, timezone

import pytest
from hypothesis import given, strategies as st
from pydantic import ValidationError

from app.models.graph import Edge, Graph, GraphIntegrityError, Node
from app.models.provenance import DataClassification, ReuseDetermination, SourceRecord
from app.services.graph_artifact import (
    ArtifactError,
    read_artifact,
    read_latest_version,
    write_artifact,
)

TS = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)


def node(i, level="surface"):
    return Node(node_id=i, lat=41.87, lon=-87.65, kind="intersection", level=level)


def edge(i="e1", a="a", b="b", **kw):
    base = dict(
        edge_id=i, from_node=a, to_node=b, geometry="abc", distance_meters=10.0,
        estimated_travel_time_seconds=8.0, travel_mode=["walking"], source="osm",
        data_timestamp=TS, classification="Static",
    )
    base.update(kw)
    return Edge(**base)


def test_defaults_are_unknown_literal():
    e = edge()
    assert e.road_type == "Unknown" and e.bridge_flag == "Unknown"
    assert e.pedestrian_access == "Unknown" and e.elevation_start == "Unknown"
    assert node("a").elevation_m == "Unknown"


@pytest.mark.parametrize("field", ["bridge_flag", "pedestrian_access", "elevation_start", "road_type"])
@pytest.mark.parametrize("bad", [None, ""])
def test_null_or_empty_not_accepted_for_unknown(field, bad):
    with pytest.raises(ValidationError):
        edge(**{field: bad})


def test_zero_not_a_bool_or_unknown_substitute():
    with pytest.raises(ValidationError):
        edge(bridge_flag=0)
    assert edge(elevation_start=0).elevation_start == 0  # real zero value is fine


@pytest.mark.parametrize("field", ["distance_meters", "estimated_travel_time_seconds"])
@pytest.mark.parametrize("bad", [0, -1.0, "Unknown", None])
def test_positive_required(field, bad):
    with pytest.raises(ValidationError):
        edge(**{field: bad})


def test_level_transition_needs_type():
    with pytest.raises(ValidationError):
        edge(edge_kind="level_transition")
    with pytest.raises(ValidationError):
        edge(transition_type="exit")


def test_missing_endpoint_excluded_with_warning():
    g = Graph.build([node("a"), node("b")], [edge("e1"), edge("e2", b="zzz")])
    assert [e.edge_id for e in g.edges] == ["e1"]
    assert "e2" in g.warnings[0]


def test_level_difference_requires_transition():
    nodes = [node("a"), node("b", level="platform")]
    with pytest.raises(GraphIntegrityError):
        Graph.build(nodes, [edge()])
    ok = edge(edge_kind="level_transition", transition_type="platform_stair")
    assert len(Graph.build(nodes, [ok]).edges) == 1


def test_source_record_and_classification():
    assert {c.value for c in DataClassification} == {"Historical", "Static", "Current", "Simulated"}
    sr = SourceRecord(
        source_id="s1", organization="UIC", url="https://x", title="t", published_at=date(2020, 1, 1),
        retrieved_at=TS, geographic_applicability="UIC", data_classification="Static",
        verification_status="verified", is_official_source=True,
        reuse_determination=ReuseDetermination(approved_content=["a"], excluded_content=["codes"]),
    )
    assert SourceRecord.model_validate_json(sr.model_dump_json()) == sr
    with pytest.raises(ValidationError):
        SourceRecord(**{**sr.model_dump(), "data_classification": "Bogus"})


def make_graph(n=3):
    nodes = [node(f"n{i}") for i in range(n)]
    edges = [edge(f"e{i}", f"n{i}", f"n{(i + 1) % n}") for i in range(n)]
    return Graph.build(nodes, edges)


def test_artifact_roundtrip_and_latest(tmp_path):
    g = make_graph()
    layers = {"buildings": {"type": "FeatureCollection", "features": []}}
    v = write_artifact(tmp_path, g, layers, built_at=TS, source_record_ids=["s1"])
    assert v.startswith("v20260315-") and len(v.split("-")[1]) == 6
    assert read_latest_version(tmp_path) == v
    g2, m, l2 = read_artifact(tmp_path)
    assert g2 == g and l2 == layers
    assert m.counts["nodes"] == 3 and m.counts["edges"] == 3 and m.source_record_ids == ["s1"]
    assert set(m.sha256) == {"nodes.jsonl.gz", "edges.jsonl.gz", "layers/buildings.geojson"}


def test_no_overwrite_and_new_version_updates_latest(tmp_path):
    g = make_graph()
    v1 = write_artifact(tmp_path, g, built_at=TS)
    before = (tmp_path / "graphs" / v1 / "nodes.jsonl.gz").read_bytes()
    with pytest.raises(ArtifactError):
        write_artifact(tmp_path, g, built_at=TS)
    assert read_latest_version(tmp_path) == v1
    v2 = write_artifact(tmp_path, make_graph(4), built_at=TS)
    assert v2 != v1 and read_latest_version(tmp_path) == v2
    assert (tmp_path / "graphs" / v1 / "nodes.jsonl.gz").read_bytes() == before
    assert not [p for p in (tmp_path / "graphs").iterdir() if p.name.startswith(".")]


def test_checksum_mismatch_detected(tmp_path):
    v = write_artifact(tmp_path, make_graph(), built_at=TS)
    p = tmp_path / "graphs" / v / "edges.jsonl.gz"
    p.write_bytes(p.read_bytes() + b"x")
    with pytest.raises(ArtifactError):
        read_artifact(tmp_path, v)


@given(
    st.lists(
        st.tuples(
            st.one_of(st.just("Unknown"), st.booleans()),
            st.one_of(st.just("Unknown"), st.floats(-100, 500, allow_nan=False)),
            st.floats(0.001, 1e5),
        ),
        min_size=1, max_size=5,
    )
)
def test_graph_roundtrip_property(tmp_path_factory, rows):
    """**Validates: Requirements 7.8, 12.3**"""
    root = tmp_path_factory.mktemp("a")
    nodes = [node("a"), node("b")]
    edges = [
        edge(f"e{i}", bridge_flag=b, elevation_start=el, distance_meters=d)
        for i, (b, el, d) in enumerate(rows)
    ]
    g = Graph.build(nodes, edges)
    write_artifact(root, g, built_at=TS)
    g2, _, _ = read_artifact(root)
    assert g2 == g
