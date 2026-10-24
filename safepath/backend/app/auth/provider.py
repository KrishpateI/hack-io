"""DemoTokenAuthProvider: HMAC-signed bearer tokens for the local profile (Req 22, 31.4).

Token format: ``base64url(json payload) "." hex(HMAC-SHA256)``. The payload holds only the
subject, a guest/user kind and the expiry. The role is NEVER read from the token or any
client-supplied value: it comes from the admin ``roles.yaml`` (subject -> role) and defaults to
Guest for guest sessions and User for registered accounts (Req 22.3).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

import yaml

from app.adapters.ports import GuestSession, Principal, Role, Unauthenticated
from app.models.ids import new_id

log = logging.getLogger("safepath.auth")

GUEST_TTL = timedelta(hours=24)
USER_TTL = timedelta(hours=24)
PRIVILEGED_ROLES = frozenset({Role.MODERATOR, Role.AUTHORIZED_OFFICIAL})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def load_roles(path: str | Path | None) -> dict[str, Role]:
    """Parse ``roles.yaml``: ``users: {<subject>: <role>}``. Missing path/file -> no assignments."""
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        log.warning("roles file %s not found; no privileged roles assigned", p)
        return {}
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    users = doc.get("users", doc) if isinstance(doc, dict) else {}
    out: dict[str, Role] = {}
    for subject, role in (users or {}).items():
        try:
            out[str(subject)] = Role(str(role).strip().lower())
        except ValueError:
            raise ValueError(f"roles file {p}: unknown role {role!r} for {subject!r}") from None
    return out


class DemoTokenAuthProvider:
    def __init__(self, secret: str | bytes | None = None, *,
                 roles: Mapping[str, Role] | None = None, roles_path: str | Path | None = None,
                 clock: Callable[[], datetime] = _now, allow_ephemeral: bool = True):
        if secret is None or secret == "" or secret == b"":
            if not allow_ephemeral:
                raise ValueError("SAFEPATH_TOKEN_SECRET is required")
            secret = secrets.token_bytes(32)
            log.warning("SAFEPATH_TOKEN_SECRET not set; using an ephemeral random secret "
                        "(tokens become invalid on restart)")
        self._secret = secret.encode() if isinstance(secret, str) else bytes(secret)
        self._clock = clock
        self._roles_path = Path(roles_path) if roles_path else None
        self._static_roles = dict(roles or {})
        self._roles_mtime: float | None = None
        self._file_roles: dict[str, Role] = {}

    # -- roles (admin config only) --------------------------------------------------
    def _reload_roles(self) -> None:
        if self._roles_path is None:
            return
        try:
            mtime = self._roles_path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._roles_mtime:
            self._file_roles = load_roles(self._roles_path)
            self._roles_mtime = mtime

    def role_for(self, subject: str, *, is_guest: bool = False) -> Role:
        self._reload_roles()
        assigned = {**self._file_roles, **self._static_roles}.get(subject)
        if assigned is not None:
            return assigned
        return Role.GUEST if is_guest or subject.startswith("guest_") else Role.USER

    # -- tokens -----------------------------------------------------------------------
    def _sign(self, body: str) -> str:
        return hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).hexdigest()

    def issue_token(self, subject: str, *, is_guest: bool = False,
                    ttl: timedelta | None = None) -> tuple[str, datetime]:
        expires = self._clock() + (ttl or (GUEST_TTL if is_guest else USER_TTL))
        payload = {"sub": subject, "kind": "guest" if is_guest else "user",
                   "exp": int(expires.timestamp())}
        body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        return f"{body}.{self._sign(body)}", expires

    def issue_guest(self) -> GuestSession:
        subject = new_id("user").replace("usr_", "guest_", 1)
        token, expires = self.issue_token(subject, is_guest=True, ttl=GUEST_TTL)
        principal = Principal(subject=subject, role=Role.GUEST, is_guest=True, expires_at=expires)
        return GuestSession(token=token, principal=principal, expires_at=expires)

    def verify(self, token: str | None) -> Principal:
        if not token or not isinstance(token, str) or token.count(".") != 1:
            raise Unauthenticated("missing or malformed token")
        body, _, sig = token.partition(".")
        try:
            if not hmac.compare_digest(sig.encode("ascii"), self._sign(body).encode("ascii")):
                raise Unauthenticated("invalid token signature")
            payload = json.loads(_b64d(body))
            subject, exp, kind = str(payload["sub"]), int(payload["exp"]), payload.get("kind")
        except Unauthenticated:
            raise
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise Unauthenticated("malformed token") from None
        expires = datetime.fromtimestamp(exp, tz=timezone.utc)
        if self._clock() >= expires:
            raise Unauthenticated("token expired")
        is_guest = kind == "guest"
        role = self.role_for(subject, is_guest=is_guest)
        return Principal(subject=subject, role=role, is_guest=is_guest and role is Role.GUEST,
                         expires_at=expires)


def secret_from_env(env: Mapping[str, str] | None = None) -> str | None:
    e = os.environ if env is None else env
    return e.get("SAFEPATH_TOKEN_SECRET") or None
