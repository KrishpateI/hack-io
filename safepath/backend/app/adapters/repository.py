"""Local Repository implementations: in-memory (default) and SQLite.

Tables are schemaless. A table's primary key is a tuple of attribute names
(default ``("pk", "sk")``; a missing ``sk`` counts as ``""``). ``query`` matches
``key_cond`` by equality, sorts by the last key attribute and, when ``since`` is
given, returns only items whose last key attribute is strictly greater.
Items updated through ``update_cas`` carry an integer ``version`` (absent == 0).
"""
from __future__ import annotations

import copy
import json
import sqlite3
import threading
from typing import Any, Mapping

from app.adapters.ports import Conflict, RepositoryUnavailable, TxOp

DEFAULT_KEY: tuple[str, ...] = ("pk", "sk")


def _key_attrs(schema: Mapping[str, tuple[str, ...]], table: str) -> tuple[str, ...]:
    return tuple(schema.get(table, DEFAULT_KEY))


def _key_of(attrs: tuple[str, ...], source: Mapping[str, Any]) -> tuple:
    if attrs[0] not in source:
        raise ValueError(f"missing partition key attribute {attrs[0]!r}")
    return tuple(source.get(a, "") for a in attrs)


def _matches(item: Mapping[str, Any], cond: Mapping[str, Any]) -> bool:
    return all(k in item and item[k] == v for k, v in cond.items())


def _select(items: list[dict], attrs: tuple[str, ...], key_cond: Mapping[str, Any],
            since: str | None, limit: int) -> list[dict]:
    sort_attr = attrs[-1]
    out = [i for i in items if _matches(i, key_cond)]
    if since is not None:
        out = [i for i in out if str(i.get(sort_attr, "")) > since]
    out.sort(key=lambda i: str(i.get(sort_attr, "")))
    return [copy.deepcopy(i) for i in out[: max(limit, 0)]]


class _Core:
    """Shared dict-of-dicts engine; both repositories delegate here via a load/save snapshot."""

    def __init__(self, schema: Mapping[str, tuple[str, ...]] | None) -> None:
        self.schema = dict(schema or {})

    # operations act on ``data``: {table: {key_tuple: item}}
    def op_put(self, data: dict, table: str, item: Mapping[str, Any], if_absent: bool) -> None:
        key = _key_of(_key_attrs(self.schema, table), item)
        rows = data.setdefault(table, {})
        if if_absent and key in rows:
            raise Conflict(f"{table}{key} already exists")
        rows[key] = copy.deepcopy(dict(item))

    def op_cas(self, data: dict, table: str, key: Mapping[str, Any], expected: int,
               patch: Mapping[str, Any]) -> dict:
        k = _key_of(_key_attrs(self.schema, table), key)
        row = data.get(table, {}).get(k)
        if row is None:
            raise Conflict(f"{table}{k} does not exist")
        current = int(row.get("version", 0))
        if current != expected:
            raise Conflict(f"{table}{k} version {current} != expected {expected}")
        attrs = _key_attrs(self.schema, table)
        new = {**row, **copy.deepcopy(dict(patch))}
        for a in attrs:  # key attributes are immutable
            if a in row:
                new[a] = row[a]
        new["version"] = current + 1
        data[table][k] = new
        return copy.deepcopy(new)

    def op_delete(self, data: dict, table: str, key: Mapping[str, Any]) -> None:
        k = _key_of(_key_attrs(self.schema, table), key)
        data.get(table, {}).pop(k, None)

    def apply(self, data: dict, op: TxOp) -> None:
        if op.kind == "put":
            self.op_put(data, op.table, op.item or {}, op.if_absent)
        elif op.kind == "update_cas":
            self.op_cas(data, op.table, op.key or {}, int(op.expected_version or 0), op.patch or {})
        elif op.kind == "delete":
            self.op_delete(data, op.table, op.key or {})
        else:  # pragma: no cover
            raise ValueError(f"unknown op kind {op.kind!r}")


