"""Example tests for the earthquake, tornado and fire modules (Req 18, 19, 20)."""
from datetime import datetime, timedelta, timezone

from app.disasters import earthquake, fire, tornado
from app.disasters.base import IncidentContext, get_module, register_module
from app.disasters.earthquake import Building, EarthquakeModule
from app.disasters.fire import FireModule, FirePerimeter
from app.disasters.rules_engine import evaluate_pre_route
from app.disasters.tornado import Shelter, TornadoModule
from app.models.domain import (
    HazardReport,
    Incident,
    IncidentType,
    OfficialNotice,
    PolicyOutcome,
    Restriction,
)
from app.models.routing import LatLon
from app.routing.restrictions import RestrictionResolver

from test_routing_search import six_node_graph

NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
INC = "inc1"
# box straddling only edge "be" (lon -87.649, lat 41.8800..41.8810)
BOX = {
    "type": "Polygon",
    "coordinates": [[[-87.6491, 41.8803], [-87.6489, 41.8803], [-87.6489, 41.8807],
                     [-87.6491, 41.8807], [-87.6491, 41.8803]]],
}
# ~16 m west of edge "be"; 66 m from edge "ad"
BUILDING_BOX = {
    "type": "Polygon",
    "coordinates": [[[-87.64922, 41.88048], [-87.64918, 41.88048], [-87.64918, 41.88052],
                     [-87.64922, 41.88052], [-87.64922, 41.88048]]],
}
BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[-87.66, 41.87], [-87.64, 41.87], [-87.64, 41.89], [-87.66, 41.89],
                     [-87.66, 41.87]]],
}
INSIDE = LatLon(latitude=41.8810, longitude=-87.650)
OUTSIDE = LatLon(latitude=41.85, longitude=-87.60)


def incident(kind, status="active", boundary=None, sim=True):
    return Incident(incident_id=INC, incident_type=kind, source="t", created_at=NOW,
                    updated_at=NOW, simulation_flag=sim, status=status,
                    geographic_boundary=boundary)


def ctx(kind, origin=INSIDE, status="active", boundary=None, **kw):
    return IncidentContext(incident=incident(kind, status, boundary), now=NOW, origin=origin, **kw)


def notice(ntype, **kw):
    base = dict(notice_id="n1", incident_id=INC, notice_type=ntype, text="official text",
                source_organization="OEMC", source_url="u", source_record_id="src-n",
                effective_at=NOW - timedelta(hours=1), published_at=NOW, retrieved_at=NOW,
                verification_status="Officially_Verified", simulation_flag=True)
    base.update(kw)
    return OfficialNotice(**base)


def report(category, status, rid="r1", lat=41.8805, lon=-87.6492, **kw):
    base = dict(report_id=rid, incident_id=INC, category=category, latitude=lat, longitude=lon,
                verification_status=status, created_at=NOW, expires_at=NOW + timedelta(hours=1),
                simulated=True)
    base.update(kw)
    return HazardReport(**base)


def zone(kind="impassable_polygon", **kw):
    base = dict(restriction_id="z1", incident_id=INC, kind=kind, geometry=BOX,
                source_record_id="src-z", simulated=True)
    base.update(kw)
    return Restriction(**base)


def penalties(module, c, g):
    return {e.edge_id: module.edge_penalty(e, c, g).hazard_total for e in g.model.edges}


# ------------------------------------------------------------------------ registration
def test_modules_register_for_their_incident_types():
    for mod in (earthquake.MODULE, tornado.MODULE, fire.MODULE):
        register_module(mod, replace=True)
        assert get_module(mod.incident_type) is mod
    assert isinstance(get_module("fire"), FireModule)


# -------------------------------------------------------------------------- earthquake
def test_earthquake_hard_restrictions_and_resolver():
    g = six_node_graph()
    c = ctx(IncidentType.EARTHQUAKE,
            overlays=[zone()],
            notices=[notice("road_closure", edge_ids=["abf"])],
            reports=[report("bridge_closure", "Officially_Verified", affected_edge_ids=["bcf"]),
                     report("bridge_closure", "Unverified", rid="r2", affected_edge_ids=["def"])])
    m = EarthquakeModule()
    ids = {r.restriction_id for r in m.hard_restrictions(c, g)}
    assert ids == {"z1", "r1", "n1"}
    rs = RestrictionResolver().resolve(c, g, [m])
    assert set(rs.blocked_edges) == {"bef", "ber", "abf", "bcf"}
    assert not rs.is_blocked("def")


