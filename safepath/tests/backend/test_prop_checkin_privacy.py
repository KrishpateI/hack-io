"""Property 31: Check-in privacy and history.

For any check-in, visibility excludes location by default; when the user opted in, the stored
location is rounded to a grid no finer than ~200 m (two points in the same grid cell are stored
identically); location data older than the retention period (72 h) is removed by the sweeper
while the check-in record remains; and the displayed current status is the newest check-in with
all previous check-ins retained in history.

**Validates: Requirements 27.3, 27.4, 27.5, 27.8**
"""
# Feature: safepath-evacuation-navigation, Property 31: Check-in privacy and history
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from hypothesis import given, strategies as st

from app.adapters import MemoryRepository
from app.adapters.ports import Principal, Role
from app.auth.accounts import AccountService
from app.communication.checkins import (
    CHECKINS_TABLE, LOCATION_RETENTION, CheckInService, CheckInStatus,
)
from app.communication.events import EventBus
from app.models.privacy import MIN_GRID_M, _M_PER_DEG_LAT, coarsen

INC = "inc_prop31"
T0 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
LAT_STEP = MIN_GRID_M / _M_PER_DEG_LAT


class _Pub:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, incident_id: str, event: Any) -> None:
        self.events.append(event)


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def _env():
    repo = MemoryRepository()
    clock = _Clock()
    pub = _Pub()
    svc = CheckInService(repo, EventBus(repo, pub, clock=clock), AccountService(repo, clock=clock),
                         incident_exists=lambda i: i == INC, clock=clock)
    return repo, clock, pub, svc


def _rows(repo) -> list[dict]:
    return repo.query(CHECKINS_TABLE, {"pk": INC}, limit=5000)


lats = st.floats(min_value=-80, max_value=80, allow_nan=False, allow_infinity=False)
lons = st.floats(min_value=-179.5, max_value=179.5, allow_nan=False, allow_infinity=False)
locations = st.builds(lambda a, b: {"latitude": a, "longitude": b}, lats, lons)

checkin_bodies = st.fixed_dictionaries(
    {"status": st.sampled_from([s.value for s in CheckInStatus])},
    optional={
        "visibility": st.sampled_from(["private", "incident-members", "public"]),
        "share_location": st.booleans(),
        "location": locations,
    },
).filter(lambda b: not (b.get("share_location") is True and "location" not in b))


@given(opted_in=st.booleans(), bodies=st.lists(checkin_bodies, min_size=1, max_size=6),
       gaps=st.lists(st.integers(min_value=1, max_value=3600), min_size=6, max_size=6))
