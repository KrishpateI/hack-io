"""Property 30: Public views never leak private fields.

For any stored record and any non-owner principal, the serialized public view omits private
fields (display name, email, account id, reporter id, exact check-in or user location). The
reporter alias contains no substring of the display name, email or account id (case-insensitive,
substrings of length >= 3). Exact location is returned only to the owner and published only when
the owner opted in.

**Validates: Requirements 22.5, 31.6, 31.8**
"""
# Feature: safepath-evacuation-navigation, Property 30: Public views never leak private fields
from __future__ import annotations

import json
import re
import string
from datetime import timedelta
from typing import Any

from hypothesis import given, strategies as st

from strategies import NOW, reports

from app.models.ids import _CROCKFORD, PREFIXES
from app.models.privacy import _ADJ, _NOUN, coarsen, reporter_alias
from app.models.records import (
    AuditLogRecord, CheckInRecord, EventRecord, HazardReportRecord, MessageRecord, ReportVote,
    UserRecord, view_for,
)

# Keys that must never appear in any serialized public view (31.8).
PRIVATE_KEYS = {
    "reporter_id", "sender_id", "user_id", "actor_id", "display_name", "email", "alias_seed",
    "photo_key", "client_report_id", "privacy_preferences", "visibility", "location_expires_at",
    "share_location", "status_history", "version", "ttl",
}

# ---------------------------------------------------------------- alias vocabulary
# Aliases are "<Adj> <Noun> <NNN>" from a fixed public word list. Identities that happen to share
# a 3-gram with that public vocabulary would match by coincidence, not by leakage, so generated
# identities are constrained to be disjoint from it.
_ALIAS_RE = re.compile(
    rf"^(?:{'|'.join(_ADJ)}) (?:{'|'.join(_NOUN)}) \d{{3}}$"
)


def _grams(s: str) -> set[str]:
    s = s.lower()
    return {s[i:i + 3] for i in range(len(s) - 2)}


_VOCAB_GRAMS: set[str] = set()
for _a in _ADJ:
    for _n in _NOUN:
        _VOCAB_GRAMS |= _grams(f"{_a} {_n} ")
for _n in _NOUN:
    for _d in string.digits:
        _VOCAB_GRAMS.add(f"{_n[-1].lower()} {_d}")
for _d in string.digits:
    for _e in string.digits:
        _VOCAB_GRAMS.add(f" {_d}{_e}")
_DIGIT_RUN = re.compile(r"\d{3}")


def _disjoint_from_alias_vocab(s: str) -> bool:
    return not _DIGIT_RUN.search(s) and not (_grams(s) & _VOCAB_GRAMS)


# ---------------------------------------------------------------- identity strategies
def _ids(record_type: str) -> st.SearchStrategy[str]:
    """``<prefix>_<ULID>`` ids in the same format as ``new_id``."""
    ulid = st.tuples(
        st.sampled_from("01234567"),
        st.text(alphabet=_CROCKFORD, min_size=25, max_size=25),
    ).map("".join)
    return ulid.map(lambda u: f"{PREFIXES[record_type]}_{u}")


account_ids = _ids("user").filter(_disjoint_from_alias_vocab)
display_names = st.text(
    alphabet=string.ascii_letters + " '-", min_size=3, max_size=20,
).filter(lambda s: s.strip() != "" and _disjoint_from_alias_vocab(s))
emails = st.from_regex(r"[a-z][a-z0-9._]{1,10}@[a-z]{2,8}\.(com|org|net)", fullmatch=True).filter(
    _disjoint_from_alias_vocab
)
secrets = st.binary(min_size=8, max_size=32)
incident_ids = _ids("incident")
_LAT = st.floats(41.85, 41.91, allow_nan=False, allow_infinity=False)
_LON = st.floats(-87.68, -87.60, allow_nan=False, allow_infinity=False)
locations = st.tuples(_LAT, _LON)
free_text = st.text(alphabet=string.ascii_letters + " .,!?", max_size=40)


def _non_owner(owner: str) -> st.SearchStrategy[str | None]:
    return st.one_of(st.none(), account_ids.filter(lambda s: s != owner))


def _dump(view: Any) -> dict[str, Any]:
    return json.loads(view.model_dump_json())


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        out = set(value)
        for v in value.values():
            out |= _all_keys(v)
        return out
    if isinstance(value, list):
        return set().union(*(_all_keys(v) for v in value)) if value else set()
    return set()


def _assert_no_leak(public: Any, *secret_values: str) -> None:
    data = _dump(public)
    leaked = _all_keys(data) & PRIVATE_KEYS
    assert not leaked, f"private keys in public view: {leaked}"
    text = json.dumps(data)
    for value in secret_values:
        if value:
            assert value not in text, f"private value {value!r} leaked into {text}"


# ---------------------------------------------------------------- alias (22.5)
@given(secret=secrets, user_id=account_ids, name=display_names, email=emails, inc=incident_ids)
def test_alias_contains_no_part_of_identity(secret: bytes, user_id: str, name: str, email: str,
                                            inc: str) -> None:
    alias = reporter_alias(secret, user_id, inc)
    assert _ALIAS_RE.match(alias), alias
    alias_l = alias.lower()
    for ident in (user_id, name, email):
        for gram in _grams(ident):
            assert gram not in alias_l, f"alias {alias!r} contains {gram!r} from {ident!r}"

    user = UserRecord(user_id=user_id, display_name=name, created_at=NOW, alias_seed="seed")
    public = view_for(user, None, alias=alias)
    assert _dump(public) == {"alias": alias}
    _assert_no_leak(public, user_id, name)


