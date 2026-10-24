"""In-memory fake of the boto3 DynamoDB low-level client (no network, no AWS).

Implements the subset DynamoRepository uses with real DynamoDB semantics for conditions
(``attribute_exists``, ``attribute_not_exists``, ``=``, ``<``, ``>``, AND/OR, parentheses),
``SET`` updates, ``Limit``/``LastEvaluatedKey`` paging (page size is applied *before* filters),
all-or-nothing ``transact_write_items`` with ``CancellationReasons``, and fault injection.
"""
from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

_DES = TypeDeserializer()
_TOKEN = re.compile(r"\s*(attribute_not_exists|attribute_exists|AND|OR|\(|\)|=|<|>|#\w+|:\w+)")


def client_error(code: str, op: str, **extra: Any) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}, **extra}, op)


def _tokens(expr: str) -> list[str]:
    pos, out = 0, []
    expr = expr.strip()
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m:
            raise ValueError(f"fake dynamodb cannot parse {expr!r} at {pos}")
        out.append(m.group(1))
        pos = m.end()
        while pos < len(expr) and expr[pos].isspace():
            pos += 1
    return out


class _Eval:
    def __init__(self, expr: str, item: dict, names: dict, values: dict) -> None:
        self.t, self.i, self.item, self.names, self.values = _tokens(expr), 0, item, names, values

    def _peek(self) -> str | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def _next(self) -> str:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def run(self) -> bool:
        res = self._or()
        assert self.i == len(self.t), "trailing tokens"
        return res

    def _or(self) -> bool:
        res = self._and()
        while self._peek() == "OR":
            self._next()
            rhs = self._and()
            res = res or rhs
        return res

    def _and(self) -> bool:
        res = self._term()
        while self._peek() == "AND":
            self._next()
            rhs = self._term()
            res = res and rhs
        return res

    def _attr(self, tok: str) -> str:
        return self.names[tok]

    def _operand(self, tok: str) -> Any:
        if tok.startswith("#"):
            av = self.item.get(self._attr(tok))
            return None if av is None else _DES.deserialize(av)
        return _DES.deserialize(self.values[tok])

    def _term(self) -> bool:
        tok = self._next()
        if tok == "(":
            res = self._or()
            assert self._next() == ")"
            return res
        if tok in ("attribute_exists", "attribute_not_exists"):
            assert self._next() == "("
            name = self._attr(self._next())
            assert self._next() == ")"
            return (name in self.item) == (tok == "attribute_exists")
        left = self._operand(tok)
        op = self._next()
        right = self._operand(self._next())
        if left is None or right is None:
            return False
        if isinstance(left, (str, bytes)) != isinstance(right, (str, bytes)):
            return False
        return {"=": left == right, "<": left < right, ">": left > right}[op]


def evaluate(expr: str | None, item: dict, names: dict | None, values: dict | None) -> bool:
    if not expr:
        return True
    return _Eval(expr, item, names or {}, values or {}).run()


