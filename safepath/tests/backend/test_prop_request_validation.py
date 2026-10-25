"""Property 23: invalid requests are rejected with the specified status and field errors.

For any request outside the schema (route ``alternatives`` not an integer in 1..3, unsupported
``travel_mode``/``mobility_profile``; report with missing or unknown category, missing position or
description > 1000 characters; message with empty or > 1000-char content or unknown type; check-in
with unknown status; unknown incident; invalid routing weights file) the API returns 400 (404 for
an unknown incident, 503 for the invalid weights file), identifies each invalid field, and persists
and publishes nothing.

Runs against ``create_app(Settings(profile="local"))`` through FastAPI's TestClient.

**Validates: Requirements 13.11, 13.12, 23.7, 26.7, 26.8, 27.7, 28.4, 43.5, 43.6, 43.7**
"""
# Feature: safepath-evacuation-navigation, Property 23: Invalid requests are rejected with the specified status and field errors
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from app.adapters import InProcessPublisher, MemoryRepository
from app.api.ratelimit import InMemoryRateLimiter
from app.auth.deps import install_auth
from app.auth.provider import DemoTokenAuthProvider
from app.communication.checkins import STATUS_LABELS
from app.communication.messaging import MESSAGE_TYPES
from app.config import Settings
from app.main import create_app
from app.models.domain import MobilityProfile, TravelMode
from app.routing.cost import DEFAULT_WEIGHTS_PATH, _NUMERIC_SECTIONS, load_weights_state
from app.routing.validation import SUPPORTED_MOBILITY_PROFILES, SUPPORTED_TRAVEL_MODES
from app.services.overlays import RepositoryContextProvider
from app.services.reports import CATEGORIES
from strategies import coordinates

INC = "inc_p23"
SUBJECT = "usr_p23"


# ------------------------------------------------------------------------------- app harness
class _Env:
    """One app for the module; repository/publisher snapshots prove nothing is written."""

    def __init__(self) -> None:
        self.app = create_app(Settings(profile="local", demo_mode=False))
        self.repo = MemoryRepository()
        self.auth = DemoTokenAuthProvider("p23-secret")
        install_auth(self.app, auth=self.auth, repository=self.repo)
        self.publisher = InProcessPublisher()
        st_ = self.app.state
        st_.event_bus = None  # rebuilt lazily over this repository and publisher
        st_.publisher = self.publisher
        st_.routing_context_provider = RepositoryContextProvider(self.repo)
        st_.routing_service = st_.report_service = st_.messaging_service = None
        st_.checkin_service = None
        self.good_weights = st_.routing_weights
        self.repo.put("incidents", {"pk": "all", "sk": INC, "incident_id": INC,
                                    "incident_type": "flood", "source": "test",
                                    "simulation_flag": True})
        self.client = TestClient(self.app, raise_server_exceptions=False)
        tok, _ = self.auth.issue_token(SUBJECT)
        self.headers = {"Authorization": f"Bearer {tok}"}

    def snapshot(self) -> tuple[Any, int]:
        return copy.deepcopy(self.repo._data), len(self.publisher.published)

    def post(self, path: str, body: Any) -> Any:
        # Fresh limiter: only validation, never a 429, decides the outcome.
        self.app.state.rate_limiter = InMemoryRateLimiter()
        before = self.snapshot()
        res = self.client.post(path, json=body, headers=self.headers)
        assert self.snapshot() == before, f"{path} persisted or published on a rejected request"
        return res


_ENV: _Env | None = None


def env() -> _Env:
    global _ENV
    if _ENV is None:
        _ENV = _Env()
    return _ENV


def _fields(res: Any) -> set[str]:
    return {f["field"] for f in res.json()["error"]["fields"]}


# ------------------------------------------------------------------------------- generators
_inside = coordinates().filter(lambda c: c.status == "inside").map(
    lambda c: {"latitude": c.lat, "longitude": c.lon})

# Arbitrary JSON scalars/containers that are not one of ``allowed``.
_json_scalar = st.one_of(st.none(), st.booleans(), st.integers(-10**6, 10**6),
                         st.floats(allow_nan=False, allow_infinity=False), st.text(max_size=20))


