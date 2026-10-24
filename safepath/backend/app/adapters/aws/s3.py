"""S3BlobStore: presigned upload/download URLs for a private bucket (Req 23.5, 31, 32).

* The bucket is private (block public access is an infrastructure concern); every access goes
  through a short-lived SigV4 presigned URL.
* ``presign_put`` signs ``Content-Type`` and ``Content-Length`` (the exact byte size the client
  declared, which the caller has already bounded to ``max_bytes``), so S3 rejects an upload whose
  type or size differs from what was authorised. Valid for 15 minutes.
* ``presign_get`` never exceeds 15 minutes.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from app.adapters.ports import AdapterNotAvailable, PresignedUpload

PUT_TTL_SECONDS = 900
MAX_GET_TTL_SECONDS = 900
BUCKET_ENV_KEYS = ("SAFEPATH_PHOTO_BUCKET", "SAFEPATH_BLOB_BUCKET", "SAFEPATH_DATA_BUCKET")


def _valid_key(key: str) -> None:
    if not key or key.startswith(("/", "\\")) or "\\" in key or ".." in key.split("/"):
        raise ValueError(f"invalid blob key {key!r}")


class S3BlobStore:
    def __init__(self, bucket: str, client: Any, *, prefix: str = "",
                 clock: Callable[[], float] = time.time) -> None:
        if not bucket:
            raise ValueError("bucket is required")
        self.bucket = bucket
        self._c = client
        self.prefix = prefix
        self._clock = clock

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, client: Any = None) -> "S3BlobStore":
        from app.adapters.aws.session import env_of, make_client

        e = env_of(env)
        bucket = next((e[k].strip() for k in BUCKET_ENV_KEYS if (e.get(k) or "").strip()), "")
        if not bucket:
            raise AdapterNotAvailable(
                f"S3 blob store needs a bucket; set one of {', '.join(BUCKET_ENV_KEYS)}")
        if client is None:
            from botocore.config import Config

            client = make_client("s3", e, config=Config(
                signature_version="s3v4", s3={"addressing_style": "virtual"},
                retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=2, read_timeout=5))
        return cls(bucket, client, prefix=e.get("SAFEPATH_BLOB_PREFIX", ""))

    def _full(self, key: str) -> str:
        _valid_key(key)
        return f"{self.prefix}{key}"

    def presign_put(self, key: str, content_type: str, max_bytes: int) -> PresignedUpload:
        if not content_type or max_bytes < 1:
            raise ValueError("content_type and a positive max_bytes are required")
        url = self._c.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": self._full(key), "ContentType": content_type,
                    "ContentLength": int(max_bytes)},
            ExpiresIn=PUT_TTL_SECONDS, HttpMethod="PUT",
        )
        expires = datetime.fromtimestamp(int(self._clock()) + PUT_TTL_SECONDS, timezone.utc)
        return PresignedUpload(url=url, key=key, method="PUT",
                               headers={"Content-Type": content_type},
                               expires_at=expires, max_bytes=int(max_bytes))

    def presign_get(self, key: str, ttl_seconds: int = 900) -> str:
        ttl = max(1, min(int(ttl_seconds), MAX_GET_TTL_SECONDS))
        return self._c.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._full(key),
                    "ResponseCacheControl": "private, no-store"},
            ExpiresIn=ttl, HttpMethod="GET",
        )
