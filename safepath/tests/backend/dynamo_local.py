"""DynamoDB Local helpers for the Repository contract suite (task 12.8; Req 30.9, 44.11).

Set ``DYNAMODB_ENDPOINT`` (e.g. ``http://localhost:8000`` for
``docker run -p 8000:8000 amazon/dynamodb-local``) to run the contract suite against a real
DynamoDB Local. When it is unset or unreachable the DynamoDB Local cases are skipped.

Safety: AWS hosts (``*.amazonaws.com``) are refused and the client always uses dummy
credentials, so these tests can never touch a real AWS account.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Iterable
from urllib.parse import urlparse

ENDPOINT_ENV = "DYNAMODB_ENDPOINT"
REGION = "us-east-1"
#: Logical tables the contract suite touches (every table is provisioned, as in a deployment).
CONTRACT_TABLES: tuple[str, ...] = ("t", "t1", "t2", "nope", "events")
#: A closed local port; pointing a client here simulates a DynamoDB outage.
UNREACHABLE_ENDPOINT = "http://127.0.0.1:9"


def configured_endpoint() -> str | None:
    url = (os.environ.get(ENDPOINT_ENV) or "").strip()
    return url or None


def _check_local(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if not host or host.endswith("amazonaws.com") or host.endswith("amazonaws.com.cn"):
        raise ValueError(f"{ENDPOINT_ENV} must point at DynamoDB Local, not {url!r}")


def make_local_client(url: str, *, retries: int = 1, timeout: float = 2) -> Any:
    import boto3
    from botocore.config import Config

    if url != UNREACHABLE_ENDPOINT:
        _check_local(url)
    return boto3.client(
        "dynamodb", endpoint_url=url, region_name=REGION,
        aws_access_key_id="local", aws_secret_access_key="local",
        config=Config(retries={"max_attempts": retries, "mode": "standard"},
                      connect_timeout=timeout, read_timeout=timeout * 3),
    )


def probe(url: str | None) -> str | None:
    """Return a skip reason, or ``None`` when DynamoDB Local answers at ``url``."""
    if not url:
        return f"{ENDPOINT_ENV} not set; DynamoDB Local contract run skipped"
    try:
        _check_local(url)
        import boto3  # noqa: F401
    except (ValueError, ImportError) as exc:
        return str(exc)
    try:
        make_local_client(url, retries=1, timeout=1).list_tables(Limit=1)
    except Exception as exc:  # unreachable, refused, not DynamoDB
        return f"DynamoDB Local unreachable at {url}: {type(exc).__name__}"
    return None


class LocalTables:
    """Creates the contract tables (with GSIs via ``create_table_params``) under a unique prefix."""

    def __init__(self, url: str, tables: Iterable[str] = CONTRACT_TABLES) -> None:
        from app.adapters.aws.dynamo import create_table_params

        self.url = url
        self.client = make_local_client(url)
        self.prefix = f"contract-{uuid.uuid4().hex[:8]}-"
        self.tables = tuple(tables)
        for t in self.tables:
            self.client.create_table(**create_table_params(t, self.prefix))
        waiter = self.client.get_waiter("table_exists")
        for t in self.tables:
            waiter.wait(TableName=f"{self.prefix}{t}", WaiterConfig={"Delay": 0.2, "MaxAttempts": 50})

    def clear(self) -> None:
        """Delete every item so each harness starts from empty tables."""
        for t in self.tables:
            name = f"{self.prefix}{t}"
            kwargs: dict[str, Any] = {"TableName": name, "ProjectionExpression": "pk, sk"}
            while True:
                resp = self.client.scan(**kwargs)
                for it in resp.get("Items", []):
                    self.client.delete_item(TableName=name, Key={"pk": it["pk"], "sk": it["sk"]})
                if not resp.get("LastEvaluatedKey"):
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def drop(self) -> None:
        for t in self.tables:
            try:
                self.client.delete_table(TableName=f"{self.prefix}{t}")
            except Exception:
                pass
