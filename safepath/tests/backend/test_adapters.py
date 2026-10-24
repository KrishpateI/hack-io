"""Tests for adapter ports and local implementations (task 8.1; Req 29.7, 30.9)."""
from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from app.adapters import (
    AdapterNotAvailable, Conflict, Event, InProcessPublisher, LocalBlobStore, MemoryRepository,
    RealtimePublisher, Repository, RepositoryUnavailable, SqliteRepository, TxOp,
    make_auth_provider, make_blob_store, make_publisher, make_repository,
)


@pytest.fixture(params=["memory", "sqlite"])
def repo(request):
    r = MemoryRepository() if request.param == "memory" else SqliteRepository()
    yield r
    if isinstance(r, SqliteRepository):
        r.close()


def test_protocol_conformance(repo, tmp_path):
    assert isinstance(repo, Repository)
    assert isinstance(InProcessPublisher(), RealtimePublisher)


def test_put_get_if_absent(repo):
    repo.put("t", {"pk": "a", "sk": "1", "v": 1})
    assert repo.get("t", {"pk": "a", "sk": "1"})["v"] == 1
    assert repo.get("t", {"pk": "a", "sk": "2"}) is None
    with pytest.raises(Conflict):
        repo.put("t", {"pk": "a", "sk": "1", "v": 2}, if_absent=True)
    assert repo.get("t", {"pk": "a", "sk": "1"})["v"] == 1


def test_query_since_limit_order(repo):
    for i in range(5):
        repo.put("t", {"pk": "a", "sk": f"0{i}"})
    repo.put("t", {"pk": "b", "sk": "09"})
    assert [r["sk"] for r in repo.query("t", {"pk": "a"})] == ["00", "01", "02", "03", "04"]
    assert [r["sk"] for r in repo.query("t", {"pk": "a"}, since="02")] == ["03", "04"]
    assert len(repo.query("t", {"pk": "a"}, limit=2)) == 2


def test_update_cas(repo):
    repo.put("t", {"pk": "a", "sk": "1", "n": 0})
    out = repo.update_cas("t", {"pk": "a", "sk": "1"}, 0, {"n": 5})
    assert out["version"] == 1 and out["n"] == 5
    with pytest.raises(Conflict):
        repo.update_cas("t", {"pk": "a", "sk": "1"}, 0, {"n": 6})
    with pytest.raises(Conflict):
        repo.update_cas("t", {"pk": "zz", "sk": "1"}, 0, {"n": 6})
    assert repo.get("t", {"pk": "a", "sk": "1"})["n"] == 5


def test_transact_commits_all(repo):
    repo.put("t", {"pk": "a", "sk": "1", "version": 0})
    repo.transact([
        TxOp("put", "t", item={"pk": "b", "sk": "1"}),
        TxOp("update_cas", "t", key={"pk": "a", "sk": "1"}, expected_version=0, patch={"z": 1}),
        TxOp("put", "events", item={"pk": "e", "sk": "1"}),
    ])
    assert repo.get("t", {"pk": "a", "sk": "1"})["version"] == 1
    assert repo.get("events", {"pk": "e", "sk": "1"}) is not None


def test_transact_is_all_or_nothing_on_conflict(repo):
    repo.put("t", {"pk": "a", "sk": "1", "version": 0})
    with pytest.raises(Conflict):
        repo.transact([
            TxOp("put", "t", item={"pk": "new", "sk": "1"}),
            TxOp("update_cas", "t", key={"pk": "a", "sk": "1"}, expected_version=7, patch={}),
        ])
    assert repo.get("t", {"pk": "new", "sk": "1"}) is None
    assert repo.get("t", {"pk": "a", "sk": "1"})["version"] == 0


def test_delete_where_and_tx_delete(repo):
    for i in range(3):
        repo.put("t", {"pk": "a", "sk": str(i), "scenario": "s1" if i < 2 else "s2"})
    assert repo.delete_where("t", {"scenario": "s1"}) == 2
    assert len(repo.query("t", {"pk": "a"})) == 1
    repo.transact([TxOp("delete", "t", key={"pk": "a", "sk": "2"})])
    assert repo.query("t", {"pk": "a"}) == []


def test_memory_outage_raises_unavailable_and_persists_nothing():
    r = MemoryRepository()
    r.put("t", {"pk": "a", "sk": "1"})
    r.available = False
    with pytest.raises(RepositoryUnavailable):
        r.transact([TxOp("put", "t", item={"pk": "b", "sk": "1"})])
    r.available = True
    assert r.get("t", {"pk": "b", "sk": "1"}) is None


def test_sqlite_closed_db_raises_unavailable():
    r = SqliteRepository()
    r.close()
    with pytest.raises(RepositoryUnavailable):
        r.transact([TxOp("put", "t", item={"pk": "b", "sk": "1"})])