class FakeDynamoDB:
    """``tables`` maps full table name -> {(pk, sk): item(AttributeValue dict)}."""

    def __init__(self, page_size: int | None = None) -> None:
        self.tables: dict[str, dict[tuple[str, str], dict]] = {}
        self.page_size = page_size  # simulates the 1 MB page cap
        self.faults: dict[str, ClientError] = {}  # op -> error raised once
        self.calls: list[tuple[str, dict]] = []
        self.unprocessed_once = False

    # ------------------------------------------------------------------ plumbing
    def _enter(self, op: str, kwargs: dict) -> None:
        self.calls.append((op, copy.deepcopy(kwargs)))
        err = self.faults.pop(op, None)
        if err is not None:
            raise err

    @staticmethod
    def _key(item: dict) -> tuple[str, str]:
        return item["pk"]["S"], item["sk"]["S"]

    def _table(self, name: str) -> dict:
        return self.tables.setdefault(name, {})

    # ------------------------------------------------------------------ operations
    def put_item(self, **kw: Any) -> dict:
        self._enter("put_item", kw)
        self._put(kw, self._table(kw["TableName"]))
        return {}

    def _put(self, kw: dict, table: dict) -> None:
        item = kw["Item"]
        assert set(item["pk"]) == {"S"} and set(item["sk"]) == {"S"} and item["sk"]["S"] != ""
        existing = table.get(self._key(item), {})
        if not evaluate(kw.get("ConditionExpression"), existing, kw.get("ExpressionAttributeNames"),
                        kw.get("ExpressionAttributeValues")):
            raise client_error("ConditionalCheckFailedException", "PutItem")
        table[self._key(item)] = copy.deepcopy(item)

    def get_item(self, **kw: Any) -> dict:
        self._enter("get_item", kw)
        item = self._table(kw["TableName"]).get(self._key(kw["Key"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def _update(self, kw: dict, table: dict) -> dict:
        key = self._key(kw["Key"])
        existing = table.get(key)
        names, values = kw.get("ExpressionAttributeNames", {}), kw.get("ExpressionAttributeValues", {})
        if not evaluate(kw.get("ConditionExpression"), existing or {}, names, values):
            raise client_error("ConditionalCheckFailedException", "UpdateItem")
        item = copy.deepcopy(existing) if existing else dict(copy.deepcopy(kw["Key"]))
        assert kw["UpdateExpression"].startswith("SET ")
        for clause in kw["UpdateExpression"][4:].split(","):
            lhs, rhs = [s.strip() for s in clause.split("=")]
            item[names[lhs]] = copy.deepcopy(values[rhs])
        table[key] = item
        return item

    def update_item(self, **kw: Any) -> dict:
        self._enter("update_item", kw)
        item = self._update(kw, self._table(kw["TableName"]))
        return {"Attributes": copy.deepcopy(item)} if kw.get("ReturnValues") == "ALL_NEW" else {}

    def transact_write_items(self, **kw: Any) -> dict:
        self._enter("transact_write_items", kw)
        staged = {n: dict(t) for n, t in self.tables.items()}
        reasons, failed = [], False
        for ti in kw["TransactItems"]:
            (kind, req), = ti.items()
            table = staged.setdefault(req["TableName"], {})
            snapshot = {n: dict(t) for n, t in staged.items()}  # conditions see pre-state per item
            try:
                if kind == "Put":
                    self._put(req, table)
                elif kind == "Update":
                    self._update(req, table)
                elif kind == "Delete":
                    table.pop(self._key(req["Key"]), None)
                reasons.append({"Code": "None"})
            except ClientError:
                staged = snapshot
                failed = True
                reasons.append({"Code": "ConditionalCheckFailed", "Message": "The conditional request failed"})
        if failed:
            raise client_error("TransactionCanceledException", "TransactWriteItems",
                               CancellationReasons=reasons)
        self.tables = staged
        return {}

    def batch_write_item(self, **kw: Any) -> dict:
        self._enter("batch_write_item", kw)
        leftovers: dict[str, list] = {}
        for name, reqs in kw["RequestItems"].items():
            assert len(reqs) <= 25
            if self.unprocessed_once and reqs:
                leftovers[name], reqs = reqs[-1:], reqs[:-1]
            for r in reqs:
                self._table(name).pop(self._key(r["DeleteRequest"]["Key"]), None)
        self.unprocessed_once = False
        return {"UnprocessedItems": leftovers}

    def _page(self, kw: dict, rows: list[dict], matcher) -> dict:
        start = kw.get("ExclusiveStartKey")
        if start:
            sk = (start["pk"]["S"], start["sk"]["S"])
            rows = [r for r in rows if self._key(r) > sk]
        cap = kw.get("Limit") or len(rows)
        if self.page_size:
            cap = min(cap, self.page_size)
        evaluated, rest = rows[:cap], rows[cap:]
        out = [copy.deepcopy(r) for r in evaluated if matcher(r)]
        resp: dict[str, Any] = {"Items": out, "Count": len(out)}
        if rest:
            resp["LastEvaluatedKey"] = {"pk": evaluated[-1]["pk"], "sk": evaluated[-1]["sk"]}
        if "ProjectionExpression" in kw:
            resp["Items"] = [{"pk": r["pk"], "sk": r["sk"]} for r in resp["Items"]]
        return resp

    def query(self, **kw: Any) -> dict:
        self._enter("query", kw)
        names, values = kw.get("ExpressionAttributeNames"), kw.get("ExpressionAttributeValues")
        rows = sorted(self._table(kw["TableName"]).values(), key=self._key)
        rows = [r for r in rows if evaluate(kw["KeyConditionExpression"], r, names, values)]
        assert kw.get("ScanIndexForward", True)
        return self._page(kw, rows, lambda r: evaluate(kw.get("FilterExpression"), r, names, values))

    def scan(self, **kw: Any) -> dict:
        self._enter("scan", kw)
        names, values = kw.get("ExpressionAttributeNames"), kw.get("ExpressionAttributeValues")
        rows = sorted(self._table(kw["TableName"]).values(), key=self._key)
        return self._page(kw, rows, lambda r: evaluate(kw.get("FilterExpression"), r, names, values))


def n(value: int | float | Decimal) -> dict:
    return {"N": str(value)}
