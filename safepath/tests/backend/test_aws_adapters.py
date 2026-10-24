"""AWS adapters and Lambda entry points (task 12.2; Req 25.4, 29.3, 29.5, 30.7, 30.9, 31.4, 31.5).

No network and no AWS: DynamoDB is a fake client or botocore's Stubber, S3 presigning is a local
computation with dummy credentials, JWTs are signed with a locally generated RSA key.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import boto3
import jwt
import pytest
from botocore.config import Config
from botocore.exceptions import EndpointConnectionError
from botocore.stub import Stubber
from cryptography.hazmat.primitives.asymmetric import rsa
from fake_dynamodb import FakeDynamoDB, client_error

from app.adapters import (
    AdapterNotAvailable, Conflict, Event, MemoryRepository, RepositoryUnavailable, Role, TxOp,
    Unauthenticated, make_auth_provider, make_blob_store, make_publisher, make_repository,
)
from app.adapters.aws.cognito import CognitoAuthProvider, issuer_for
from app.adapters.aws.dynamo import (
    INDEXES, TABLES, DynamoRepository, create_table_params, decode_item, ttl_specification,
)
from app.adapters.aws.graph_source import GraphDownloadError, download_graph
from app.adapters.aws.publishers import (
    ApiGwWebSocketPublisher, AppSyncEventsPublisher, register_connection,
)
from app.adapters.aws.s3 import S3BlobStore
from app.auth.provider import DemoTokenAuthProvider

ROOT = Path(__file__).resolve().parents[2]
CREDS = {"aws_access_key_id": "AKIDTESTONLY", "aws_secret_access_key": "test-secret-not-real"}
AWS_ENV = {"SAFEPATH_PROFILE": "aws", "AWS_REGION": "us-east-1", **{
    "AWS_ACCESS_KEY_ID": "AKIDTESTONLY", "AWS_SECRET_ACCESS_KEY": "test-secret-not-real"}}


# ============================================================================ DynamoRepository
def _repo(fake: FakeDynamoDB | None = None, **kw) -> tuple[DynamoRepository, FakeDynamoDB]:
    fake = fake or FakeDynamoDB()
    return DynamoRepository(fake, table_prefix="sp-", **kw), fake


def test_dynamo_roundtrip_types_and_missing_sort_key():
    repo, fake = _repo()
    repo.put("t", {"pk": "a", "sk": "1", "lat": 41.88, "n": 3, "flag": True, "nul": None,
                   "nested": {"xs": [1, 2.5, "z"]}, "empty": ""})
    assert repo.get("t", {"pk": "a", "sk": "1"}) == {
        "pk": "a", "sk": "1", "lat": 41.88, "n": 3, "flag": True, "nul": None,
        "nested": {"xs": [1, 2.5, "z"]}, "empty": ""}
    repo.put("t", {"pk": "solo", "x": 1})  # no sk: stored with a sentinel, returned without it
    assert repo.get("t", {"pk": "solo"}) == {"pk": "solo", "x": 1}
    assert "sp-t" in fake.tables
    with pytest.raises(ValueError):
        repo.put("t", {"sk": "no-pk"})


def test_dynamo_if_absent_conflict_and_cas_request_shapes():
    repo, fake = _repo()
    repo.put("t", {"pk": "a", "sk": "1"}, if_absent=True)
    with pytest.raises(Conflict):
        repo.put("t", {"pk": "a", "sk": "1", "v": 2}, if_absent=True)
    put_calls = [kw for op, kw in fake.calls if op == "put_item"]
    assert "attribute_not_exists(#pk)" in put_calls[0]["ConditionExpression"]
    # CAS: absent version counts as 0; version bumps; stale version conflicts; missing item conflicts.
    out = repo.update_cas("t", {"pk": "a", "sk": "1"}, 0, {"n": 5, "pk": "ignored", "version": 99})
    assert out["version"] == 1 and out["n"] == 5 and out["pk"] == "a"
    with pytest.raises(Conflict):
        repo.update_cas("t", {"pk": "a", "sk": "1"}, 0, {"n": 6})
    with pytest.raises(Conflict):
        repo.update_cas("t", {"pk": "missing", "sk": "1"}, 0, {})
    assert repo.update_cas("t", {"pk": "a", "sk": "1"}, 1, {"n": 7})["version"] == 2
    cas = [kw for op, kw in fake.calls if op == "update_item"][0]
    assert cas["ConditionExpression"].startswith("attribute_exists(#pk)")
    assert cas["UpdateExpression"].startswith("SET #version = :nv")


def test_dynamo_transact_maps_conditional_failure_to_conflict_and_is_atomic():
    repo, fake = _repo()
    repo.put("t", {"pk": "a", "sk": "1", "version": 0})
    ops = [TxOp("put", "t", item={"pk": "new", "sk": "1"}, if_absent=True),
           TxOp("update_cas", "t", key={"pk": "a", "sk": "1"}, expected_version=7, patch={"z": 1})]
    with pytest.raises(Conflict):
        repo.transact(ops)
    assert repo.get("t", {"pk": "new", "sk": "1"}) is None
    assert repo.get("t", {"pk": "a", "sk": "1"})["version"] == 0
    repo.transact([ops[0], TxOp("update_cas", "t", key={"pk": "a", "sk": "1"}, expected_version=0,
                                patch={"z": 1}), TxOp("delete", "t", key={"pk": "gone", "sk": "1"})])
    assert repo.get("t", {"pk": "a", "sk": "1"})["z"] == 1
    # One TransactWriteItems call carries every op.
    assert sum(1 for op, _ in fake.calls if op == "transact_write_items") == 2


def test_dynamo_transact_limits_and_duplicates():
    repo, _ = _repo()
    repo.transact([])  # no-op
    too_many = [TxOp("put", "t", item={"pk": f"p{i}", "sk": "1"}) for i in range(101)]
    with pytest.raises(ValueError, match="100"):
        repo.transact(too_many)
    repo.transact(too_many[:100])
    with pytest.raises(ValueError, match="one operation per item"):
        repo.transact([TxOp("put", "t", item={"pk": "d", "sk": "1"}),
                       TxOp("delete", "t", key={"pk": "d", "sk": "1"})])


@pytest.mark.parametrize("code", ["ThrottlingException", "ProvisionedThroughputExceededException",
                                  "InternalServerError", "ServiceUnavailable", "RequestLimitExceeded"])
def test_dynamo_throttling_and_service_errors_map_to_unavailable(code):
    client = boto3.client("dynamodb", region_name="us-east-1", **CREDS)
    repo = DynamoRepository(client, table_prefix="sp-")
    with Stubber(client) as stub:
        stub.add_client_error("put_item", service_error_code=code)
        stub.add_client_error("get_item", service_error_code=code)
        stub.add_client_error("transact_write_items", service_error_code=code)
        with pytest.raises(RepositoryUnavailable):
            repo.put("t", {"pk": "a", "sk": "1"})
        with pytest.raises(RepositoryUnavailable):
            repo.get("t", {"pk": "a", "sk": "1"})
        with pytest.raises(RepositoryUnavailable):
            repo.transact([TxOp("put", "t", item={"pk": "a", "sk": "1"})])
        stub.assert_no_pending_responses()


def test_dynamo_stubber_conditional_and_cancellation_reasons():
    client = boto3.client("dynamodb", region_name="us-east-1", **CREDS)
    repo = DynamoRepository(client, table_prefix="sp-", clock=lambda: 1_000.0)
    with Stubber(client) as stub:
        stub.add_client_error("put_item", service_error_code="ConditionalCheckFailedException",
                              expected_params={
                                  "TableName": "sp-t",
                                  "Item": {"pk": {"S": "a"}, "sk": {"S": "1"}},
                                  "ConditionExpression": "attribute_not_exists(#pk) OR "
                                                         "(attribute_exists(#ttl) AND #ttl < :now)",
                                  "ExpressionAttributeNames": {"#pk": "pk", "#ttl": "ttl"},
                                  "ExpressionAttributeValues": {":now": {"N": "1000"}}})
        with pytest.raises(Conflict):
            repo.put("t", {"pk": "a", "sk": "1"}, if_absent=True)
        stub.add_client_error(
            "transact_write_items", service_error_code="TransactionCanceledException",
            modeled_fields={"CancellationReasons": [{"Code": "None"}, {"Code": "ConditionalCheckFailed"}]})
        with pytest.raises(Conflict):
            repo.transact([TxOp("put", "t", item={"pk": "a", "sk": "1"}),
                           TxOp("put", "t", item={"pk": "b", "sk": "1"}, if_absent=True)])
        stub.add_client_error(
            "transact_write_items", service_error_code="TransactionCanceledException",
            modeled_fields={"CancellationReasons": [{"Code": "ThrottlingError"}, {"Code": "None"}]})
        with pytest.raises(RepositoryUnavailable):
            repo.transact([TxOp("put", "t", item={"pk": "a", "sk": "1"}),
                           TxOp("put", "t", item={"pk": "b", "sk": "1"})])


def test_dynamo_network_failure_maps_to_unavailable():
    repo, fake = _repo()
    fake.faults["query"] = EndpointConnectionError(endpoint_url="https://dynamodb.invalid")
    with pytest.raises(RepositoryUnavailable):
        repo.query("t", {"pk": "a"})


def test_dynamo_query_index_filters_paging_and_limit():
    repo, fake = _repo(FakeDynamoDB(page_size=2))
    for i in range(7):
        repo.put("reports", {"pk": "inc1", "sk": f"r{i}", "report_id": f"rep{i}", "cat": "a" if i % 2 else "b"})
    # GSI lookup by report_id (used by verification.find_row)
    rows = repo.query("reports", {"report_id": "rep3"}, index="report-id", limit=1)
    assert [r["sk"] for r in rows] == ["r3"]
    call = [kw for op, kw in fake.calls if op == "query"][-1]
    assert call["IndexName"] == "report-id" and "ConsistentRead" not in call
    # Non-key condition becomes a filter; paging continues until the limit is satisfied.
    got = repo.query("reports", {"pk": "inc1", "cat": "b"}, limit=3)
    assert [r["sk"] for r in got] == ["r0", "r2", "r4"]
    assert [r["sk"] for r in repo.query("reports", {"pk": "inc1"}, since="r4")] == ["r5", "r6"]
    assert repo.query("reports", {"pk": "inc1"}, limit=0) == []
    with pytest.raises(ValueError):
        repo.query("reports", {"sk": "x"})
    assert "user-index" in {n for per in INDEXES.values() for n in per}


def test_dynamo_ttl_expired_items_are_absent_and_replaceable():
    now = [1_000.0]
    repo, _ = _repo(clock=lambda: now[0])
    repo.put("t", {"pk": "a", "sk": "1", "ttl": 1_500})
    repo.put("t", {"pk": "a", "sk": "2", "ttl": 900})
    assert repo.get("t", {"pk": "a", "sk": "1"}) is not None
    assert repo.get("t", {"pk": "a", "sk": "2"}) is None  # past ttl, not yet deleted by DynamoDB
    assert [r["sk"] for r in repo.query("t", {"pk": "a"})] == ["1"]
    repo.put("t", {"pk": "a", "sk": "2", "ttl": 5_000}, if_absent=True)  # may replace an expired item
    with pytest.raises(Conflict):
        repo.put("t", {"pk": "a", "sk": "1"}, if_absent=True)
    assert ttl_specification() == {"Enabled": True, "AttributeName": "ttl"}


def test_dynamo_delete_where_by_pk_and_scan_and_unprocessed_retry():
    repo, fake = _repo(sleep=lambda s: None)
    for i in range(30):
        repo.put("t", {"pk": "a", "sk": f"{i:02d}", "g": "x" if i < 28 else "y"})
    repo.put("t", {"pk": "b", "sk": "1", "g": "x"})
    fake.unprocessed_once = True
    assert repo.delete_where("t", {"pk": "a", "g": "x"}) == 28  # query + filter, batches of 25
    assert [r["sk"] for r in repo.query("t", {"pk": "a"})] == ["28", "29"]
    assert repo.delete_where("t", {"g": "x"}) == 1  # no pk -> scan
    assert repo.delete_where("t", {"pk": "zzz"}) == 0
    assert [op for op, _ in fake.calls if op == "scan"]


def test_dynamo_table_definitions_are_on_demand_with_required_gsis():
    assert "reports" in TABLES and "connections" in TABLES
    p = create_table_params("reports", "sp-")
    assert p["BillingMode"] == "PAY_PER_REQUEST" and p["TableName"] == "sp-reports"
    assert p["GlobalSecondaryIndexes"][0]["IndexName"] == "report-id"
    assert "GlobalSecondaryIndexes" not in create_table_params("events")
    assert decode_item({"pk": {"S": "a"}, "sk": {"S": "~none~"}}) == {"pk": "a"}


# ============================================================================ S3BlobStore
def _s3():
    client = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"), **CREDS)
    return S3BlobStore("sp-private-bucket", client, clock=lambda: 1_000.0)


def _q(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_s3_presign_put_signs_type_and_size_with_15_minute_expiry():
    up = _s3().presign_put("photos/u/abc.jpg", "image/jpeg", 2048)
    q = _q(up.url)
    assert up.method == "PUT" and up.headers == {"Content-Type": "image/jpeg"}
    assert up.max_bytes == 2048 and up.key == "photos/u/abc.jpg"
    assert up.expires_at == datetime.fromtimestamp(1_000 + 900, timezone.utc)
    assert urlsplit(up.url).scheme == "https" and "sp-private-bucket" in up.url
    assert q["X-Amz-Expires"] == "900" and q["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256"
    signed = q["X-Amz-SignedHeaders"].split(";")
    assert "content-type" in signed and "content-length" in signed  # type and size are enforced by S3
    assert "X-Amz-Signature" in q and "test-secret-not-real" not in up.url


def test_s3_presign_get_expiry_is_capped_at_15_minutes_and_keys_validated():
    store = _s3()
    assert _q(store.presign_get("photos/a.jpg"))["X-Amz-Expires"] == "900"
    assert _q(store.presign_get("photos/a.jpg", 60))["X-Amz-Expires"] == "60"
    assert _q(store.presign_get("photos/a.jpg", 86_400))["X-Amz-Expires"] == "900"
    for bad in ["", "../x", "a/../b", "/abs", "a\\b"]:
        with pytest.raises(ValueError):
            store.presign_get(bad)
    with pytest.raises(ValueError):
        store.presign_put("k", "image/png", 0)


# ============================================================================ publishers
EVENT = Event(id="evt_1", incident_id="inc_1", type="report_created", payload={"a": 1},
              created_at="2026-01-01T00:00:00+00:00")


def test_appsync_publish_api_key_and_channel():
    sent = []
    pub = AppSyncEventsPublisher("abc.appsync-api.us-east-1.amazonaws.com", api_key="da2-key",
                                 transport=lambda u, b, h, t: sent.append((u, b, h, t)) or 200)
    pub.publish("inc_1", EVENT)
    (url, body, headers, timeout), = sent
    assert url == "https://abc.appsync-api.us-east-1.amazonaws.com/event" and timeout <= 5
    assert headers["x-api-key"] == "da2-key"
    doc = json.loads(body)
    assert doc["channel"] == "/incidents/inc_1" and len(doc["events"]) == 1
    assert json.loads(doc["events"][0])["event_id"] == "evt_1"
    assert pub.channel("a/b c") == "/incidents/a_b_c"


def test_appsync_iam_signs_with_sigv4_and_never_raises():
    from botocore.credentials import Credentials

    sent = []
    pub = AppSyncEventsPublisher(
        "https://abc.appsync-api.us-east-1.amazonaws.com/event", region="us-east-1",
        credentials=lambda: Credentials("AKIDTESTONLY", "test-secret-not-real"),
        transport=lambda u, b, h, t: sent.append(h) or 200)
    pub.publish("inc_1", EVENT)
    assert sent[0]["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDTESTONLY/")
    assert "/us-east-1/appsync/aws4_request" in sent[0]["Authorization"]

    def boom(*a):
        raise TimeoutError("down")

    for transport in (boom, lambda *a: 503):
        p = AppSyncEventsPublisher("https://x.example.com", api_key="k", transport=transport)
        p.publish("inc_1", EVENT)  # must not raise
        assert p.failures == 1
    no_creds = AppSyncEventsPublisher("https://x.example.com", region="us-east-1",
                                      credentials=lambda: None, transport=lambda *a: 200)
    no_creds.publish("inc_1", EVENT)
    assert no_creds.failures == 1
    with pytest.raises(ValueError):
        AppSyncEventsPublisher("http://insecure.example.com", api_key="k")
    with pytest.raises(ValueError):
        AppSyncEventsPublisher("https://x.example.com")  # neither api key nor IAM


class _Gone(Exception):
    response = {"Error": {"Code": "GoneException"}, "ResponseMetadata": {"HTTPStatusCode": 410}}


class _FakeMgmt:
    def __init__(self, gone=(), broken=()):
        self.sent, self.gone, self.broken = [], set(gone), set(broken)

    def post_to_connection(self, ConnectionId, Data):
        if ConnectionId in self.gone:
            raise _Gone()
        if ConnectionId in self.broken:
            raise RuntimeError("boom")
        self.sent.append((ConnectionId, json.loads(Data)))


def test_apigw_publisher_fans_out_prunes_gone_and_never_raises():
    repo = MemoryRepository()
    for c in ("c1", "c2", "c3"):
        register_connection(repo, c, "inc_1", 1_000)
    register_connection(repo, "other", "inc_2", 1_000)
    client = _FakeMgmt(gone={"c2"}, broken={"c3"})
    pub = ApiGwWebSocketPublisher(repo, client)
    pub.publish("inc_1", EVENT)
    assert [c for c, _ in client.sent] == ["c1"] and client.sent[0][1]["type"] == "report_created"
    assert pub.failures == 1  # the broken one
    assert repo.get("connections", {"pk": "inc_1", "sk": "c2"}) is None  # pruned
    assert repo.get("connections", {"pk": "conn#c2", "sk": "incident"}) is None
    assert repo.get("connections", {"pk": "inc_1", "sk": "c1"}) is not None
    repo.available = False  # store outage: still best effort
    pub.publish("inc_1", EVENT)
    assert pub.failures == 2


# ============================================================================ Cognito
POOL, CLIENT, REGION = "us-east-1_TestPool", "client-123", "us-east-1"
ISS = issuer_for(REGION, POOL)


@pytest.fixture(scope="module")
def keypair():
    def make(kid):
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key()))
        jwk.update(kid=kid, alg="RS256", use="sig")
        return priv, jwk

    return {"k1": make("k1"), "k2": make("k2")}


def _provider(keypair, **kw):
    calls = []

    def fetch():
        calls.append(1)
        return {"keys": [keypair["k1"][1]]}

    return CognitoAuthProvider(region=REGION, user_pool_id=POOL, client_id=CLIENT,
                               jwks_fetcher=fetch, **kw), calls


def _token(keypair, kid="k1", **over):
    now = int(time.time())
    claims = {"sub": "sub-1", "iss": ISS, "aud": CLIENT, "token_use": "id", "iat": now,
              "exp": now + 600, **over}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, keypair[kid][0], algorithm="RS256", headers={"kid": kid})


def test_cognito_valid_id_and_access_tokens(keypair):
    prov, calls = _provider(keypair)
    p = prov.verify(_token(keypair))
    assert (p.subject, p.role, p.is_guest) == ("sub-1", Role.USER, False) and p.expires_at
    access = _token(keypair, aud=None, client_id=CLIENT, token_use="access")
    assert prov.verify(access).subject == "sub-1"
    assert len(calls) == 1  # JWKS cached across verifications


def test_cognito_roles_come_only_from_groups_and_admin_config(keypair):
    prov, _ = _provider(keypair, roles={"sub-admin": Role.MODERATOR})
    assert prov.verify(_token(keypair, **{"cognito:groups": ["Moderator"]})).role is Role.MODERATOR
    assert prov.verify(_token(keypair, **{"cognito:groups": ["AuthorizedOfficial", "Moderator"]})
                       ).role is Role.AUTHORIZED_OFFICIAL
    assert prov.verify(_token(keypair, sub="sub-admin")).role is Role.MODERATOR
    assert prov.role_for("sub-admin") is Role.MODERATOR and prov.role_for("nobody") is Role.USER
    # Client-controllable / unverified claims never grant a role.
    forged = _token(keypair, role="authorized_official", **{"custom:role": "authorized_official",
                                                            "cognito:groups": ["SomethingElse"]})
    assert prov.verify(forged).role is Role.USER


@pytest.mark.parametrize("over,why", [
    ({"exp": int(time.time()) - 3600}, "expired"),
    ({"aud": "someone-else"}, "wrong audience"),
    ({"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other"}, "wrong issuer"),
    ({"token_use": "refresh"}, "wrong token_use"),
    ({"aud": None, "client_id": "other", "token_use": "access"}, "wrong access client"),
    ({"exp": None}, "missing exp"),
])
def test_cognito_rejects_bad_claims(keypair, over, why):
    prov, _ = _provider(keypair)
    with pytest.raises(Unauthenticated):
        prov.verify(_token(keypair, **over))


def test_cognito_rejects_tampered_unknown_kid_and_bad_algorithms(keypair):
    prov, calls = _provider(keypair)
    good = _token(keypair)
    head, payload, sig = good.split(".")
    tampered = jwt.encode({"sub": "attacker", "iss": ISS, "aud": CLIENT, "token_use": "id",
                           "exp": int(time.time()) + 600}, keypair["k1"][0], algorithm="RS256",
                          headers={"kid": "k1"}).split(".")[1]
    for bad in (f"{head}.{tampered}.{sig}", f"{head}.{payload}.{sig[:-4]}AAAA", "a.b.c", "x.y", ""):
        with pytest.raises(Unauthenticated):
            prov.verify(bad)
    with pytest.raises(Unauthenticated):  # signed by a key that is not in the JWKS
        prov.verify(_token(keypair, kid="k2"))
    assert len(calls) == 1  # forced refresh is rate limited (keys were fetched moments ago)
    # After the minimum interval an unknown kid triggers one refresh, which may pick up new keys.
    clock = [time.time()]
    fetched = []
    docs = [{"keys": [keypair["k1"][1]]}, {"keys": [keypair["k1"][1], keypair["k2"][1]]}]
    rotating = CognitoAuthProvider(region=REGION, user_pool_id=POOL, client_id=CLIENT,
                                   jwks_fetcher=lambda: fetched.append(1) or docs[min(len(fetched) - 1, 1)],
                                   clock=lambda: clock[0])
    rotating.verify(_token(keypair))
    with pytest.raises(Unauthenticated):
        rotating.verify(_token(keypair, kid="k2"))
    clock[0] += 120
    assert rotating.verify(_token(keypair, kid="k2")).subject == "sub-1"  # rotated key found
    assert len(fetched) == 2
    hs = jwt.encode({"sub": "x", "iss": ISS, "aud": CLIENT, "token_use": "id",
                     "exp": int(time.time()) + 60}, "secret" * 8, algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(Unauthenticated):
        prov.verify(hs)
    none_tok = jwt.encode({"sub": "x"}, None, algorithm="none", headers={"kid": "k1"})
    with pytest.raises(Unauthenticated):
        prov.verify(none_tok)
    with pytest.raises(Unauthenticated):
        prov.verify(None)


def test_cognito_jwks_outage_fails_closed_and_stale_keys_keep_working(keypair):
    def down():
        raise OSError("no network")

    prov = CognitoAuthProvider(region=REGION, user_pool_id=POOL, client_id=CLIENT, jwks_fetcher=down)
    with pytest.raises(Unauthenticated):
        prov.verify(_token(keypair))
    now = [0.0]
    state = {"fail": False}

    def fetch():
        if state["fail"]:
            raise OSError("down")
        return {"keys": [keypair["k1"][1]]}

    prov2 = CognitoAuthProvider(region=REGION, user_pool_id=POOL, client_id=CLIENT, jwks_fetcher=fetch,
                                clock=lambda: time.time() + now[0])
    long_lived = _token(keypair, exp=int(time.time()) + 86_400)
    assert prov2.verify(long_lived).subject == "sub-1"
    state["fail"] = True
    now[0] = 7_200  # cache expired, refresh fails -> keep the stale keys
    assert prov2.verify(long_lived).subject == "sub-1"


def test_cognito_guest_tokens_delegate_to_demo_provider(keypair):
    demo = DemoTokenAuthProvider("s" * 32, allow_ephemeral=False)
    prov, _ = _provider(keypair, guest_provider=demo)
    guest = prov.issue_guest()
    assert prov.verify(guest.token).is_guest
    plain, _ = _provider(keypair)
    with pytest.raises(AdapterNotAvailable):
        plain.issue_guest()
    with pytest.raises(Unauthenticated):
        plain.verify(guest.token)


# ============================================================================ factory wiring
def test_factory_aws_profile_builds_real_adapters_offline():
    env = {**AWS_ENV, "SAFEPATH_TABLE_PREFIX": "demo-",
           "SAFEPATH_PHOTO_BUCKET": "bucket-1", "SAFEPATH_APPSYNC_EVENTS_ENDPOINT": "https://h.example.com/event",
           "SAFEPATH_APPSYNC_API_KEY": "k", "SAFEPATH_COGNITO_USER_POOL_ID": POOL,
           "SAFEPATH_COGNITO_CLIENT_ID": CLIENT, "SAFEPATH_TOKEN_SECRET": "t" * 32}
    repo = make_repository(env)
    assert isinstance(repo, DynamoRepository) and repo.table_name("x") == "demo-x"
    assert isinstance(make_publisher(env), AppSyncEventsPublisher)
    store = make_blob_store(env)
    assert isinstance(store, S3BlobStore) and store.bucket == "bucket-1"
    auth = make_auth_provider(env)
    assert isinstance(auth, CognitoAuthProvider) and auth.issue_guest().principal.is_guest


def test_factory_per_port_overrides_and_fallback_publisher():
    env = {**AWS_ENV, "SAFEPATH_PUBLISHER": "apigw", "SAFEPATH_AUTH": "demo", "SAFEPATH_REPOSITORY": "memory",
           "SAFEPATH_WS_ENDPOINT": "https://abc.execute-api.us-east-1.amazonaws.com/prod",
           "SAFEPATH_TOKEN_SECRET": "t" * 32}
    assert isinstance(make_publisher(env), ApiGwWebSocketPublisher)
    assert isinstance(make_repository(env), MemoryRepository)
    assert isinstance(make_auth_provider(env), DemoTokenAuthProvider)
    local = {"SAFEPATH_PROFILE": "local", "SAFEPATH_REPOSITORY": "dynamo", "AWS_REGION": "us-east-1",
             "AWS_ENDPOINT_URL": "http://localhost:8000"}
    repo = make_repository(local)  # DynamoDB Local via AWS_ENDPOINT_URL
    assert repo._c.meta.endpoint_url == "http://localhost:8000"


def test_factory_reports_missing_configuration_as_adapter_not_available():
    for factory, extra in [(make_blob_store, {}), (make_publisher, {}), (make_auth_provider, {}),
                           (make_publisher, {"SAFEPATH_PUBLISHER": "apigw"})]:
        with pytest.raises(AdapterNotAvailable):
            factory({**AWS_ENV, **extra})
    with pytest.raises(AdapterNotAvailable, match="region"):
        make_repository({"SAFEPATH_PROFILE": "aws"})


# ============================================================================ S3 graph source
FIXTURE = ROOT / "tests" / "fixtures" / "graph"


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise client_error("NoSuchKey", "GetObject")

        class Body:
            def __init__(self, data):
                self._d = data

            def read(self):
                return self._d

        return {"Body": Body(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix, Delimiter, **kw):
        subs = sorted({k[len(Prefix):].split("/", 1)[0] for k in self.objects if k.startswith(Prefix) and "/" in k[len(Prefix):]})
        return {"CommonPrefixes": [{"Prefix": f"{Prefix}{s}/"} for s in subs]}


def _bucket_from_fixture(prefix="graphs", versions=("v1",), latest="v1") -> dict[str, bytes]:
    src = FIXTURE / "graphs"
    real = json.loads((src / "latest.json").read_text())["version"]
    objects: dict[str, bytes] = {f"{prefix}/latest.json": json.dumps({"version": latest}).encode()}
    for v in versions:
        for f in (src / real).rglob("*"):
            if f.is_file():
                objects[f"{prefix}/{v}/{f.relative_to(src / real).as_posix()}"] = f.read_bytes()
        m = json.loads(objects[f"{prefix}/{v}/manifest.json"])
        m["version"] = v
        objects[f"{prefix}/{v}/manifest.json"] = json.dumps(m).encode()
    return objects


def test_s3_graph_download_verifies_checksums_and_loads(tmp_path):
    from app.routing.graph import Graph

    objects = _bucket_from_fixture()
    assert download_graph(_FakeS3(objects), "bkt", tmp_path) == "v1"
    g = Graph.load(tmp_path)
    assert g.loaded and g.num_edges > 0 and g.status == "ok"


def test_s3_graph_falls_back_to_previous_version_on_checksum_failure(tmp_path):
    objects = _bucket_from_fixture(versions=("v1", "v2"), latest="v2")
    objects["graphs/v2/edges.jsonl.gz"] = gzip.compress(b"corrupted\n")
    assert download_graph(_FakeS3(objects), "bkt", tmp_path) == "v1"
    assert json.loads((tmp_path / "graphs" / "latest.json").read_text()) == {"version": "v1"}
    assert not (tmp_path / "graphs" / "v2").exists()
    broken = {k: (b"x" if k.endswith("nodes.jsonl.gz") else v) for k, v in objects.items()}
    with pytest.raises(GraphDownloadError):
        download_graph(_FakeS3(broken), "bkt", tmp_path / "other")
    with pytest.raises(GraphDownloadError):
        download_graph(_FakeS3({}), "bkt", tmp_path / "empty")


def test_s3_graph_rejects_unsafe_manifest_paths(tmp_path):
    objects = _bucket_from_fixture()
    m = json.loads(objects["graphs/v1/manifest.json"])
    m["sha256"]["../evil"] = hashlib.sha256(b"x").hexdigest()
    objects["graphs/v1/manifest.json"] = json.dumps(m).encode()
    objects["graphs/v1/../evil"] = b"x"
    with pytest.raises(GraphDownloadError):
        download_graph(_FakeS3(objects), "bkt", tmp_path)
    assert not (tmp_path.parent / "evil").exists()


# ============================================================================ Lambda entry points
def test_bootstrap_graph_sets_root_or_falls_back_without_raising(tmp_path):
    from app import lambda_handler as lh

    env = {"SAFEPATH_GRAPH_BUCKET": "bkt", "SAFEPATH_GRAPH_CACHE_DIR": str(tmp_path / "g"),
           "SAFEPATH_GRAPH_ROOT": "stale"}
    info = lh.bootstrap_graph(env, client=_FakeS3(_bucket_from_fixture()))
    assert info["source"] == "s3" and info["version"] == "v1" and info["seconds"] >= 0
    assert env["SAFEPATH_GRAPH_ROOT"] == str(tmp_path / "g")
    env2 = {"SAFEPATH_GRAPH_BUCKET": "bkt", "SAFEPATH_GRAPH_CACHE_DIR": str(tmp_path / "h"),
            "SAFEPATH_GRAPH_ROOT": "stale"}
    info2 = lh.bootstrap_graph(env2, client=_FakeS3({}))
    assert info2["source"] == "bundled" and info2["error"] and "SAFEPATH_GRAPH_ROOT" not in env2
    assert lh.bootstrap_graph({})["source"] == "unchanged"


def test_api_handler_serves_http_api_v2_events():
    from app import lambda_handler as lh

    api = lh.build_api_handler({})
    event = {"version": "2.0", "routeKey": "$default", "rawPath": "/api/health", "rawQueryString": "",
             "headers": {"host": "example.execute-api.us-east-1.amazonaws.com"}, "isBase64Encoded": False,
             "requestContext": {"stage": "$default", "http": {
                 "method": "GET", "path": "/api/health", "protocol": "HTTP/1.1", "sourceIp": "127.0.0.1",
                 "userAgent": "pytest"}}}
    resp = api(event, None)
    assert resp["statusCode"] == 200 and json.loads(resp["body"])["status"]


def test_sweeper_handler_runs_the_sweeper(monkeypatch):
    from app import lambda_handler as lh
    import app.services.sweeper as sweeper

    seen = {}

    def fake_run(now, *, app):
        seen["now"], seen["app"] = now, app
        return {"expired": 2, "when": now}

    monkeypatch.setattr(sweeper, "run_sweep", fake_run)
    out = lh.sweeper_handler({"time": "2026-03-01T12:00:00Z"}, None)
    assert seen["now"] == datetime(2026, 3, 1, 12, tzinfo=timezone.utc) and out["expired"] == 2
    assert lh.sweeper_handler({}, None)["expired"] == 2 and seen["now"] is None


def _ws_event(route, conn="conn-1", query=None):
    return {"requestContext": {"routeKey": route, "connectionId": conn}, "queryStringParameters": query}


def test_websocket_handler_connect_disconnect_default():
    from app import lambda_handler as lh

    repo = MemoryRepository()
    assert lh.websocket_handler(_ws_event("$connect", query={"incident_id": "inc_1"}), None, repo=repo,
                                clock=lambda: 1_000)["statusCode"] == 200
    row = repo.get("connections", {"pk": "inc_1", "sk": "conn-1"})
    assert row["ttl"] > 1_000
    assert lh.websocket_handler(_ws_event("$default"), None, repo=repo)["statusCode"] == 200
    assert lh.websocket_handler(_ws_event("$disconnect"), None, repo=repo)["statusCode"] == 200
    assert repo.get("connections", {"pk": "inc_1", "sk": "conn-1"}) is None
    assert repo.get("connections", {"pk": "conn#conn-1", "sk": "incident"}) is None
    for q in (None, {}, {"incident_id": "../../x"}, {"incident_id": "a" * 65}):
        assert lh.websocket_handler(_ws_event("$connect", query=q), None, repo=repo)["statusCode"] == 400
    assert lh.websocket_handler({"requestContext": {"routeKey": "$connect"}}, None, repo=repo)["statusCode"] == 400


def test_websocket_connect_validates_supplied_token_and_survives_store_outage():
    from app import lambda_handler as lh

    class Auth:
        def verify(self, token):
            if token != "good":
                raise Unauthenticated("bad")

    repo = MemoryRepository()
    q = {"incident_id": "inc_1", "token": "bad"}
    assert lh.websocket_handler(_ws_event("$connect", query=q), None, repo=repo, auth=Auth())["statusCode"] == 401
    assert repo.query("connections", {"pk": "inc_1"}) == []
    q["token"] = "good"
    assert lh.websocket_handler(_ws_event("$connect", query=q), None, repo=repo, auth=Auth())["statusCode"] == 200
    repo.available = False
    assert lh.websocket_handler(_ws_event("$connect", query={"incident_id": "inc_2"}), None,
                                repo=repo)["statusCode"] == 500


# ============================================================================ packaging
def _load_package_script():
    spec = importlib.util.spec_from_file_location("package_lambda", ROOT / "scripts" / "package_lambda.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_package_requirements_are_pinned_runtime_only():
    pkg = _load_package_script()
    reqs = pkg.collect_requirements()
    names = {pkg._name(r) for r in reqs}
    assert all("==" in r for r in reqs)
    assert {"fastapi", "mangum", "pydantic", "pyjwt", "cryptography", "shapely", "numpy"} <= names
    assert not names & {"uvicorn", "pytest", "hypothesis", "httpx", "osmnx", "geopandas", "pypdf",
                        "boto3", "botocore"}
    assert "boto3" in {pkg._name(r) for r in pkg.collect_requirements(include_boto=True)}


def test_package_zip_contains_app_data_and_entry_but_no_tests(tmp_path):
    pkg = _load_package_script()
    zpath = pkg.build(skip_deps=True, build_dir=tmp_path)
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
    assert "lambda_entry.py" in names and "backend/app/lambda_handler.py" in names
    assert any(n.startswith("data/sources/") for n in names)
    assert any(n.startswith("tests/fixtures/graph/") for n in names)  # SIMULATED fallback graph
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or "/test_" in n]
    assert not (tmp_path / "lambda-stage").exists()
