"""DynamoRepository: the Repository port on Amazon DynamoDB (Req 29.3, 30.7, 30.9, 32.2).

* One DynamoDB table per logical table, named ``<SAFEPATH_TABLE_PREFIX><table>`` (default
  ``safepath-``), every table keyed ``pk`` (HASH) / ``sk`` (RANGE), both strings, on-demand.
  An item without ``sk`` is stored with a sentinel sort key and returned without it.
* ``put(if_absent)`` and ``update_cas`` use condition expressions (``ConditionalCheckFailed`` ->
  ``Conflict``). ``update_cas`` treats a missing ``version`` as 0, like the local repositories.
* ``transact`` is one ``TransactWriteItems`` call (all-or-nothing, at most 100 items).
  ``TransactionCanceled`` with a failed condition (or a concurrent-transaction conflict) ->
  ``Conflict``; throttling, service, network and validation failures -> ``RepositoryUnavailable``
  with nothing persisted.
* ``query(index=...)`` queries a GSI. The indexes the code uses are ``report-id`` (``report_id``)
  and ``user-index`` (``user_id``); GSIs are eventually consistent.
* TTL: items may carry an epoch-seconds ``ttl`` attribute (enable DynamoDB TTL on it). DynamoDB
  deletes lazily (up to ~48 h), so reads treat an expired item as absent, and ``put(if_absent)``
  may overwrite an expired item.
* ``AWS_ENDPOINT_URL`` points the client at DynamoDB Local.
"""
from __future__ import annotations

import logging
import math
import time
from decimal import Decimal
from typing import Any, Callable, Mapping

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import BotoCoreError, ClientError

from app.adapters.ports import Conflict, RepositoryUnavailable, TxOp

log = logging.getLogger(__name__)

DEFAULT_PREFIX = "safepath-"
TABLE_PREFIX_ENV = "SAFEPATH_TABLE_PREFIX"
TTL_ATTRIBUTE = "ttl"
NO_SORT_KEY = "~none~"  # DynamoDB keys cannot be empty; stands in for a missing ``sk``
MAX_TRANSACTION_ITEMS = 100
BATCH_DELETE_SIZE = 25

#: Logical tables used by the application. Every table has the pk/sk primary key.
TABLES: tuple[str, ...] = (
    "users", "credentials", "incidents", "notices", "overlays", "overlay_meta", "reports",
    "report_votes", "messages", "message_index", "checkins", "checkin_locations", "events",
    "audit", "ratelimits", "demo_scenarios", "connections",
)
#: GSIs per table: index name -> (partition attribute, sort attribute or None).
INDEXES: dict[str, dict[str, tuple[str, str | None]]] = {
    "reports": {"report-id": ("report_id", None)},
    "checkins": {"user-index": ("user_id", None)},
}

_CONFLICT_REASONS = frozenset({"ConditionalCheckFailed", "TransactionConflict"})
_SER = TypeSerializer()
_DES = TypeDeserializer()