def test_earthquake_inactive_incident_has_no_restrictions():
    c = ctx(IncidentType.EARTHQUAKE, status="resolved", overlays=[zone()])
    assert list(EarthquakeModule().hard_restrictions(c, six_node_graph())) == []


def test_highrise_penalty_only_near_associated_building():
    g = six_node_graph()
    b = Building("b1", BUILDING_BOX, height_class="High-rise")
    m = EarthquakeModule([b], weights={"w_highrise_proximity": 60, "proximity_meters": 50})

    # no debris zone, notice or report: height class alone adds nothing (18.4)
    assert all(v == 0 for v in penalties(m, ctx(IncidentType.EARTHQUAKE), g).values())

    # tied to an active debris zone over the building
    zone_over_building = zone(geometry=BUILDING_BOX)
    p = penalties(m, ctx(IncidentType.EARTHQUAKE, overlays=[zone_over_building]), g)
    assert p["bef"] == 60 and p["ber"] == 60
    assert p["adf"] == 0 and p["cff"] == 0  # beyond 50 m

    # tied to a notice, or a report at the building
    n = notice("road_closure", geometry=BUILDING_BOX)
    assert penalties(m, ctx(IncidentType.EARTHQUAKE, notices=[n]), g)["bef"] == 60
    r = report("building_damage", "Unverified")
    assert penalties(m, ctx(IncidentType.EARTHQUAKE, reports=[r]), g)["bef"] == 60


def test_height_class_does_not_change_penalty_and_official_info_replaces_it():
    g = six_node_graph()
    c = ctx(IncidentType.EARTHQUAKE, reports=[report("building_damage", "Unverified")])
    results = []
    for hc in ("Low-rise", "High-rise", "Unknown"):
        m = EarthquakeModule([Building("b1", BUILDING_BOX, height_class=hc)])
        results.append(penalties(m, c, g))
    assert results[0] == results[1] == results[2]
    intact = Building("b1", BUILDING_BOX, height_class="High-rise",
                      structural_status="intact", structural_source_record_id="src-eng")
    assert all(v == 0 for v in penalties(EarthquakeModule([intact]), c, g).values())


def test_earthquake_advisories_simulated_and_sourced():
    c = ctx(IncidentType.EARTHQUAKE, overlays=[zone()],
            notices=[notice("road_closure", edge_ids=["abf"])])
    adv = EarthquakeModule().advisories(c)
    assert any(a.simulated and a.text.startswith("Simulated") for a in adv)
    assert any(a.source_record_id == "src-n" for a in adv)
    assert any(a.source_record_id == "READY-ILLINOIS" for a in adv)
    assert not any("route is safe" in a.text.lower() for a in adv)


# ----------------------------------------------------------------------------- tornado
def test_tornado_override_without_verified_shelter():
    m = TornadoModule()
    o = m.policy_override(ctx(IncidentType.TORNADO, boundary=BOUNDARY))
    assert o.outcome is PolicyOutcome.SHELTER_IN_PLACE
    assert "no verified shelter available" in o.text.lower()
    assert "ready.illinois.gov" in o.text and "retrieved" in o.text
    assert o.source_record_id == "READY-ILLINOIS"
    d = evaluate_pre_route(ctx(IncidentType.TORNADO, boundary=BOUNDARY), module=m)
    assert d.outcome is PolicyOutcome.SHELTER_IN_PLACE
    assert d.explanation.lower().count("no verified shelter available") == 1


def test_tornado_override_with_verified_shelter_and_notice():
    m = TornadoModule([Shelter("s1", "Library basement", 41.88, -87.65, "src-shelter"),
                       Shelter("s2", "Unsourced hall", 41.88, -87.65, None)])
    o = m.policy_override(ctx(IncidentType.TORNADO, boundary=BOUNDARY))
    assert "Library basement" in o.text and o.source_record_id == "src-shelter"
    assert "Unsourced" not in o.text
    assert "no verified shelter" not in o.text.lower()

    n = notice("incident_destination", text="Tornado shelter at City Hall")
    o2 = TornadoModule().policy_override(ctx(IncidentType.TORNADO, boundary=BOUNDARY, notices=[n]))
    assert o2.source_record_id == "src-n" and o2.notice_ids == ("n1",)


