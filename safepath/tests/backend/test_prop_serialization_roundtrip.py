"""Property 22: Serialization round trip.

**Validates: Requirements 7.8, 30.1, 30.2, 30.3, 30.4, 30.5, 30.6, 30.8**

Graph (artifact with manifest), Source_Record, OfficialNotice, HazardReport, Message, CheckIn,
Incident, User and realtime Event serialize to the storage/wire format and parse back equal.
Every created record id is unique within its record type.
"""
# Feature: safepath-evacuation-navigation, Property 22: Serialization round trip
from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hypothesis import HealthCheck, given, settings, strategies as st

from app.adapters.ports import Event
from app.adapters.repository import SqliteRepository
from app.communication.events import event_to_dict
from app.models.domain import Incident, OfficialNotice
from app.models.ids import PREFIXES, new_id
from app.models.provenance import DataClassification, ReuseDetermination, SourceRecord
from app.models.records import (
    CheckInRecord, EventRecord, HazardReportRecord, MessageRecord, PrivacyPreferences,
    StatusHistoryEntry, UserRecord,
)
from app.services.graph_artifact import read_artifact, write_artifact
from strategies import NOW, graph_models, incidents, notices, reports

# Profile "ci" (conftest.py) sets max_examples=100; keep that and only relax deadline/health checks.
SETTINGS = settings(
    settings.get_profile("ci"), max_examples=100, deadline=None,
    suppress_health_check=list(HealthCheck),
)

_TXT = st.text(max_size=30)
_ID = st.text(alphabet="abcdefghij0123456789_-", min_size=1, max_size=12)
_DT = st.integers(0, 10**6).map(lambda m: NOW - timedelta(minutes=m))
_COORD = st.tuples(st.floats(41.8, 41.9, allow_nan=False), st.floats(-87.7, -87.6, allow_nan=False))
_JSON = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(-10**6, 10**6),
              st.floats(allow_nan=False, allow_infinity=False), _TXT),
    lambda c: st.one_of(st.lists(c, max_size=3), st.dictionaries(_ID, c, max_size=3)),
    max_leaves=8,
)


def _through_storage(model, cls):
    """Storage path: model_dump(mode=json) -> SqliteRepository row -> model_validate."""
    item = {**model.model_dump(mode="json"), "pk": "p", "sk": "s"}
    repo = SqliteRepository()
    repo.put("t", item)
    row = repo.get("t", {"pk": "p", "sk": "s"})
    return cls.model_validate({k: v for k, v in row.items() if k not in ("pk", "sk")})


def _check(model, cls):
    assert cls.model_validate_json(model.model_dump_json()) == model
    assert cls.model_validate(json.loads(json.dumps(model.model_dump(mode="json")))) == model
    assert _through_storage(model, cls) == model


@st.composite
def source_records(draw):
    return SourceRecord(
        source_id=draw(_ID), organization=draw(_TXT), url="https://example.invalid/" + draw(_ID),
        title=draw(_TXT), published_at=draw(st.one_of(st.none(), st.dates(date(2000, 1, 1), date(2030, 1, 1)))),
        retrieved_at=draw(_DT), geographic_applicability=draw(_TXT),
        data_classification=draw(st.sampled_from(list(DataClassification))),
        verification_status=draw(st.sampled_from(["verified", "unverified", "unknown", "simulated"])),
        page_or_section=draw(st.one_of(st.none(), _TXT)),
        usage_restrictions=draw(st.lists(_TXT, max_size=3)),
        is_official_source=draw(st.booleans()),
        reuse_determination=draw(st.one_of(st.none(), st.builds(
            ReuseDetermination, approved_content=st.lists(_TXT, max_size=3),
            excluded_content=st.lists(_TXT, max_size=3),
            determined_at=st.one_of(st.none(), _DT), basis=st.one_of(st.none(), _TXT)))),
        check_status=draw(st.one_of(st.none(), st.sampled_from(["ok", "unreachable", "not_located"]))),
        checked_at=draw(st.one_of(st.none(), _DT)),
    )


@st.composite
def hazard_records(draw):
    base = draw(reports())
    at = draw(_DT)
    return HazardReportRecord(
        **base.model_dump(), reporter_id=draw(_ID), alias=draw(_TXT), description=draw(_TXT),
        photo_key=draw(st.one_of(st.none(), _ID)), updated_at=at,
        status_history=draw(st.lists(st.builds(
            StatusHistoryEntry, status=_ID, at=_DT, actor_role=st.one_of(st.none(), _ID),
            reason=st.one_of(st.none(), _TXT), previous_status=st.one_of(st.none(), _ID),
            actor_alias=st.one_of(st.none(), _TXT)), max_size=3)),
        duplicate_of=draw(st.one_of(st.none(), _ID)), version=draw(st.integers(1, 50)),
        client_report_id=draw(st.one_of(st.none(), _ID)),
        source_category=draw(st.one_of(st.none(), _ID)), restriction_id=draw(st.one_of(st.none(), _ID)),
    )


