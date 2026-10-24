"""Shared boto3 client construction for the AWS adapters.

boto3 is an optional dependency (``pip install -e "backend[aws]"``). It is imported lazily so the
local profile never needs it. All configuration comes from an environment mapping (default
``os.environ``); nothing here contacts AWS until a client method is called.
"""
from __future__ import annotations

import os
from typing import Any, Mapping

from app.adapters.ports import AdapterNotAvailable

REGION_KEYS = ("SAFEPATH_AWS_REGION", "AWS_REGION", "AWS_DEFAULT_REGION")


def env_of(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def region_of(env: Mapping[str, str]) -> str | None:
    for k in REGION_KEYS:
        v = (env.get(k) or "").strip()
        if v:
            return v
    return None


def endpoint_of(env: Mapping[str, str], service: str) -> str | None:
    """Service-specific then global ``AWS_ENDPOINT_URL`` (DynamoDB Local, LocalStack)."""
    for k in (f"AWS_ENDPOINT_URL_{service.upper().replace('-', '_')}", "AWS_ENDPOINT_URL"):
        v = (env.get(k) or "").strip()
        if v:
            return v
    return None


def make_client(service: str, env: Mapping[str, str] | None = None, *, endpoint: bool = True,
                **overrides: Any) -> Any:
    """Create a boto3 client with short timeouts and standard retries (Lambda friendly)."""
    e = env_of(env)
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise AdapterNotAvailable(
            "boto3 is not installed; run: pip install -e \"backend[aws]\"") from exc
    region = overrides.pop("region_name", None) or region_of(e)
    if not region:
        raise AdapterNotAvailable(
            "AWS region is not configured; set AWS_REGION (or SAFEPATH_AWS_REGION)")
    config = overrides.pop("config", None) or Config(
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=2, read_timeout=5,
    )
    kwargs: dict[str, Any] = {"region_name": region, "config": config, **overrides}
    url = endpoint_of(e, service) if endpoint else None
    if url and "endpoint_url" not in kwargs:
        kwargs["endpoint_url"] = url
    return boto3.client(service, **kwargs)
