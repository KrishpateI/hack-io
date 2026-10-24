"""Property tests for geometry clipping and region bounds."""
from __future__ import annotations

from hypothesis import given, strategies as st

from app import geo
from app.geo import CoordinateStatus as S

lons = st.floats(-180, 180, allow_nan=False, allow_infinity=False)
lats = st.floats(-90, 90, allow_nan=False, allow_infinity=False)
near_lons = st.floats(geo.WEST - 0.05, geo.EAST + 0.05, allow_nan=False)
near_lats = st.floats(geo.SOUTH - 0.05, geo.NORTH + 0.05, allow_nan=False)
points = st.tuples(near_lons, near_lats)
bad_values = st.one_of(
    st.none(), st.text(max_size=5), st.booleans(),
    st.just(float("nan")), st.just(float("inf")), st.just(float("-inf")),
)


def _inside(lat, lon):
    return geo.SOUTH <= lat <= geo.NORTH and geo.WEST <= lon <= geo.EAST


# Feature: safepath-evacuation-navigation, Property 20: Geometry clipping and region bounds are exact and boundary-inclusive
# **Validates: Requirements 2.1, 2.4, 2.5, 23.8**
@given(st.lists(points, min_size=1, max_size=12))
def test_clipped_linestring_vertices_within_bounds(coords):
    for part in geo.clip_linestring(coords):
        for lon, lat in part:
            assert geo.WEST <= lon <= geo.EAST
            assert geo.SOUTH <= lat <= geo.NORTH


# Feature: safepath-evacuation-navigation, Property 20: Geometry clipping and region bounds are exact and boundary-inclusive
# **Validates: Requirements 2.1, 2.4, 2.5, 23.8**
@given(near_lons, near_lats)
def test_clip_point_exact(lon, lat):
    expected = (lon, lat) if _inside(lat, lon) else None
    assert geo.clip_point(lon, lat) == expected


# Feature: safepath-evacuation-navigation, Property 20: Geometry clipping and region bounds are exact and boundary-inclusive
# **Validates: Requirements 2.1, 2.4, 2.5, 23.8**
@given(lats, lons)
def test_classification_valid_pairs(lat, lon):
    expected = S.INSIDE if _inside(lat, lon) else S.OUTSIDE_REGION
    assert geo.classify_coordinate(lat, lon) == expected


# Feature: safepath-evacuation-navigation, Property 20: Geometry clipping and region bounds are exact and boundary-inclusive
# **Validates: Requirements 2.1, 2.4, 2.5, 23.8**
@given(st.sampled_from([geo.SOUTH, geo.NORTH]), st.sampled_from([geo.WEST, geo.EAST]))
def test_corners_are_inside(lat, lon):
    assert geo.classify_coordinate(lat, lon) == S.INSIDE
    assert geo.clip_point(lon, lat) == (lon, lat)


# Feature: safepath-evacuation-navigation, Property 20: Geometry clipping and region bounds are exact and boundary-inclusive
# **Validates: Requirements 2.1, 2.4, 2.5, 23.8**
@given(
    st.one_of(
        st.tuples(bad_values, lons),
        st.tuples(lats, bad_values),
        st.tuples(st.floats(90.0001, 1e6), lons),
        st.tuples(st.floats(-1e6, -90.0001), lons),
        st.tuples(lats, st.floats(180.0001, 1e6)),
        st.tuples(lats, st.floats(-1e6, -180.0001)),
    )
)
def test_invalid_pairs(pair):
    assert geo.classify_coordinate(*pair) == S.INVALID


# Feature: safepath-evacuation-navigation, Property 20: Geometry clipping and region bounds are exact and boundary-inclusive
# **Validates: Requirements 2.1, 2.4, 2.5, 23.8**
@given(st.tuples(lats, lons), st.tuples(lats, lons))
def test_endpoint_failure_identification(o, d):
    res = geo.classify_endpoints(o, d)
    co, cd = geo.classify_coordinate(*o), geo.classify_coordinate(*d)
    oi, di = co == S.INSIDE, cd == S.INSIDE
    if oi and di:
        assert res == {"status": S.INSIDE, "failed": None}
    else:
        assert res["failed"] == ("both" if not oi and not di else "destination" if oi else "origin")
        assert res["status"] in (S.INVALID, S.OUTSIDE_REGION)