def test_checkin_privacy_retention_and_history(opted_in, bodies, gaps):
    repo, clock, pub, svc = _env()
    user = Principal(subject="user_prop31", role=Role.USER)
    other = Principal(subject="user_other", role=Role.USER)
    if opted_in:
        svc.accounts.set_share_location(user.subject, user.role, True)

    created: list[tuple[dict, dict]] = []
    for body, gap in zip(bodies, gaps):
        clock.now += timedelta(seconds=gap)
        resp = svc.create(user, {"incident_id": INC, **body})
        created.append((body, resp))

    by_id = {r["checkin_id"]: r for r in _rows(repo)}
    assert len(by_id) == len(bodies)
    for body, resp in created:
        c = resp["checkin"]
        row = by_id[c["checkin_id"]]
        # 27.3: visibility defaults to private (excludes location from others)
        assert row["visibility"] == body.get("visibility", "private")
        shares = opted_in and body.get("share_location") is True and "location" in body
        if not shares:
            # no opt-in (preference + explicit flag): no location stored or displayed
            assert row["optional_location"] is None and c["location"] is None
            assert row["location_expires_at"] is None
            continue
        # 27.4: stored/displayed location is the coarse grid point, never the exact input
        loc = body["location"]
        expected = coarsen(loc["latitude"], loc["longitude"])
        assert tuple(row["optional_location"]) == expected
        assert tuple(c["location"]) == expected
        assert coarsen(*expected) == expected  # stored value is already a grid point
        k = expected[0] / LAT_STEP  # grid spacing >= 200 m in latitude
        assert abs(k - round(k)) < 1e-3
        # 27.5: retention ends 72 h after created_at
        created_at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(row["location_expires_at"].replace("Z", "+00:00"))
        assert expires - created_at == LOCATION_RETENTION

    # 27.3: other viewers never see a location for a private check-in
    others_view = svc.list_checkins(INC, other)["checkins"]
    anon_view = svc.list_checkins(INC, None)["checkins"]
    for item in others_view + anon_view:
        assert by_id[item["checkin_id"]]["visibility"] != "private"
    for item in anon_view:
        assert by_id[item["checkin_id"]]["visibility"] == "public"
    assert all("user_id" not in item for item in others_view + anon_view)

    # 27.8: newest is current; every previous check-in retained in history (newest first)
    ids_in_order = [resp["checkin"]["checkin_id"] for _, resp in created]
    mine = svc.list_checkins(INC, user)["mine"]
    assert mine["current"]["checkin_id"] == ids_in_order[-1]
    assert [h["checkin_id"] for h in mine["history"]] == list(reversed(ids_in_order))

    # 27.5: the sweeper's scrub keeps locations before their expiry, removes them after
    first_created = T0 + timedelta(seconds=gaps[0])
    with_loc = {i for i, r in by_id.items() if r["optional_location"] is not None}
    assert svc.scrub_expired_locations(first_created + LOCATION_RETENTION - timedelta(seconds=1)) == 0
    assert {i for i, r in ((r["checkin_id"], r) for r in _rows(repo))
            if r["optional_location"] is not None} == with_loc
    late = clock.now + LOCATION_RETENTION
    assert svc.scrub_expired_locations(late) == len(with_loc)
    assert svc.scrub_expired_locations(late) == 0  # idempotent
    after = {r["checkin_id"]: r for r in _rows(repo)}
    assert set(after) == set(by_id)  # records remain
    for cid, r in after.items():
        assert r["optional_location"] is None and r["location_expires_at"] is None
        assert r["status"] == by_id[cid]["status"] and r["visibility"] == by_id[cid]["visibility"]
    mine = svc.list_checkins(INC, user)["mine"]
    assert [h["checkin_id"] for h in mine["history"]] == list(reversed(ids_in_order))
    assert all(h["location"] is None for h in mine["history"])


@given(lat=st.floats(min_value=-80, max_value=80, allow_nan=False),
       lon=st.floats(min_value=-179, max_value=179, allow_nan=False),
       f1=st.tuples(st.floats(-0.4, 0.4), st.floats(-0.4, 0.4)),
       f2=st.tuples(st.floats(-0.4, 0.4), st.floats(-0.4, 0.4)))
def test_same_grid_cell_stored_identically(lat, lon, f1, f2):
    repo, clock, _, svc = _env()
    user = Principal(subject="user_cell", role=Role.USER)
    svc.accounts.set_share_location(user.subject, user.role, True)
    clat, clon = coarsen(lat, lon)
    lon_step = MIN_GRID_M / (_M_PER_DEG_LAT * max(math.cos(math.radians(clat)), 0.01))
    stored = []
    for dlat, dlon in (f1, f2):
        clock.now += timedelta(seconds=1)
        point = {"latitude": clat + dlat * LAT_STEP, "longitude": clon + dlon * lon_step}
        resp = svc.create(user, {"incident_id": INC, "status": "safe", "visibility": "public",
                                 "share_location": True, "location": point})
        stored.append(tuple(resp["checkin"]["location"]))
    rows = _rows(repo)
    assert stored[0] == stored[1] == (clat, clon)
    assert {tuple(r["optional_location"]) for r in rows} == {(clat, clon)}
