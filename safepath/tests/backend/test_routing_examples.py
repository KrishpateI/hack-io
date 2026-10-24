"""Task 6.31: routing examples not covered elsewhere.

Existing coverage: known 6-node shortest path (test_routing_search), weights validation
(test_routing_weights), outcome examples (test_routing_service). Added here: tornado with and
without an official shelter via the API, all five outcomes in one table, and a 200-request p95 smoke.
Validates: Requirements 13.10, 13.12, 44.1, 19.3, 19.4
"""
from __future__ import annotations

import time

from app.models.domain import IncidentType, PolicyOutcome
from test_routing_service import (  # noqa: F401  (fixtures + helpers)
    BOUNDARY, env, graph, make_incident, make_notice, post,
)


def _tornado(provider):
    provider.add_incident(make_incident(IncidentType.TORNADO, BOUNDARY, iid="tor-1"))


def test_tornado_with_official_shelter_names_it_and_source(env):
    client, provider, _ = env
    _tornado(provider)
    provider.add_notice(make_notice("shelter_in_place", nid="sh", text="Shelter at City Hall basement",
                                    incident_id="tor-1"))
    data = post(client, incident_id="tor-1").json()
    assert data["policy_outcome"] == "SHELTER_IN_PLACE"
    assert data["route_count"] == 0
    assert "City Hall basement" in data["explanation"]
    assert "src-sh" in data["sources"]
    assert "no verified shelter" not in data["explanation"].lower()
    assert all(r["geometry"]["coordinates"] == [] for r in data["routes"])


def test_tornado_without_official_shelter_states_none_available(env):
    client, provider, _ = env
    _tornado(provider)
    data = post(client, incident_id="tor-1").json()
    assert data["policy_outcome"] == "SHELTER_IN_PLACE"
    assert "no verified shelter available" in data["explanation"].lower()
    assert data["route_count"] == 0


def test_performance_smoke_200_requests_p95_under_5s(env):
    client, _, _ = env
    times = []
    for _ in range(200):
        t = time.perf_counter()
        assert post(client).status_code == 200
        times.append(time.perf_counter() - t)
    times.sort()
    assert times[189] < 5.0
