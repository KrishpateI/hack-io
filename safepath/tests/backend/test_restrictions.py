from datetime import datetime, timedelta, timezone

from app.disasters.base import IncidentContext
from app.models.domain import HazardReport, Incident, OfficialNotice, Restriction
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


def incident():
    return Incident(incident_id=INC, incident_type="flood", source="t", created_at=NOW,
                    updated_at=NOW, simulation_flag=True)


def ctx(**kw):
    kw.setdefault("origin", LatLon(latitude=41.8810, longitude=-87.650))
    return IncidentContext(incident=incident(), now=NOW, **kw)


def report(status, edges=("abf",), expires=NOW + timedelta(hours=1)):
    return HazardReport(report_id="r1", incident_id=INC, category="road_closure", latitude=41.881,
                        longitude=-87.65, affected_edge_ids=list(edges),
                        verification_status=status, created_at=NOW, expires_at=expires,
                        simulated=True, source_record_id="src-r")


def notice(ntype, **kw):
    base = dict(notice_id="n1", incident_id=INC, notice_type=ntype, text="t",
                source_organization="o", source_url="u", source_record_id="src-n",
                effective_at=NOW - timedelta(hours=1), published_at=NOW, retrieved_at=NOW,
                verification_status="Officially_Verified", simulation_flag=True)
    base.update(kw)
    return OfficialNotice(**base)


def overlay(**kw):
    base = dict(restriction_id="o1", incident_id=INC, kind="closure", source_record_id="src-o")
    base.update(kw)
    return Restriction(**base)


def resolve(c, mods=()):
    return RestrictionResolver().resolve(c, six_node_graph(), mods)


def test_only_officially_verified_reports_block():
    for s in ("Unverified", "Corroborated", "Disputed", "Resolved", "Expired"):
        assert not resolve(ctx(reports=[report(s)])).blocked_edges
    rs = resolve(ctx(reports=[report("Officially_Verified")]))
    assert set(rs.blocked_edges) == {"abf"}
    c = rs.causes("abf")[0]
    assert (c.kind, c.id, c.source_record_id, c.simulated) == ("closure", "r1", "src-r", True)
    assert rs.excluded_edges_count == 1


def test_expired_verified_report_ignored():
    r = report("Officially_Verified", expires=NOW - timedelta(seconds=1))
    assert not resolve(ctx(reports=[r])).blocked_edges


def test_polygon_uses_exact_intersects():
    rs = resolve(ctx(overlays=[overlay(kind="impassable_polygon", geometry=BOX)]))
    assert set(rs.blocked_edges) == {"bef", "ber"}


def test_overlay_edge_ids_inactive_and_other_incident():
    rs = resolve(ctx(overlays=[
        overlay(edge_ids=["abf", "nope"]),
        overlay(restriction_id="o2", edge_ids=["bcf"], active=False),
        overlay(restriction_id="o3", edge_ids=["cff"], incident_id="other"),
    ]))
    assert set(rs.blocked_edges) == {"abf"}


def test_road_direction_is_directed():
    rs = resolve(ctx(notices=[notice("road_direction_restriction", edge_ids=["abf"])]))
    assert set(rs.blocked_edges) == {"abf"}
    assert rs.causes("abf")[0].kind == "road_direction"
    assert not rs.is_blocked("abr")


def test_notice_eligibility():
    n_exp = notice("road_closure", edge_ids=["abf"], expires_at=NOW - timedelta(seconds=61))
    n_grace = notice("road_closure", notice_id="n2", edge_ids=["bcf"],
                     expires_at=NOW - timedelta(seconds=30))
    n_future = notice("road_closure", notice_id="n3", edge_ids=["def"],
                      effective_at=NOW + timedelta(minutes=1))
    n_cbd = notice("road_closure", notice_id="n4", edge_ids=["eff"], cbd_derived=True)
    rs = resolve(ctx(notices=[n_exp, n_grace, n_future, n_cbd]))
    assert set(rs.blocked_edges) == {"bcf"}


def test_perimeter_notice_polygon_and_multiple_causes():
    rs = resolve(ctx(
        notices=[notice("temporary_perimeter", geometry=BOX)],
        overlays=[overlay(edge_ids=["bef"])],
    ))
    assert {c.kind for c in rs.causes("bef")} == {"prohibited_area", "closure"}
    assert len(rs.causes("ber")) == 1


def test_module_hard_restrictions():
    class Mod:
        incident_type = "flood"

        def hard_restrictions(self, c, g):
            return [overlay(restriction_id="m1", kind="module", edge_ids=["cff"])]

    rs = resolve(ctx(), [Mod()])
    assert rs.causes("cff")[0].kind == "module"


def test_isolated_origin_and_destination():
    # origin at node a: block both out-edges of a; destination f: block both in-edges of f
    c = ctx(destination=LatLon(latitude=41.8800, longitude=-87.648),
            overlays=[overlay(edge_ids=["abf", "adf", "cff", "eff"])])
    rs = resolve(c)
    assert rs.isolated_origin and rs.isolated_destination and rs.isolated
    ok = resolve(ctx(destination=LatLon(latitude=41.8800, longitude=-87.648),
                     overlays=[overlay(edge_ids=["abf"])]))
    assert not ok.isolated


def test_cache_by_overlay_version_and_mask():
    g = six_node_graph()
    r = RestrictionResolver()
    c = ctx(overlays=[overlay(edge_ids=["abf"])])
    a = r.resolve(c, g, [], overlay_version=3)
    assert r.resolve(c, g, [], overlay_version=3) is a
    assert r.resolve(c, g, [], overlay_version=4) is not a
    m = a.mask(g)
    assert m.blocked_count == 1 and not m[g.edge_ids.index("abf")]