# ---------------------------------------------------------------- hazard reports
@given(
    data=st.data(), reporter=account_ids, secret=secrets, desc=free_text,
    photo=st.one_of(st.none(), st.uuids().map(lambda u: f"photos/{u}.jpg")),
    client_id=st.one_of(st.none(), st.uuids().map(str)),
)
def test_report_public_view_hides_reporter(data: st.DataObject, reporter: str, secret: bytes,
                                           desc: str, photo: str | None,
                                           client_id: str | None) -> None:
    base = data.draw(reports())
    record = HazardReportRecord(
        **base.model_dump(), reporter_id=reporter,
        alias=reporter_alias(secret, reporter, base.incident_id), description=desc,
        photo_key=photo, updated_at=base.created_at, client_report_id=client_id,
    )
    viewer = data.draw(_non_owner(reporter))
    public = view_for(record, viewer)
    _assert_no_leak(public, reporter, photo or "", client_id or "")
    owner = view_for(record, reporter)
    assert owner.photo_key == photo and owner.client_report_id == client_id
    assert "reporter_id" not in _dump(owner)


# ---------------------------------------------------------------- check-ins (31.6, 31.8)
@given(
    data=st.data(), user_id=account_ids, inc=incident_ids, loc=st.one_of(st.none(), locations),
    visibility=st.sampled_from(["private", "incident-members", "public", "shared"]),
    expiry=st.one_of(st.none(), st.integers(-120, 120).map(lambda m: NOW + timedelta(minutes=m))),
    now=st.one_of(st.none(), st.just(NOW)),
)
def test_checkin_location_only_for_owner_or_opt_in(data: st.DataObject, user_id: str, inc: str,
                                                   loc: tuple[float, float] | None,
                                                   visibility: str, expiry: Any, now: Any) -> None:
    record = CheckInRecord(
        checkin_id=data.draw(_ids("checkin")), incident_id=inc, user_id=user_id,
        alias="Calm Fox 001", status=data.draw(st.sampled_from(["safe", "need_help", "evacuating", "sheltering"])),
        created_at=NOW, optional_location=loc, visibility=visibility, location_expires_at=expiry,
    )
    viewer = data.draw(_non_owner(user_id))
    public = view_for(record, viewer, now=now)
    _assert_no_leak(public, user_id)

    opted_in = (
        visibility != "private" and loc is not None
        and (expiry is None or (now is not None and now < expiry))
    )
    if opted_in:
        assert public.location == coarsen(*loc)
    else:
        assert public.location is None

    owner = view_for(record, user_id)
    assert owner.location == (coarsen(*loc) if loc else None)


# ---------------------------------------------------------------- messages
@given(
    data=st.data(), sender=account_ids, inc=incident_ids, loc=st.one_of(st.none(), locations),
    share=st.booleans(), hidden=st.booleans(), content=free_text,
)
def test_message_public_view_hides_sender_and_location(data: st.DataObject, sender: str, inc: str,
                                                       loc: tuple[float, float] | None,
                                                       share: bool, hidden: bool,
                                                       content: str) -> None:
    record = MessageRecord(
        message_id=data.draw(_ids("message")), incident_id=inc, sender_id=sender,
        alias="Keen Otter 042", message_type="status", content=content, created_at=NOW,
        approximate_location=loc, share_location=share, hidden=hidden,
    )
    public = view_for(record, data.draw(_non_owner(sender)))
    _assert_no_leak(public, sender)
    expected = coarsen(*loc) if (share and loc is not None) else None
    assert public.approximate_location == expected
    owner = view_for(record, sender)
    assert owner.share_location == share


# ---------------------------------------------------------------- votes, audit, events
# Non-text public values, so a leaked display name can't be confused with ordinary payload text.
_payload_values = st.one_of(st.integers(), st.booleans(), st.none())


@given(data=st.data(), user_id=account_ids, name=display_names, email=emails, inc=incident_ids)
def test_votes_audit_events_hide_identity(data: st.DataObject, user_id: str, name: str,
                                          email: str, inc: str) -> None:
    vote = ReportVote(report_id=data.draw(_ids("report")), user_id=user_id,
                      action=data.draw(st.sampled_from(["confirm", "dispute"])), created_at=NOW)
    _assert_no_leak(view_for(vote, data.draw(_non_owner(user_id))), user_id)

    audit = AuditLogRecord(audit_id=data.draw(_ids("audit")), day="2026-03-15", ts=NOW,
                           actor_id=user_id, actor_role="moderator", action="hide_message",
                           target_id=data.draw(_ids("message")))
    _assert_no_leak(view_for(audit, data.draw(_non_owner(user_id))), user_id)

    # Event payloads: private keys at arbitrary nesting depth must be scrubbed.
    private_leaf = st.fixed_dictionaries({
        "reporter_id": st.just(user_id), "sender_id": st.just(user_id), "user_id": st.just(user_id),
        "actor_id": st.just(user_id), "display_name": st.just(name), "email": st.just(email),
    })
    public_part = st.dictionaries(st.sampled_from(["category", "status", "count", "kind"]),
                                  _payload_values, max_size=3)
    node = st.builds(lambda a, b: {**a, **b}, public_part, private_leaf)
    payload = data.draw(st.recursive(
        node,
        lambda inner: st.one_of(
            st.dictionaries(st.sampled_from(["report", "message", "items", "meta"]), inner,
                            min_size=1, max_size=3),
            st.lists(inner, min_size=1, max_size=3).map(lambda xs: {"items": xs}),
        ),
        max_leaves=6,
    ))
    event = EventRecord(event_id=data.draw(_ids("event")), incident_id=inc, type="report.created",
                        payload=payload, created_at=NOW)
    _assert_no_leak(view_for(event, data.draw(_non_owner(user_id))), user_id, name, email)
