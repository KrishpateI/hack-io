"""Example tests for app.geo (Req 2.1, 2.4, 2.5, 6.2, 6.3)."""
import pytest

from app import geo
from app.geo import CoordinateStatus as S


@pytest.mark.parametrize("lat,lon,exp", [
    (41.88, -87.63, S.INSIDE),
    (41.91, -87.68, S.INSIDE), (41.85, -87.60, S.INSIDE),
    (41.9101, -87.63, S.OUTSIDE_REGION), (41.88, -87.5999, S.OUTSIDE_REGION),
    (None, -87.63, S.INVALID), ("41.88", -87.63, S.INVALID),
    (91, -87.63, S.INVALID), (41.88, 181, S.INVALID), (float("nan"), 0, S.INVALID),
])
def test_classify_coordinate(lat, lon, exp):
    assert geo.classify_coordinate(lat, lon) == exp


def test_classify_endpoints_identifies_failed():
    ok, out, bad = (41.88, -87.63), (40.0, -87.63), (None, 1)
    assert geo.classify_endpoints(ok, ok) == {"status": S.INSIDE, "failed": None}
    assert geo.classify_endpoints(out, ok)["failed"] == "origin"
    assert geo.classify_endpoints(ok, out)["failed"] == "destination"
    assert geo.classify_endpoints(out, out)["failed"] == "both"
    r = geo.classify_endpoints(ok, bad)
    assert r == {"status": S.INVALID, "failed": "destination"}


def test_clip_linestring_vertices_within_bounds():
    parts = geo.clip_linestring([(-88.0, 41.88), (-87.5, 41.88)])
    assert parts == [[(geo.WEST, 41.88), (geo.EAST, 41.88)]]
    assert geo.clip_linestring([(-90, 40), (-89, 40)]) == []
    for part in geo.clip_linestring([(-87.65, 41.9), (-87.7, 41.95), (-87.62, 41.86)]):
        assert all(geo.in_bbox(x, y) for x, y in part)


def test_haversine():
    assert geo.haversine(41.88, -87.63, 41.88, -87.63) == 0
    # one degree of latitude is about 111.2 km
    assert geo.haversine(41.0, -87.0, 42.0, -87.0) == pytest.approx(111_195, rel=1e-3)


@pytest.mark.parametrize("h,f,exp", [
    (11.99, None, "Low-rise"), (12, None, "Mid-rise"), (34.99, None, "Mid-rise"),
    (35, None, "High-rise"),
    (None, 1, "Low-rise"), (None, 3, "Low-rise"), (None, 4, "Mid-rise"),
    (None, 11, "Mid-rise"), (None, 12, "High-rise"),
    (50, 2, "High-rise"), (5, 40, "Low-rise"),  # height wins
    (None, None, "Unknown"), (0, 0, "Unknown"), (-5, -1, "Unknown"),
    ("tall", "x", "Unknown"), (0, 5, "Mid-rise"), ("n/a", 2, "Low-rise"),
])
def test_building_height_class(h, f, exp):
    assert geo.building_height_class(h, f) == exp