def test_tornado_override_requires_active_and_origin_inside():
    m = TornadoModule()
    assert m.policy_override(ctx(IncidentType.TORNADO, origin=OUTSIDE, boundary=BOUNDARY)) is None
    assert m.policy_override(ctx(IncidentType.TORNADO, status="expired", boundary=BOUNDARY)) is None


def test_tornado_route_filter_excludes_outdoor_edges_until_inactive():
    g = six_node_graph()
    m = TornadoModule()
    active = ctx(IncidentType.TORNADO, boundary=BOUNDARY)
    assert not any(m.route_filter(e, active) for e in g.model.edges)
    ended = ctx(IncidentType.TORNADO, status="expired", boundary=BOUNDARY)
    assert all(m.route_filter(e, ended) for e in g.model.edges)
    assert list(m.hard_restrictions(active, g)) == []
    assert m.edge_penalty(g.model.edges[0], active, g).total == 0


def test_tornado_advisories():
    m = TornadoModule()
    adv = m.advisories(ctx(IncidentType.TORNADO, boundary=BOUNDARY))
    assert any("no verified shelter available" in a.text.lower() for a in adv)
    assert m.advisories(ctx(IncidentType.TORNADO, status="expired")) == []


# -------------------------------------------------------------------------------- fire
def test_fire_hard_restrictions_verified_only():
    g = six_node_graph()
    verified = FirePerimeter("p1", BOX, "Officially_Verified", "src-p", simulated=True)
    m = FireModule([verified])
    c = ctx(IncidentType.FIRE,
            notices=[notice("road_closure", edge_ids=["abf"])],
            reports=[report("road_closure", "Officially_Verified", affected_edge_ids=["bcf"])])
    assert {r.restriction_id for r in m.hard_restrictions(c, g)} == {"p1", "n1", "r1"}
    rs = RestrictionResolver().resolve(c, g, [m])
    assert set(rs.blocked_edges) == {"bef", "ber", "abf", "bcf"}


def test_fire_unverified_perimeter_is_penalty_and_warning_only():
    g = six_node_graph()
    m = FireModule([FirePerimeter("p2", BOX, "Unverified", None, simulated=True)])
    c = ctx(IncidentType.FIRE)
    assert list(m.hard_restrictions(c, g)) == []
    assert not RestrictionResolver().resolve(c, g, [m]).blocked_edges
    p = penalties(m, c, g)
    assert p["bef"] > 0 and p["ber"] > 0 and p["abf"] == 0
    assert any(a.text.startswith("Warning: Simulated fire perimeter p2") for a in m.advisories(c))


def test_fire_smoke_report_penalty_within_radius():
    g = six_node_graph()
    m = FireModule(weights={"w_smoke_report": 90, "radius_meters": 100})
    for status in ("Unverified", "Corroborated", "Disputed"):
        c = ctx(IncidentType.FIRE, reports=[report("smoke", status, lat=41.8805, lon=-87.6490)])
        p = penalties(m, c, g)
        assert p["bef"] == 90
        assert all(v > 0 for v in (p["abf"], p["bcf"]))  # endpoints are within 56 m
    far = ctx(IncidentType.FIRE, reports=[report("smoke", "Unverified", lat=41.8900, lon=-87.6490)])
    assert all(v == 0 for v in penalties(m, far, g).values())
    # non-fire reports and officially verified reports add no smoke penalty
    other = ctx(IncidentType.FIRE, reports=[report("pothole", "Unverified", lat=41.8805, lon=-87.649)])
    assert all(v == 0 for v in penalties(m, other, g).values())


def test_fire_instruction_advisory_and_rules_engine_precedence():
    n = notice("follow_instructions", text="Avoid Wabash between Harrison and Roosevelt")
    c = ctx(IncidentType.FIRE, notices=[n])
    adv = FireModule().advisories(c)
    assert any(a.source_record_id == "src-n" and "Avoid Wabash" in a.text for a in adv)
    assert any(a.source_record_id == "READY-ILLINOIS" for a in adv)
    d = evaluate_pre_route(c, module=FireModule())
    assert d.outcome is PolicyOutcome.FOLLOW_OFFICIAL_INSTRUCTIONS


def test_fire_simulated_perimeter_labeled():
    p = FirePerimeter("sim1", BOX, "Officially_Verified", "src-sim", simulated=True)
    c = ctx(IncidentType.FIRE)
    r = list(FireModule([p]).hard_restrictions(c, six_node_graph()))[0]
    assert r.simulated is True
    assert any(a.simulated and a.text.startswith("Simulated fire perimeter")
               for a in FireModule([p]).advisories(c))
