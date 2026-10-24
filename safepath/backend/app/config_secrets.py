"""Resolve secrets passed by ARN (AWS profile only) into the plain env vars the backend reads.

The CDK ApiStack sets ``SAFEPATH_TOKEN_SECRET_ARN`` / ``SAFEPATH_ALIAS_SECRET_ARN`` so no secret value
lives in the Lambda configuration. At start-up this module fetches each value from Secrets Manager
and sets ``SAFEPATH_TOKEN_SECRET`` / ``SAFEPATH_ALIAS_SECRET`` in-process. Values are cached, never
logged and never included in error messages.
"""
from __future__ import annotations

import logging
import os
from typing import Any, MutableMapping

log = logging.getLogger("safepath.secrets")

SECRET_ENV_PAIRS: tuple[tuple[str, str], ...] = (
    ("SAFEPATH_TOKEN_SECRET", "SAFEPATH_TOKEN_SECRET_ARN"),
    ("SAFEPATH_ALIAS_SECRET", "SAFEPATH_ALIAS_SECRET_ARN"),
)

_cache: dict[str, str] = {}


class SecretResolutionError(RuntimeError):
    """A secret could not be fetched. The message names the env var and ARN, never a value."""


def _client(env: MutableMapping[str, str]) -> Any:
    try:
        import boto3  # lazy: only the aws profile needs it
    except ImportError as exc:
        raise SecretResolutionError(
            "boto3 is required to resolve *_ARN secrets (install the 'aws' extra)") from exc
    region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION")
    return boto3.client("secretsmanager", region_name=region) if region else boto3.client("secretsmanager")


def _fetch(arn: str, client: Any, var: str) -> str:
    if arn in _cache:
        return _cache[arn]
    try:
        resp = client.get_secret_value(SecretId=arn)
    except Exception as exc:  # botocore ClientError and friends; do not echo response bodies
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        raise SecretResolutionError(
            f"could not read secret for {var} from Secrets Manager ({code}); check {var}_ARN and the "
            "function role's secretsmanager:GetSecretValue permission") from None
    value = resp.get("SecretString")
    if not value:
        raise SecretResolutionError(f"secret referenced by {var}_ARN has no SecretString value")
    _cache[arn] = value
    return value


def resolve_secrets(env: MutableMapping[str, str] | None = None, *, client: Any = None) -> list[str]:
    """Populate missing secret env vars from their ``*_ARN`` twins. Returns the names set.

    No-op unless ``SAFEPATH_PROFILE`` is ``aws``. A plain env var always wins over the ARN.
    """
    e = os.environ if env is None else env
    if (e.get("SAFEPATH_PROFILE", "local").strip().lower() or "local") != "aws":
        return []
    set_names: list[str] = []
    for var, arn_var in SECRET_ENV_PAIRS:
        arn = (e.get(arn_var) or "").strip()
        if e.get(var) or not arn:
            continue
        if client is None:
            client = _client(e)
        e[var] = _fetch(arn, client, var)
        set_names.append(var)
        log.info("resolved %s from Secrets Manager", var)
    return set_names


def clear_cache() -> None:
    _cache.clear()
