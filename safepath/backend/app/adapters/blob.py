"""Filesystem BlobStore with HMAC-signed, expiring local URLs."""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from app.adapters.ports import PresignedUpload

PUT_TTL_SECONDS = 900


class LocalBlobStore:
    def __init__(self, root: str | Path, secret: bytes | str, base_url: str = "/blobs",
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root).resolve()
        self._secret = secret.encode() if isinstance(secret, str) else secret
        self.base_url = base_url.rstrip("/")
        self._clock = clock

    # -- paths -------------------------------------------------------------
    def _path(self, key: str) -> Path:
        if not key or key.startswith(("/", "\\")) or "\\" in key or ".." in key.split("/"):
            raise ValueError(f"invalid blob key {key!r}")
        p = (self.root / key).resolve()
        if self.root != p and self.root not in p.parents:
            raise ValueError(f"invalid blob key {key!r}")
        return p

    def write(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def read(self, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.is_file() else None

    # -- signing -----------------------------------------------------------
    def _sig(self, op: str, key: str, exp: int, extra: str = "") -> str:
        msg = f"{op}\n{key}\n{exp}\n{extra}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def verify(self, op: str, key: str, exp: int, sig: str, extra: str = "") -> bool:
        """True if the signature matches and the URL has not expired."""
        if int(self._clock()) > exp:
            return False
        return hmac.compare_digest(self._sig(op, key, exp, extra), sig)

    def _expiry(self, ttl: int) -> int:
        return int(self._clock()) + ttl

    def presign_put(self, key: str, content_type: str, max_bytes: int) -> PresignedUpload:
        self._path(key)
        exp = self._expiry(PUT_TTL_SECONDS)
        extra = f"{content_type}|{max_bytes}"
        sig = self._sig("put", key, exp, extra)
        url = (f"{self.base_url}/{quote(key)}?op=put&exp={exp}&max={max_bytes}"
               f"&ct={quote(content_type, safe='')}&sig={sig}")
        return PresignedUpload(
            url=url, key=key, method="PUT", headers={"Content-Type": content_type},
            expires_at=datetime.fromtimestamp(exp, timezone.utc), max_bytes=max_bytes,
        )

    def presign_get(self, key: str, ttl_seconds: int = 900) -> str:
        self._path(key)
        exp = self._expiry(ttl_seconds)
        return f"{self.base_url}/{quote(key)}?op=get&exp={exp}&sig={self._sig('get', key, exp)}"