@st.composite
def messages(draw):
    return MessageRecord(
        message_id=new_id("message"), incident_id=draw(_ID), sender_id=draw(_ID), alias=draw(_TXT),
        message_type=draw(_ID), content=draw(_TXT), created_at=draw(_DT),
        approximate_location=draw(st.one_of(st.none(), _COORD)), share_location=draw(st.booleans()),
        hidden=draw(st.booleans()), source_label=draw(st.one_of(st.none(), _TXT)),
    )


@st.composite
def checkins(draw):
    return CheckInRecord(
        checkin_id=new_id("checkin"), incident_id=draw(_ID), user_id=draw(_ID), alias=draw(_TXT),
        status=draw(_ID), created_at=draw(_DT), optional_location=draw(st.one_of(st.none(), _COORD)),
        visibility=draw(st.sampled_from(["private", "incident-members", "public", "shared"])),
        location_expires_at=draw(st.one_of(st.none(), _DT)),
    )


@st.composite
def users(draw):
    return UserRecord(
        user_id=new_id("user"), display_name=draw(st.one_of(st.none(), _TXT)),
        role=draw(st.sampled_from(["guest", "user", "moderator", "authorized_official"])),
        created_at=draw(_DT), privacy_preferences=PrivacyPreferences(share_location=draw(st.booleans())),
        alias_seed=draw(st.one_of(st.none(), _ID)),
    )


@st.composite
def events(draw):
    created = draw(_DT)
    return EventRecord(
        event_id=new_id("event"), incident_id=draw(_ID), type=draw(_ID),
        payload=draw(st.dictionaries(_ID, _JSON, max_size=4)), created_at=created,
        ttl=draw(st.one_of(st.none(), st.integers(0, 2 * 10**9))),
    )


@SETTINGS
@given(g=graph_models(), ca=_DT)
def test_graph_artifact_round_trip(g, ca):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        version = write_artifact(root, g, built_at=ca, source_record_ids=["s1", "s2"])
        g2, manifest, layers = read_artifact(root)
    assert g2 == g
    assert manifest.version == version
    assert manifest.counts == {"nodes": len(g.nodes), "edges": len(g.edges), "layers": 0}
    assert manifest.built_at == ca and manifest.source_record_ids == ["s1", "s2"]
    assert layers == {}


@SETTINGS
@given(g=graph_models())
def test_graph_json_round_trip(g):
    from app.models.graph import Graph
    assert Graph.model_validate_json(g.model_dump_json()) == g


@SETTINGS
@given(m=source_records())
def test_source_record_round_trip(m):
    _check(m, SourceRecord)


@SETTINGS
@given(m=notices())
def test_notice_round_trip(m):
    _check(m, OfficialNotice)


@SETTINGS
@given(m=hazard_records())
def test_hazard_report_round_trip(m):
    _check(m, HazardReportRecord)


@SETTINGS
@given(m=messages())
def test_message_round_trip(m):
    _check(m, MessageRecord)


@SETTINGS
@given(m=checkins())
def test_checkin_round_trip(m):
    _check(m, CheckInRecord)


@SETTINGS
@given(m=incidents())
def test_incident_round_trip(m):
    _check(m, Incident)


@SETTINGS
@given(m=users())
def test_user_round_trip(m):
    _check(m, UserRecord)


@SETTINGS
@given(m=events())
def test_event_record_round_trip(m):
    _check(m, EventRecord)


@SETTINGS
@given(m=events())
def test_realtime_event_envelope_round_trip(m):
    pub = m.public_view()
    ev = Event(id=pub.event_id, incident_id=pub.incident_id, type=pub.type,
               payload=pub.payload, created_at=pub.created_at.isoformat())
    wire = json.loads(json.dumps(event_to_dict(ev)))
    back = Event(id=wire["event_id"], incident_id=wire["incident_id"], type=wire["type"],
                 payload=wire["payload"], created_at=wire["created_at"])
    assert back == Event(ev.id, ev.incident_id, ev.type, dict(ev.payload), ev.created_at)


@SETTINGS
@given(n=st.integers(2, 200))
def test_ids_unique_within_record_type(n):
    for rtype in PREFIXES:
        ids = [new_id(rtype) for _ in range(n)]
        assert len(set(ids)) == n
        assert all(i.startswith(PREFIXES[rtype] + "_") for i in ids)