class MemoryRepository:
    """Thread-safe in-memory repository. ``available = False`` simulates an outage."""

    def __init__(self, key_schema: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self._core = _Core(key_schema)
        self._data: dict[str, dict[tuple, dict]] = {}
        self._lock = threading.RLock()
        self.available = True

    def _check(self) -> None:
        if not self.available:
            raise RepositoryUnavailable("memory repository marked unavailable")

    def put(self, table: str, item: dict, *, if_absent: bool = False) -> None:
        with self._lock:
            self._check()
            self._core.op_put(self._data, table, item, if_absent)

    def get(self, table: str, key: dict) -> dict | None:
        with self._lock:
            self._check()
            row = self._data.get(table, {}).get(_key_of(_key_attrs(self._core.schema, table), key))
            return copy.deepcopy(row) if row is not None else None

    def query(self, table: str, key_cond: dict, *, index: str | None = None,
              since: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            self._check()
            attrs = _key_attrs(self._core.schema, table)
            return _select(list(self._data.get(table, {}).values()), attrs, key_cond, since, limit)

    def update_cas(self, table: str, key: dict, expected_version: int, patch: dict) -> dict:
        with self._lock:
            self._check()
            return self._core.op_cas(self._data, table, key, expected_version, patch)

    def transact(self, ops: list[TxOp]) -> None:
        with self._lock:
            self._check()
            staged = copy.deepcopy(self._data)
            for op in ops:
                self._core.apply(staged, op)  # Conflict/ValueError leave live data untouched
            self._data = staged

    def delete_where(self, table: str, predicate: Mapping) -> int:
        with self._lock:
            self._check()
            rows = self._data.get(table, {})
            doomed = [k for k, v in rows.items() if _matches(v, predicate)]
            for k in doomed:
                del rows[k]
            return len(doomed)


class SqliteRepository:
    """SQLite-backed repository (``:memory:`` or a file path). Items are stored as JSON."""

    def __init__(self, path: str = ":memory:",
                 key_schema: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self._core = _Core(key_schema)
        self._lock = threading.RLock()
        try:
            self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS items ("
                "tbl TEXT NOT NULL, k TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (tbl, k))"
            )
        except sqlite3.Error as exc:
            raise RepositoryUnavailable(str(exc)) from exc

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _k(key: tuple) -> str:
        return json.dumps(list(key), sort_keys=True, default=str)

    def _load_table(self, table: str) -> dict[tuple, dict]:
        rows = self._db.execute("SELECT k, body FROM items WHERE tbl = ?", (table,)).fetchall()
        return {tuple(json.loads(k)): json.loads(b) for k, b in rows}

    def _write_back(self, table: str, before: dict[tuple, dict], after: dict[tuple, dict]) -> None:
        for k in before.keys() - after.keys():
            self._db.execute("DELETE FROM items WHERE tbl = ? AND k = ?", (table, self._k(k)))
        for k, v in after.items():
            if before.get(k) != v:
                self._db.execute(
                    "INSERT OR REPLACE INTO items (tbl, k, body) VALUES (?, ?, ?)",
                    (table, self._k(k), json.dumps(v, default=str)),
                )

    def _run(self, tables: list[str], fn):
        """Run ``fn(data)`` on a snapshot of ``tables`` inside one DB transaction."""
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise RepositoryUnavailable(str(exc)) from exc
            try:
                before = {t: self._load_table(t) for t in tables}
                data = copy.deepcopy(before)
                result = fn(data)
                for t in tables:
                    self._write_back(t, before[t], data.get(t, {}))
                self._db.execute("COMMIT")
                return result
            except sqlite3.Error as exc:
                self._rollback()
                raise RepositoryUnavailable(str(exc)) from exc
            except BaseException:
                self._rollback()
                raise

    def _rollback(self) -> None:
        try:
            self._db.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def put(self, table: str, item: dict, *, if_absent: bool = False) -> None:
        self._run([table], lambda d: self._core.op_put(d, table, item, if_absent))

    def get(self, table: str, key: dict) -> dict | None:
        k = _key_of(_key_attrs(self._core.schema, table), key)
        with self._lock:
            try:
                row = self._db.execute(
                    "SELECT body FROM items WHERE tbl = ? AND k = ?", (table, self._k(k))
                ).fetchone()
            except sqlite3.Error as exc:
                raise RepositoryUnavailable(str(exc)) from exc
        return json.loads(row[0]) if row else None

    def query(self, table: str, key_cond: dict, *, index: str | None = None,
              since: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            try:
                items = list(self._load_table(table).values())
            except sqlite3.Error as exc:
                raise RepositoryUnavailable(str(exc)) from exc
        return _select(items, _key_attrs(self._core.schema, table), key_cond, since, limit)

    def update_cas(self, table: str, key: dict, expected_version: int, patch: dict) -> dict:
        return self._run([table], lambda d: self._core.op_cas(d, table, key, expected_version, patch))

    def transact(self, ops: list[TxOp]) -> None:
        tables = sorted({op.table for op in ops})

        def work(data: dict) -> None:
            for op in ops:
                self._core.apply(data, op)

        self._run(tables, work)

    def delete_where(self, table: str, predicate: Mapping) -> int:
        def work(data: dict) -> int:
            rows = data.setdefault(table, {})
            doomed = [k for k, v in rows.items() if _matches(v, predicate)]
            for k in doomed:
                del rows[k]
            return len(doomed)

        return self._run([table], work)
