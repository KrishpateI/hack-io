"""Reusable Repository contract suite (task 8.7; Req 30.9, 44.11).

Every test takes the ``harness`` fixture. To run the suite against another
implementation (e.g. DynamoDB Local in task 12.8) add a param to ``IMPLS`` or
override ``harness`` in a conftest; a harness exposes ``repo`` and
``break_store()`` (make subsequent calls fail as an outage).

Task 12.8: the ``dynamo_local`` param runs the same suite against ``DynamoRepository`` on a real
DynamoDB Local (``DYNAMODB_ENDPOINT``, see ``dynamo_local.py``); it is skipped when unset or
unreachable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import pytest
from dynamo_local import UNREACHABLE_ENDPOINT, LocalTables, configured_endpoint, make_local_client, probe
from fake_dynamodb import FakeDynamoDB, client_error
from hypothesis import given, settings, strategies as st

from app.adapters import Conflict, MemoryRepository, RepositoryUnavailable, SqliteRepository, TxOp
from app.adapters.aws.dynamo import DynamoRepository


@dataclass
class Harness:
    repo: object
    break_store: Callable[[], None]
    close: Callable[[], None] = lambda: None
    restore: Optional[Callable[[], None]] = None  # undo break_store, to inspect what persisted


def _memory() -> Harness:
    r = MemoryRepository()
    return Harness(r, lambda: setattr(r, "available", False))


def _sqlite() -> Harness:
    r = SqliteRepository()
    return Harness(r, r.close)


def _dynamo() -> Harness:
    fake = FakeDynamoDB(page_size=2)
    r = DynamoRepository(fake, table_prefix="contract-")

    def brk() -> None:
        for op in ("put_item", "get_item", "update_item", "transact_write_items", "query", "scan"):
            fake.faults[op] = client_error("ServiceUnavailable", op)

    return Harness(r, brk)


def _dynamo_local(tables: LocalTables) -> Harness:
    tables.clear()
    r = DynamoRepository(tables.client, table_prefix=tables.prefix)

    def brk() -> None:  # a real network failure: the client now targets a closed port
        r._c = make_local_client(UNREACHABLE_ENDPOINT, retries=1, timeout=0.5)

    def restore() -> None:
        r._c = tables.client

    return Harness(r, brk, restore=restore)


IMPLS = {"memory": _memory, "sqlite": _sqlite, "dynamo": _dynamo}
LOCAL = "dynamo_local"


@pytest.fixture(scope="module")
def local_tables():
    """Contract tables on DynamoDB Local, or ``None`` (with the skip reason) when unavailable."""
    url = configured_endpoint()
    reason = probe(url)
    if reason:
        yield None, reason
        return
    tables = LocalTables(url)
    try:
        yield tables, None
    finally:
        tables.drop()


@pytest.fixture(params=sorted(IMPLS) + [LOCAL])
def harness(request, local_tables):
    if request.param == LOCAL:
        tables, reason = local_tables
        if tables is None:
            pytest.skip(reason)
        h = _dynamo_local(tables)
    else:
        h = IMPLS[request.param]()
    yield h
    try:
        h.close()
    except Exception:
        pass


@pytest.fixture
def repo(harness):
    return harness.repo


def K(pk="a", sk="1"):
    return {"pk": pk, "sk": sk}


def test_put_overwrites_and_get_returns_copy(repo):
    repo.put("t", {**K(), "v": {"x": [1]}})
    repo.put("t", {**K(), "v": 2})
    got = repo.get("t", K())
    assert got["v"] == 2
    got["v"] = 99
    assert repo.get("t", K())["v"] == 2


def test_tables_are_isolated(repo):
    repo.put("t1", {**K(), "v": 1})
    assert repo.get("t2", K()) is None
    assert repo.query("t2", {"pk": "a"}) == []


def test_query_empty_and_since_past_end(repo):
    assert repo.query("t", {"pk": "a"}) == []
    repo.put("t", K(sk="05"))
    assert repo.query("t", {"pk": "a"}, since="05") == []


def test_query_limit_applies_after_since(repo):
    for i in range(6):
        repo.put("t", K(sk=f"0{i}"))
    assert [r["sk"] for r in repo.query("t", {"pk": "a"}, since="01", limit=2)] == ["02", "03"]


def test_update_cas_version_progression_and_key_immutable(repo):
    repo.put("t", {**K(), "n": 0})
    first = repo.update_cas("t", K(), 0, {"n": 1, "pk": "evil"})
    second = repo.update_cas("t", K(), 1, {"n": 2})
    assert (first["version"], second["version"]) == (1, 2)
    assert repo.get("t", K())["n"] == 2
    assert repo.get("t", K("evil")) is None


def test_transact_if_absent_conflict_rolls_back(repo):
    repo.put("t", K("a"))
    with pytest.raises(Conflict):
        repo.transact([
            TxOp("put", "t", item=K("fresh")),
            TxOp("put", "t", item=K("a"), if_absent=True),
        ])
    assert repo.get("t", K("fresh")) is None


def test_transact_delete_rolls_back_on_conflict(repo):
    repo.put("t", K("a"))
    with pytest.raises(Conflict):
        repo.transact([
            TxOp("delete", "t", key=K("a")),
            TxOp("update_cas", "t", key=K("missing"), expected_version=0, patch={}),
        ])
    assert repo.get("t", K("a")) is not None


def test_put_if_absent_conflicts_and_keeps_original(repo):
    repo.put("t", {**K(), "v": 1}, if_absent=True)
    with pytest.raises(Conflict):
        repo.put("t", {**K(), "v": 2}, if_absent=True)
    assert repo.get("t", K())["v"] == 1


def test_update_cas_stale_version_and_missing_item_conflict(repo):
    repo.put("t", {**K(), "n": 0})
    repo.update_cas("t", K(), 0, {"n": 1})
    with pytest.raises(Conflict):
        repo.update_cas("t", K(), 0, {"n": 99})  # stale writer loses
    assert repo.get("t", K())["n"] == 1
    with pytest.raises(Conflict):
        repo.update_cas("t", K("missing"), 0, {"n": 1})
    assert repo.get("t", K("missing")) is None


def test_transact_commits_all_ops_across_tables(repo):
    repo.put("t", {**K("a"), "n": 0})
    repo.put("t", K("gone"))
    repo.transact([
        TxOp("put", "events", item={"pk": "inc", "sk": "e1"}, if_absent=True),
        TxOp("update_cas", "t", key=K("a"), expected_version=0, patch={"n": 1}),
        TxOp("delete", "t", key=K("gone")),
    ])
    assert repo.get("events", {"pk": "inc", "sk": "e1"}) is not None
    assert repo.get("t", K("a"))["version"] == 1
    assert repo.get("t", K("gone")) is None


def test_transact_cas_conflict_rolls_back_other_tables(repo):
    repo.put("t", {**K("a"), "n": 0})
    with pytest.raises(Conflict):
        repo.transact([
            TxOp("put", "events", item={"pk": "inc", "sk": "e1"}),
            TxOp("update_cas", "t", key=K("a"), expected_version=5, patch={"n": 1}),
        ])
    assert repo.get("events", {"pk": "inc", "sk": "e1"}) is None
    assert repo.get("t", K("a"))["n"] == 0


@pytest.mark.parametrize("call", [
    lambda r: r.put("t", K("b")),
    lambda r: r.get("t", K("a")),
    lambda r: r.query("t", {"pk": "a"}),
    lambda r: r.update_cas("t", K("a"), 0, {"n": 1}),
], ids=["put", "get", "query", "update_cas"])
def test_outage_raises_unavailable_on_every_call(harness, call):
    harness.repo.put("t", K("a"))
    harness.break_store()
    with pytest.raises(RepositoryUnavailable):
        call(harness.repo)


def test_delete_where_no_match_and_missing_table(repo):
    assert repo.delete_where("nope", {"scenario": "s"}) == 0
    repo.put("t", {**K(), "scenario": "s1"})
    assert repo.delete_where("t", {"scenario": "zz"}) == 0
    assert repo.get("t", K()) is not None


def test_outage_raises_unavailable_on_transact_and_persists_nothing(harness):
    harness.repo.put("t", K("a"))
    harness.break_store()
    with pytest.raises(RepositoryUnavailable):
        harness.repo.transact([TxOp("put", "t", item=K("b"))])
    if isinstance(harness.repo, MemoryRepository):
        harness.repo.available = True
        assert harness.repo.get("t", K("b")) is None
    elif harness.restore is not None:
        harness.restore()
        assert harness.repo.get("t", K("b")) is None
        assert harness.repo.get("t", K("a")) is not None


@settings(max_examples=30, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 3), st.integers(0, 3)), max_size=8))
def test_property_put_get_query_agree(local_tables, entries):
    """put/get/query agree with a dict model for every implementation (incl. DynamoDB Local
    when available)."""
    tables, _ = local_tables
    factories = list(IMPLS.values())
    if tables is not None:
        factories.append(lambda: _dynamo_local(tables))
    for factory in factories:
        h = factory()
        try:
            model: dict = {}
            for p, s in entries:
                h.repo.put("t", {"pk": f"p{p}", "sk": f"s{s}", "v": p * 10 + s})
                model[(f"p{p}", f"s{s}")] = p * 10 + s
            for (pk, sk), v in model.items():
                assert h.repo.get("t", K(pk, sk))["v"] == v
            for pk in {k[0] for k in model}:
                assert [r["sk"] for r in h.repo.query("t", {"pk": pk})] == sorted(
                    k[1] for k in model if k[0] == pk)
        finally:
            h.close()