# --------------------------------------------------------------------------- conversion
def to_dynamo_value(value: Any) -> Any:
    """Python -> DynamoDB-safe Python (floats become Decimal, tuples lists)."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int, Decimal, bytes)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("NaN/Infinity cannot be stored in DynamoDB")
        return Decimal(repr(value))
    if isinstance(value, Mapping):
        return {str(k): to_dynamo_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dynamo_value(v) for v in value]
    return value


def from_dynamo_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_dynamo_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [from_dynamo_value(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [from_dynamo_value(v) for v in value]
    return value


def encode_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {k: _SER.serialize(v) for k, v in to_dynamo_value(dict(item)).items()}


def decode_item(raw: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: from_dynamo_value(_DES.deserialize(v)) for k, v in raw.items()}
    if out.get("sk") == NO_SORT_KEY:
        del out["sk"]
    return out


def _av(value: Any) -> Any:
    return _SER.serialize(to_dynamo_value(value))


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def create_table_params(table: str, prefix: str = DEFAULT_PREFIX) -> dict[str, Any]:
    """``CreateTable`` arguments (on-demand) for DynamoDB Local or tooling. Never called here."""
    attrs = {"pk": "S", "sk": "S"}
    gsis = []
    for name, (part, sort) in INDEXES.get(table, {}).items():
        attrs[part] = "S"
        schema = [{"AttributeName": part, "KeyType": "HASH"}]
        if sort:
            attrs[sort] = "S"
            schema.append({"AttributeName": sort, "KeyType": "RANGE"})
        gsis.append({"IndexName": name, "KeySchema": schema, "Projection": {"ProjectionType": "ALL"}})
    params: dict[str, Any] = {
        "TableName": f"{prefix}{table}",
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [{"AttributeName": a, "AttributeType": t} for a, t in attrs.items()],
        "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"},
                      {"AttributeName": "sk", "KeyType": "RANGE"}],
    }
    if gsis:
        params["GlobalSecondaryIndexes"] = gsis
    return params


def ttl_specification() -> dict[str, Any]:
    """``UpdateTimeToLive`` specification used for every table that stores ``ttl``."""
    return {"Enabled": True, "AttributeName": TTL_ATTRIBUTE}


class DynamoRepository:
    def __init__(self, client: Any, *, table_prefix: str = DEFAULT_PREFIX,
                 indexes: Mapping[str, tuple[str, str | None]] | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 ) -> None:
        self._c = client
        self.prefix = table_prefix
        merged = {name: spec for per in INDEXES.values() for name, spec in per.items()}
        merged.update(indexes or {})
        self.indexes = merged
        self._clock = clock
        self._sleep = sleep

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, client: Any = None) -> "DynamoRepository":
        from app.adapters.aws.session import env_of, make_client

        e = env_of(env)
        prefix = e.get(TABLE_PREFIX_ENV)
        return cls(client or make_client("dynamodb", e),
                   table_prefix=DEFAULT_PREFIX if prefix is None else prefix)

    # ------------------------------------------------------------------ helpers
    def table_name(self, table: str) -> str:
        return f"{self.prefix}{table}"

    @staticmethod
    def _key(key: Mapping[str, Any]) -> dict[str, Any]:
        if "pk" not in key:
            raise ValueError("missing partition key attribute 'pk'")
        sk = key.get("sk", "")
        return {"pk": _av(key["pk"]), "sk": _av(NO_SORT_KEY if sk == "" else sk)}

    def _item(self, item: Mapping[str, Any]) -> dict[str, Any]:
        if "pk" not in item:
            raise ValueError("missing partition key attribute 'pk'")
        data = dict(item)
        if data.get("sk", "") == "":
            data["sk"] = NO_SORT_KEY
        return encode_item(data)

    def _expired(self, item: Mapping[str, Any]) -> bool:
        ttl = item.get(TTL_ATTRIBUTE)
        return isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and ttl < self._clock()

    def _call(self, op: str, **kwargs: Any) -> Any:
        try:
            return getattr(self._c, op)(**kwargs)
        except ClientError as exc:
            code = _error_code(exc)
            if code == "ConditionalCheckFailedException":
                raise Conflict("conditional check failed") from exc
            if code == "TransactionCanceledException":
                reasons = [r.get("Code") for r in exc.response.get("CancellationReasons", [])]
                if any(r in _CONFLICT_REASONS for r in reasons):
                    raise Conflict(f"transaction cancelled: {reasons}") from exc
                log.warning("dynamodb %s cancelled: %s", op, reasons)
                raise RepositoryUnavailable(f"transaction cancelled: {reasons}") from exc
            log.warning("dynamodb %s failed: %s", op, code)
            raise RepositoryUnavailable(f"dynamodb {op} failed: {code or exc}") from exc
        except BotoCoreError as exc:
            log.warning("dynamodb %s failed: %s", op, exc)
            raise RepositoryUnavailable(f"dynamodb {op} failed: {exc}") from exc

    def _absent_condition(self) -> tuple[str, dict[str, str], dict[str, Any]]:
        expr = "attribute_not_exists(#pk) OR (attribute_exists(#ttl) AND #ttl < :now)"
        return expr, {"#pk": "pk", "#ttl": TTL_ATTRIBUTE}, {":now": _av(int(self._clock()))}

    def _put_request(self, table: str, item: Mapping[str, Any], if_absent: bool) -> dict[str, Any]:
        req: dict[str, Any] = {"TableName": self.table_name(table), "Item": self._item(item)}
        if if_absent:
            cond, names, values = self._absent_condition()
            req.update(ConditionExpression=cond, ExpressionAttributeNames=names,
                       ExpressionAttributeValues=values)
        return req

    def _cas_request(self, table: str, key: Mapping[str, Any], expected: int,
                     patch: Mapping[str, Any]) -> dict[str, Any]:
        names = {"#pk": "pk", "#version": "version"}
        values: dict[str, Any] = {":ev": _av(int(expected)), ":nv": _av(int(expected) + 1)}
        sets = ["#version = :nv"]
        i = 0
        for attr, val in patch.items():
            if attr in ("pk", "sk", "version"):
                continue  # key attributes are immutable; version is managed here
            names[f"#u{i}"] = attr
            values[f":u{i}"] = _av(val)
            sets.append(f"#u{i} = :u{i}")
            i += 1
        if int(expected) == 0:
            cond = "attribute_exists(#pk) AND (attribute_not_exists(#version) OR #version = :ev)"
        else:
            cond = "attribute_exists(#pk) AND #version = :ev"
        return {
            "TableName": self.table_name(table), "Key": self._key(key),
            "UpdateExpression": "SET " + ", ".join(sets), "ConditionExpression": cond,
            "ExpressionAttributeNames": names, "ExpressionAttributeValues": values,
        }

    # ------------------------------------------------------------------ port
    def put(self, table: str, item: dict, *, if_absent: bool = False) -> None:
        self._call("put_item", **self._put_request(table, item, if_absent))

    def get(self, table: str, key: dict) -> dict | None:
        resp = self._call("get_item", TableName=self.table_name(table), Key=self._key(key),
                          ConsistentRead=True)
        raw = resp.get("Item")
        if not raw:
            return None
        item = decode_item(raw)
        return None if self._expired(item) else item

    def query(self, table: str, key_cond: dict, *, index: str | None = None,
              since: str | None = None, limit: int = 100) -> list[dict]:
        if limit <= 0:
            return []
        cond = dict(key_cond)
        if index:
            part, sort = self.indexes.get(index) or (next(iter(cond), ""), None)
        else:
            part, sort = "pk", "sk"
        if part not in cond:
            raise ValueError(f"key_cond must contain {part!r}" + (f" for index {index!r}" if index else ""))
        names: dict[str, str] = {"#kp": part}
        values: dict[str, Any] = {":kp": _av(cond.pop(part))}
        expr = "#kp = :kp"
        since_attr = sort or "sk"
        post_since = since
        if sort and sort in cond:
            names["#ks"] = sort
            values[":ks"] = _av(cond.pop(sort))
            expr += " AND #ks = :ks"
        elif sort and since is not None:
            names["#ks"] = sort
            values[":since"] = _av(since)
            expr += " AND #ks > :since"
            post_since = None
        filters = []
        for i, (attr, val) in enumerate(cond.items()):
            names[f"#f{i}"] = attr
            values[f":f{i}"] = _av(val)
            filters.append(f"#f{i} = :f{i}")
        base: dict[str, Any] = {
            "TableName": self.table_name(table), "KeyConditionExpression": expr,
            "ExpressionAttributeNames": names, "ExpressionAttributeValues": values,
            "ScanIndexForward": True,
        }
        if index:
            base["IndexName"] = index
        else:
            base["ConsistentRead"] = True
        if filters:
            base["FilterExpression"] = " AND ".join(filters)

        out: list[dict] = []
        start: dict[str, Any] | None = None
        while len(out) < limit:
            req = dict(base)
            if not filters:
                req["Limit"] = limit - len(out)
            if start:
                req["ExclusiveStartKey"] = start
            resp = self._call("query", **req)
            for raw in resp.get("Items", []):
                item = decode_item(raw)
                if self._expired(item):
                    continue
                if post_since is not None and not str(item.get(since_attr, "")) > post_since:
                    continue
                out.append(item)
                if len(out) >= limit:
                    break
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
        return out

    def update_cas(self, table: str, key: dict, expected_version: int, patch: dict) -> dict:
        resp = self._call("update_item", ReturnValues="ALL_NEW",
                          **self._cas_request(table, key, expected_version, patch))
        return decode_item(resp.get("Attributes", {}))

    def transact(self, ops: list[TxOp]) -> None:
        if not ops:
            return
        if len(ops) > MAX_TRANSACTION_ITEMS:
            raise ValueError(f"a DynamoDB transaction holds at most {MAX_TRANSACTION_ITEMS} items "
                             f"(got {len(ops)})")
        seen: set[tuple[str, Any, Any]] = set()
        items: list[dict[str, Any]] = []
        for op in ops:
            src = (op.item or {}) if op.kind == "put" else (op.key or {})
            ident = (op.table, src.get("pk"), src.get("sk", "") or NO_SORT_KEY)
            if ident in seen:
                raise ValueError(f"DynamoDB allows one operation per item per transaction: {ident}")
            seen.add(ident)
            if op.kind == "put":
                items.append({"Put": self._put_request(op.table, op.item or {}, op.if_absent)})
            elif op.kind == "update_cas":
                items.append({"Update": self._cas_request(
                    op.table, op.key or {}, int(op.expected_version or 0), op.patch or {})})
            elif op.kind == "delete":
                items.append({"Delete": {"TableName": self.table_name(op.table),
                                         "Key": self._key(op.key or {})}})
            else:  # pragma: no cover
                raise ValueError(f"unknown op kind {op.kind!r}")
        self._call("transact_write_items", TransactItems=items)

    def delete_where(self, table: str, predicate: Mapping) -> int:
        cond = dict(predicate)
        names: dict[str, str] = {"#dpk": "pk", "#dsk": "sk"}
        values: dict[str, Any] = {}
        proj = "#dpk, #dsk"
        if "pk" in cond:
            values[":kp"] = _av(cond.pop("pk"))
            expr = "#dpk = :kp"
            if "sk" in cond:
                sk = cond.pop("sk")
                values[":ks"] = _av(NO_SORT_KEY if sk == "" else sk)
                expr += " AND #dsk = :ks"
            op, extra = "query", {"KeyConditionExpression": expr, "ConsistentRead": True}
        else:
            op, extra = "scan", {"ConsistentRead": True}
        filters = []
        for i, (attr, val) in enumerate(cond.items()):
            names[f"#f{i}"] = attr
            values[f":f{i}"] = _av(val)
            filters.append(f"#f{i} = :f{i}")
        if filters:
            extra["FilterExpression"] = " AND ".join(filters)
        req: dict[str, Any] = {"TableName": self.table_name(table), "ProjectionExpression": proj,
                               "ExpressionAttributeNames": names, **extra}
        if values:
            req["ExpressionAttributeValues"] = values

        keys: list[dict[str, Any]] = []
        start = None
        while True:
            page = dict(req)
            if start:
                page["ExclusiveStartKey"] = start
            resp = self._call(op, **page)
            keys.extend({"pk": it["pk"], "sk": it["sk"]} for it in resp.get("Items", []))
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
        for i in range(0, len(keys), BATCH_DELETE_SIZE):
            self._batch_delete(table, keys[i:i + BATCH_DELETE_SIZE])
        return len(keys)

    def _batch_delete(self, table: str, keys: list[dict[str, Any]]) -> None:
        name = self.table_name(table)
        pending: dict[str, list[dict[str, Any]]] = {
            name: [{"DeleteRequest": {"Key": k}} for k in keys]}
        for attempt in range(6):
            resp = self._call("batch_write_item", RequestItems=pending)
            pending = resp.get("UnprocessedItems") or {}
            if not pending:
                return
            self._sleep(0.05 * (2 ** attempt))
        raise RepositoryUnavailable("batch delete left unprocessed items")
