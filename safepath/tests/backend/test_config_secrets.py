"""Secret ARN resolution (config_secrets) and stable guest aliases. No network: botocore Stubber."""
from __future__ import annotations

import boto3
import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient

from app import config_secrets as cs
from app.models.privacy import guest_alias

ARN_T = "arn:aws:secretsmanager:us-east-1:123456789012:secret:safepath-token-AbCdEf"
ARN_A = "arn:aws:secretsmanager:us-east-1:123456789012:secret:safepath-alias-AbCdEf"


@pytest.fixture(autouse=True)
def _clean():
    cs.clear_cache()
    yield
    cs.clear_cache()


def _client():
    c = boto3.client("secretsmanager", region_name="us-east-1",
                     aws_access_key_id="x", aws_secret_access_key="y")
    return c, Stubber(c)


def test_resolves_from_arn_for_aws_profile_and_caches():
    c, st = _client()
    st.add_response("get_secret_value", {"SecretString": "tok-value"}, {"SecretId": ARN_T})
    st.add_response("get_secret_value", {"SecretString": "alias-value"}, {"SecretId": ARN_A})
    env = {"SAFEPATH_PROFILE": "aws", "SAFEPATH_TOKEN_SECRET_ARN": ARN_T, "SAFEPATH_ALIAS_SECRET_ARN": ARN_A}
    with st:
        assert cs.resolve_secrets(env, client=c) == ["SAFEPATH_TOKEN_SECRET", "SAFEPATH_ALIAS_SECRET"]
        assert env["SAFEPATH_TOKEN_SECRET"] == "tok-value" and env["SAFEPATH_ALIAS_SECRET"] == "alias-value"
        env2 = {"SAFEPATH_PROFILE": "aws", "SAFEPATH_TOKEN_SECRET_ARN": ARN_T}
        cs.resolve_secrets(env2, client=c)  # cached: the stub has no more responses
        assert env2["SAFEPATH_TOKEN_SECRET"] == "tok-value"
        st.assert_no_pending_responses()


def test_plain_env_wins_and_local_profile_is_noop():
    c, st = _client()
    with st:  # any call would fail: no responses queued
        env = {"SAFEPATH_PROFILE": "aws", "SAFEPATH_TOKEN_SECRET": "plain", "SAFEPATH_TOKEN_SECRET_ARN": ARN_T}
        assert cs.resolve_secrets(env, client=c) == []
        assert env["SAFEPATH_TOKEN_SECRET"] == "plain"
        local = {"SAFEPATH_PROFILE": "local", "SAFEPATH_TOKEN_SECRET_ARN": ARN_T}
        assert cs.resolve_secrets(local, client=c) == []
        assert "SAFEPATH_TOKEN_SECRET" not in local


def test_failure_is_clear_and_never_leaks_values():
    c, st = _client()
    st.add_client_error("get_secret_value", service_error_code="AccessDeniedException")
    env = {"SAFEPATH_PROFILE": "aws", "SAFEPATH_TOKEN_SECRET_ARN": ARN_T}
    with st, pytest.raises(cs.SecretResolutionError) as ei:
        cs.resolve_secrets(env, client=c)
    assert "SAFEPATH_TOKEN_SECRET" in str(ei.value) and "AccessDeniedException" in str(ei.value)
    assert "SAFEPATH_TOKEN_SECRET" not in env


def test_empty_secret_string_is_an_error():
    c, st = _client()
    st.add_response("get_secret_value", {"SecretBinary": b"abc"}, {"SecretId": ARN_T})
    with st, pytest.raises(cs.SecretResolutionError):
        cs.resolve_secrets({"SAFEPATH_PROFILE": "aws", "SAFEPATH_TOKEN_SECRET_ARN": ARN_T}, client=c)


def test_guest_alias_is_stable_and_secret_dependent():
    a = guest_alias("s1", "guest_abc")
    assert a == guest_alias("s1", "guest_abc")
    assert a.startswith("Guest-") and len(a) == len("Guest-ab12")
    assert guest_alias("s2", "guest_abc") != a or guest_alias("s2", "guest_other") != guest_alias("s1", "guest_other")


def test_guest_endpoints_return_same_alias():
    from app.main import create_app

    with TestClient(create_app()) as client:
        g = client.post("/api/auth/guest").json()
        assert g["display_name"].startswith("Guest-") and g["alias"] == g["display_name"]
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {g['token']}"}).json()
        assert me["display_name"] == g["display_name"] and me["alias"] == g["alias"]