def test_sqlite_persists_to_file(tmp_path):
    path = str(tmp_path / "db.sqlite")
    a = SqliteRepository(path)
    a.put("t", {"pk": "a", "sk": "1", "x": 1})
    a.close()
    b = SqliteRepository(path)
    assert b.get("t", {"pk": "a", "sk": "1"})["x"] == 1
    b.close()


@settings(max_examples=40, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 4), st.booleans()), max_size=8), st.integers(0, 8))
def test_property_failed_transaction_leaves_no_trace(puts, fail_at):
    """A transaction containing a failing op never changes stored state."""
    r = MemoryRepository()
    r.put("t", {"pk": "seed", "sk": "0", "version": 0})
    ops = [TxOp("put", "t", item={"pk": f"p{n}", "sk": "0"}, if_absent=ia) for n, ia in puts]
    ops.insert(min(fail_at, len(ops)),
               TxOp("update_cas", "t", key={"pk": "seed", "sk": "0"}, expected_version=99, patch={}))
    with pytest.raises(Conflict):
        r.transact(ops)
    assert r.query("t", {"sk": "0"}) == [{"pk": "seed", "sk": "0", "version": 0}]


def test_publisher_fanout_and_never_raises():
    pub = InProcessPublisher()
    got = []
    unsub = pub.subscribe("i1", got.append)
    pub.subscribe("i1", lambda e: 1 / 0)  # failing handler must not propagate
    ev = Event(id="e1", incident_id="i1", type="x")
    pub.publish("i1", ev)
    pub.publish("other", ev)
    assert got == [ev]
    unsub()
    pub.publish("i1", ev)
    assert got == [ev]
    assert len(pub.published) == 3


def test_blob_signed_urls_expire_and_reject_tampering(tmp_path):
    now = [1000.0]
    store = LocalBlobStore(tmp_path, "secret", clock=lambda: now[0])
    url = store.presign_get("photos/a.jpg", ttl_seconds=60)
    q = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
    exp, sig = int(q["exp"]), q["sig"]
    assert store.verify("get", "photos/a.jpg", exp, sig)
    assert not store.verify("get", "photos/b.jpg", exp, sig)
    assert not store.verify("put", "photos/a.jpg", exp, sig)
    now[0] = 1061.0
    assert not store.verify("get", "photos/a.jpg", exp, sig)

    now[0] = 1000.0
    up = store.presign_put("photos/a.jpg", "image/jpeg", 1024)
    assert up.method == "PUT" and up.max_bytes == 1024
    uq = dict(p.split("=", 1) for p in up.url.split("?", 1)[1].split("&"))
    assert store.verify("put", "photos/a.jpg", int(uq["exp"]), uq["sig"], "image/jpeg|1024")
    assert not store.verify("put", "photos/a.jpg", int(uq["exp"]), uq["sig"], "image/jpeg|999999")


def test_blob_rejects_path_traversal_and_roundtrips(tmp_path):
    store = LocalBlobStore(tmp_path / "blobs", "s")
    for bad in ["../x", "a/../../x", "/abs", "", "a\\b"]:
        with pytest.raises(ValueError):
            store.presign_get(bad)
    store.write("a/b.bin", b"hi")
    assert store.read("a/b.bin") == b"hi"
    assert store.read("a/missing") is None


def test_factory_defaults_and_overrides(tmp_path):
    assert isinstance(make_repository({}), MemoryRepository)
    assert isinstance(make_repository({"SAFEPATH_REPOSITORY": "sqlite"}), SqliteRepository)
    assert isinstance(make_publisher({"SAFEPATH_PROFILE": "local"}), InProcessPublisher)
    assert isinstance(make_blob_store({"SAFEPATH_BLOB_DIR": str(tmp_path)}), LocalBlobStore)
    # Per-port override lets the aws profile keep local ports.
    env = {"SAFEPATH_PROFILE": "aws", "SAFEPATH_REPOSITORY": "memory"}
    assert isinstance(make_repository(env), MemoryRepository)


def test_factory_errors():
    with pytest.raises(AdapterNotAvailable, match="12.2"):
        make_repository({"SAFEPATH_PROFILE": "aws"})
    with pytest.raises(AdapterNotAvailable):
        make_publisher({"SAFEPATH_PROFILE": "aws"})
    assert make_auth_provider({}).issue_guest().principal.is_guest
    with pytest.raises(AdapterNotAvailable):
        make_auth_provider({"SAFEPATH_PROFILE": "aws"})
    with pytest.raises(ValueError):
        make_repository({"SAFEPATH_REPOSITORY": "oracle"})
    with pytest.raises(ValueError):
        make_repository({"SAFEPATH_PROFILE": "mars"})
