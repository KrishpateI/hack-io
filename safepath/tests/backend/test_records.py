"""Tests for persistent records, ids and public/private serializers (30.x, 22.5, 31.8)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, strategies as st

from app.models.ids import PREFIXES, new_id, new_ulid, record_type_of, report_id_for
from app.models.privacy import MIN_GRID_M, coarsen, load_alias_secret, reporter_alias
from app.models.records import (
    AuditLogRecord, CheckInRecord, EventRecord, HazardReportRecord, MessageRecord,
    OverlayRecord, ReportVote, UserRecord, view_for,
)

NOW = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
SECRET = b"test-secret-injected"


def test_ids_prefixed_unique_and_sortable():
    ids = [new_ulid() for _ in range(2000)]
    assert len(set(ids)) == 2000 and ids == sorted(ids)
    assert all(len(i) == 26 for i in ids)
    for rtype, prefix in PREFIXES.items():
        i = new_id(rtype)
        assert i.startswith(prefix + "_") and record_type_of(i) == rtype
    assert len(set(PREFIXES.values())) == len(PREFIXES)
    assert record_type_of("bogus") is None
    assert new_ulid(1000) < new_ulid(2000)


def test_report_id_idempotent():
    a = report_id_for("u1", "c1")
    assert a == report_id_for("u1", "c1") != report_id_for("u1", "c2")
    assert a.startswith("rpt_")


def test_alias_properties():
    a = reporter_alias(SECRET, "user-123", "inc_1")
    assert a == reporter_alias(SECRET, "user-123", "inc_1")
    assert a != reporter_alias(b"other", "user-123", "inc_1")
    assert "user-123" not in a and "inc_1" not in a


def test_secret_from_env_only():
    assert load_alias_secret({"SAFEPATH_ALIAS_SECRET": "s"}) == b"s"
    with pytest.raises(RuntimeError):
        load_alias_secret({})


@settings(max_examples=50)
@given(st.floats(41.6, 42.1), st.floats(-88.0, -87.4))
def test_coarsen_idempotent_and_coarse(lat, lon):
    c = coarsen(lat, lon)
    assert coarsen(*c) == pytest.approx(c, abs=1e-5)
    assert abs(c[0] - lat) <= (MIN_GRID_M / 111_320) / 2 + 1e-6


def _report():
    return HazardReportRecord(
        report_id=new_id("report"), incident_id="inc_1", category="flooding",
        latitude=41.88, longitude=-87.63, created_at=NOW, expires_at=NOW + timedelta(hours=1),
        reporter_id="user-secret-id", alias=reporter_alias(SECRET, "user-secret-id", "inc_1"),
        updated_at=NOW, photo_key="photos/x.jpg", client_report_id="cli-1",
    )


def test_report_views():
    r = _report()
    pub = r.public_view().model_dump_json()
    assert "user-secret-id" not in pub and "reporter_id" not in pub and "photo_key" not in pub
    priv = view_for(r, "user-secret-id")
    assert priv.photo_key == "photos/x.jpg"
    assert "reporter_id" not in priv.model_dump()
    assert not hasattr(view_for(r, "someone-else"), "photo_key")


def test_checkin_location_privacy():
    base = dict(checkin_id=new_id("checkin"), incident_id="inc_1", user_id="u9", alias="A B 001",
                status="safe", created_at=NOW, optional_location=(41.881234, -87.631234))
    private = CheckInRecord(**base)
    assert private.public_view(NOW).location is None
    shared = CheckInRecord(**base, visibility="shared", location_expires_at=NOW + timedelta(minutes=5))
    assert shared.public_view(NOW).location == coarsen(41.881234, -87.631234)
    assert shared.public_view(NOW).location != (41.881234, -87.631234)
    assert shared.public_view(NOW + timedelta(minutes=6)).location is None
    assert shared.public_view(None).location is None
    assert "u9" not in shared.public_view(NOW).model_dump_json()
    assert view_for(shared, "u9").visibility == "shared"


def test_message_location_opt_in_and_hidden():
    base = dict(message_id=new_id("message"), incident_id="inc_1", sender_id="u7", alias="A B 002",
                message_type="help", content="hello", created_at=NOW,
                approximate_location=(41.88123, -87.63123))
    m = MessageRecord(**base)
    assert m.public_view().approximate_location is None
    assert "u7" not in m.public_view().model_dump_json()
    opt = MessageRecord(**base, share_location=True)
    assert opt.public_view().approximate_location == coarsen(41.88123, -87.63123)
    hid = MessageRecord(**base, hidden=True)
    assert hid.public_view().content == "" and hid.private_view().content == "hello"


def test_other_records_hide_private_fields():
    u = UserRecord(user_id="u1", display_name="Ann Lee", created_at=NOW, alias_seed="seed")
    assert "Ann" not in u.public_view("Calm Fox 001").model_dump_json()
    assert "alias_seed" not in u.private_view().model_dump()
    v = ReportVote(report_id="rpt_1", user_id="u1", action="confirm", created_at=NOW)
    assert "u1" not in v.public_view().model_dump_json()
    a = AuditLogRecord(audit_id=new_id("audit"), day="2025-01-01", ts=NOW, actor_id="u1",
                       actor_role="moderator", action="hide", target_id="msg_1")
    assert "u1" not in a.public_view().model_dump_json()
    assert a.private_view().actor_id == "u1"
    e = EventRecord(event_id=new_id("event"), incident_id="inc_1", type="x", created_at=NOW,
                    payload={"report": {"reporter_id": "u1", "category": "c"}, "l": [{"user_id": "u2"}]})
    assert "u1" not in e.public_view().model_dump_json() and "u2" not in e.public_view().model_dump_json()
    o = OverlayRecord(restriction_id=new_id("overlay"), incident_id="inc_1", kind="closure",
                      created_at=NOW, source_report_id="rpt_1")
    assert "source_report_id" not in o.public_view().model_dump()
    assert o.private_view().source_report_id == "rpt_1"