def _not_in(allowed: tuple[str, ...]) -> st.SearchStrategy:
    return st.one_of(_json_scalar, st.lists(st.text(max_size=5), max_size=2)).filter(
        lambda v: not (isinstance(v, str) and v in allowed))


_long_text = st.builds(lambda c, n: c * n,
                       st.characters(blacklist_categories=("Cs", "Cc"), blacklist_characters="<>&"),
                       st.integers(1001, 1500))
_unknown_incident = st.text("abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=32).filter(
    lambda s: s != INC)

_MISSING = object()


def _apply(base: dict, mutations: dict) -> dict:
    out = dict(base)
    for k, v in mutations.items():
        if v is _MISSING:
            out.pop(k, None)
        else:
            out[k] = v
    return out


# --- routes (13.11, 43.5, 43.7) -----------------------------------------------------------
_bad_alternatives = st.one_of(
    st.integers().filter(lambda a: not 1 <= a <= 3),
    st.floats(allow_nan=False, allow_infinity=False).filter(lambda f: not f.is_integer()),
    st.text(max_size=5), st.booleans(), st.none(), st.lists(st.integers(1, 3), max_size=2),
)


@st.composite
def _route_cases(draw) -> tuple[dict, set[str]]:
    base = {"incident_id": INC, "origin": draw(_inside), "destination": draw(_inside),
            "travel_mode": draw(st.sampled_from([m.value for m in TravelMode])),
            "mobility_profile": draw(st.sampled_from([m.value for m in MobilityProfile])),
            "alternatives": draw(st.integers(1, 3))}
    options = {
        "alternatives": _bad_alternatives,
        "travel_mode": _not_in(SUPPORTED_TRAVEL_MODES),
        "mobility_profile": _not_in(SUPPORTED_MOBILITY_PROFILES),
    }
    chosen = draw(st.lists(st.sampled_from(sorted(options)), min_size=1, unique=True))
    return _apply(base, {k: draw(options[k]) for k in chosen}), set(chosen)


@given(_route_cases())
def test_invalid_route_requests_are_400_with_each_field(case):
    body, expected = case
    res = env().post("/api/routes/calculate", body)
    assert res.status_code == 400, res.text
    assert expected <= _fields(res)
    problems = {f["field"]: f["problem"] for f in res.json()["error"]["fields"]}
    if "alternatives" in expected:
        assert "1 to 3" in problems["alternatives"]  # 13.11: states the permitted range
    for field, supported in (("travel_mode", SUPPORTED_TRAVEL_MODES),
                             ("mobility_profile", SUPPORTED_MOBILITY_PROFILES)):
        if field in expected:  # 43.7: lists the supported values
            assert all(v in problems[field] for v in supported)


# --- reports (23.7) -----------------------------------------------------------------------
@st.composite
def _report_cases(draw) -> tuple[dict, set[str]]:
    base = {"incident_id": INC, "category": draw(st.sampled_from(CATEGORIES)),
            "description": draw(st.text(max_size=40)), **draw(_inside)}
    muts: dict[str, Any] = {}
    expected: set[str] = set()
    kinds = draw(st.lists(st.sampled_from(["category", "position", "description"]),
                          min_size=1, unique=True))
    if "category" in kinds:
        muts["category"] = draw(st.one_of(st.just(_MISSING), _not_in(CATEGORIES)))
        expected.add("category")
    if "position" in kinds:
        drop = draw(st.sampled_from([("latitude",), ("longitude",), ("latitude", "longitude")]))
        muts.update({k: _MISSING for k in drop})
        expected |= set(drop)
    if "description" in kinds:
        muts["description"] = draw(_long_text)
        expected.add("description")
    return _apply(base, muts), expected


@given(_report_cases())
def test_invalid_reports_are_400_with_each_field(case):
    body, expected = case
    res = env().post("/api/reports", body)
    assert res.status_code == 400, res.text
    assert expected <= _fields(res)


