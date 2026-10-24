"""Select adapter implementations from SAFEPATH_PROFILE plus per-port overrides.

Per-port overrides: SAFEPATH_REPOSITORY (memory|sqlite|dynamo), SAFEPATH_PUBLISHER
(inprocess|appsync|apigw), SAFEPATH_AUTH (demo|cognito), SAFEPATH_BLOBSTORE (local|s3).
Defaults: local profile -> memory / inprocess / demo / local; aws -> dynamo / appsync / cognito / s3.
AWS implementations arrive in task 12.2 and currently raise AdapterNotAvailable.
"""
from __future__ import annotations

import os
from typing import Mapping

from app.adapters.blob import LocalBlobStore
from app.adapters.ports import AdapterNotAvailable, AuthProvider, BlobStore, RealtimePublisher, Repository
from app.adapters.publisher import InProcessPublisher
from app.adapters.repository import MemoryRepository, SqliteRepository

DEFAULTS: dict[str, dict[str, str]] = {
    "local": {"repository": "memory", "publisher": "inprocess", "auth": "demo", "blobstore": "local"},
    "aws": {"repository": "dynamo", "publisher": "appsync", "auth": "cognito", "blobstore": "s3"},
}
ENV_VARS = {
    "repository": "SAFEPATH_REPOSITORY",
    "publisher": "SAFEPATH_PUBLISHER",
    "auth": "SAFEPATH_AUTH",
    "blobstore": "SAFEPATH_BLOBSTORE",
}
VALID: dict[str, tuple[str, ...]] = {
    "repository": ("memory", "sqlite", "dynamo"),
    "publisher": ("inprocess", "appsync", "apigw"),
    "auth": ("demo", "cognito"),
    "blobstore": ("local", "s3"),
}


def _choice(port: str, env: Mapping[str, str]) -> str:
    profile = env.get("SAFEPATH_PROFILE", "local").strip().lower() or "local"
    if profile not in DEFAULTS:
        raise ValueError(f"SAFEPATH_PROFILE must be one of {tuple(DEFAULTS)}, got {profile!r}")
    value = env.get(ENV_VARS[port], "").strip().lower() or DEFAULTS[profile][port]
    if value not in VALID[port]:
        raise ValueError(f"{ENV_VARS[port]} must be one of {VALID[port]}, got {value!r}")
    return value


def _unavailable(port: str, name: str) -> AdapterNotAvailable:
    return AdapterNotAvailable(
        f"{port} implementation {name!r} is not available yet (AWS adapters arrive in task 12.2); "
        f"set SAFEPATH_PROFILE=local or override {ENV_VARS[port]}"
    )


def make_repository(env: Mapping[str, str] | None = None) -> Repository:
    e = os.environ if env is None else env
    name = _choice("repository", e)
    if name == "memory":
        return MemoryRepository()
    if name == "sqlite":
        return SqliteRepository(e.get("SAFEPATH_SQLITE_PATH", ":memory:"))
    raise _unavailable("repository", name)


def make_publisher(env: Mapping[str, str] | None = None) -> RealtimePublisher:
    e = os.environ if env is None else env
    name = _choice("publisher", e)
    if name == "inprocess":
        return InProcessPublisher()
    raise _unavailable("publisher", name)


def make_blob_store(env: Mapping[str, str] | None = None) -> BlobStore:
    e = os.environ if env is None else env
    name = _choice("blobstore", e)
    if name == "local":
        return LocalBlobStore(
            e.get("SAFEPATH_BLOB_DIR", "data/processed/blobs"),
            e.get("SAFEPATH_BLOB_SECRET", "safepath-local-dev-secret"),
        )
    raise _unavailable("blobstore", name)


def make_auth_provider(env: Mapping[str, str] | None = None) -> AuthProvider:
    e = os.environ if env is None else env
    name = _choice("auth", e)
    if name == "demo":
        from app.auth.provider import DemoTokenAuthProvider

        profile = e.get("SAFEPATH_PROFILE", "local").strip().lower() or "local"
        return DemoTokenAuthProvider(
            e.get("SAFEPATH_TOKEN_SECRET") or None,
            roles_path=e.get("SAFEPATH_ROLES_FILE") or None,
            allow_ephemeral=profile == "local",
        )
    raise _unavailable("auth", name)