# --- messages (26.7) ----------------------------------------------------------------------
@st.composite
def _message_cases(draw) -> tuple[dict, set[str]]:
    base = {"incident_id": INC, "message_type": draw(st.sampled_from(MESSAGE_TYPES)),
            "content": draw(st.text(st.characters(blacklist_categories=("Cs", "Cc", "Z"),
                                                  blacklist_characters="<>&"),
                                    min_size=1, max_size=40))}
    options = {
        "content": st.one_of(st.just(_MISSING), st.sampled_from(["", " ", "\t\n  "]), _long_text),
        "message_type": st.one_of(st.just(_MISSING), _not_in(MESSAGE_TYPES)),
    }
    chosen = draw(st.lists(st.sampled_from(sorted(options)), min_size=1, unique=True))
    return _apply(base, {k: draw(options[k]) for k in chosen}), set(chosen)


@given(_message_cases())
def test_invalid_messages_are_400_with_each_field(case):
    body, expected = case
    res = env().post("/api/messages", body)
    assert res.status_code == 400, res.text
    assert expected <= _fields(res)


# --- check-ins (27.7) ---------------------------------------------------------------------
_STATUS_VALUES = tuple(s.value for s in STATUS_LABELS) + tuple(STATUS_LABELS.values())


@given(status=st.one_of(st.just(_MISSING), _not_in(_STATUS_VALUES)))
def test_invalid_checkin_status_is_400(status):
    res = env().post("/api/checkins", _apply({"incident_id": INC, "status": "safe"},
                                             {"status": status}))
    assert res.status_code == 400, res.text
    assert "status" in _fields(res)


# --- unknown incident -> 404 (26.8, 27.7, 43.6) --------------------------------------------
@st.composite
def _unknown_incident_cases(draw) -> tuple[str, dict]:
    iid = draw(_unknown_incident)
    kind = draw(st.sampled_from(["route", "report", "message", "checkin"]))
    if kind == "route":
        return "/api/routes/calculate", {
            "incident_id": iid, "origin": draw(_inside), "destination": draw(_inside),
            "travel_mode": "walking", "mobility_profile": "standard"}
    if kind == "report":
        return "/api/reports", {"incident_id": iid, "category": draw(st.sampled_from(CATEGORIES)),
                                **draw(_inside)}
    if kind == "message":
        return "/api/messages", {"incident_id": iid,
                                 "message_type": draw(st.sampled_from(MESSAGE_TYPES)),
                                 "content": "Bridge is open"}
    return "/api/checkins", {"incident_id": iid, "status": draw(st.sampled_from(_STATUS_VALUES))}


@given(_unknown_incident_cases())
def test_unknown_incident_is_404(case):
    path, body = case
    res = env().post(path, body)
    assert res.status_code == 404, res.text
    err = res.json()["error"]
    assert err["code"] == "not_found"
    assert "incident" in json.dumps(err).lower()  # identifies the missing resource


# --- invalid weights file -> 503 (13.12) ---------------------------------------------------
_DEFAULT_WEIGHTS = yaml.safe_load(DEFAULT_WEIGHTS_PATH.read_text(encoding="utf-8"))
_WEIGHT_FIELDS = [("d_min_meters", None)] + [
    (sec, key) for sec in _NUMERIC_SECTIONS for key in sorted(_DEFAULT_WEIGHTS[sec])]
_bad_weight = st.one_of(
    st.floats(max_value=-1e-9, allow_nan=False, allow_infinity=False),
    st.integers(max_value=-1),
    st.text(max_size=8),  # safe_dump quotes numeric-looking strings, so they stay strings
    st.none(), st.booleans(), st.lists(st.integers(0, 3), max_size=2),
)


@given(target=st.sampled_from(_WEIGHT_FIELDS), value=_bad_weight, req=_route_cases())
def test_invalid_weights_file_is_503_naming_the_field(target, value, req):
    section, key = target
    data = copy.deepcopy(_DEFAULT_WEIGHTS)
    if key is None:
        data[section] = value
        field = section
    else:
        data[section][key] = value
        field = f"{section}.{key}"
    body = {**req[0], "travel_mode": "walking", "mobility_profile": "standard", "alternatives": 1}
    e = env()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "routing_weights.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        e.app.state.routing_weights = load_weights_state(path)
        try:
            res = e.post("/api/routes/calculate", body)
        finally:
            e.app.state.routing_weights = e.good_weights
    assert res.status_code == 503, res.text
    msg = res.json()["error"]["message"]
    assert "routing weights invalid" in msg and f"'{field}'" in msg
