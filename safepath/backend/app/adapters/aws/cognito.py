"""CognitoAuthProvider: verify Cognito JWTs against the user pool JWKS (Req 22, 29.3, 31.4).

Verification (PyJWT + ``cryptography``, RS256 only): signature against the key whose ``kid`` the
token names, ``iss`` equal to the pool issuer, ``exp``/``iat`` valid, ``token_use`` id or access,
and the app client (``aud`` of an ID token, ``client_id`` of an access token) equal to the
configured client.

Roles are NEVER read from client-controllable data. They come from the verified token's
``cognito:groups`` (assigned by pool administrators) and from the admin ``roles.yaml`` mapping
(subject -> role); the higher privilege wins; everything else is a plain User. Custom attributes
such as ``custom:role`` and any ``role`` claim are ignored.

Guest sessions are not Cognito identities: an optional ``guest_provider`` (the HMAC demo token
provider) verifies and issues them (design D7). A token with three dot-separated parts is a JWT;
the demo format has one dot.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import jwt
from jwt import InvalidTokenError, PyJWK

from app.adapters.ports import (
    AdapterNotAvailable, AuthProvider, GuestSession, Principal, Role, Unauthenticated,
)

log = logging.getLogger("safepath.auth")

JWKS_TTL_SECONDS = 3600.0
JWKS_REFRESH_MIN_INTERVAL = 60.0  # at most one forced refresh per minute (unknown kid)
LEEWAY_SECONDS = 30

_RANK = {Role.GUEST: 0, Role.USER: 1, Role.MODERATOR: 2, Role.AUTHORIZED_OFFICIAL: 3}
DEFAULT_GROUPS: dict[str, Role] = {
    "moderator": Role.MODERATOR,
    "moderators": Role.MODERATOR,
    "authorizedofficial": Role.AUTHORIZED_OFFICIAL,
    "authorized_official": Role.AUTHORIZED_OFFICIAL,
    "authorizedofficials": Role.AUTHORIZED_OFFICIAL,
}

JwksFetcher = Callable[[], Mapping[str, Any]]


def issuer_for(region: str, pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"


def _http_jwks(url: str) -> Callable[[], Mapping[str, Any]]:
    def fetch() -> Mapping[str, Any]:
        if not url.startswith("https://"):
            raise ValueError("JWKS URL must be https")
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310 - https only
            return json.loads(resp.read().decode("utf-8"))

    return fetch


class CognitoAuthProvider:
    def __init__(self, *, region: str, user_pool_id: str, client_id: str,
                 jwks_fetcher: JwksFetcher | None = None,
                 group_roles: Mapping[str, Role] | None = None,
                 roles: Mapping[str, Role] | None = None,
                 guest_provider: AuthProvider | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        if not (region and user_pool_id and client_id):
            raise ValueError("region, user_pool_id and client_id are required")
        self.issuer = issuer_for(region, user_pool_id)
        self.client_id = client_id
        self._fetch = jwks_fetcher or _http_jwks(f"{self.issuer}/.well-known/jwks.json")
        self._groups = {k.lower(): v for k, v in (group_roles or DEFAULT_GROUPS).items()}
        self._roles = dict(roles or {})
        self._guest = guest_provider
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kw: Any) -> "CognitoAuthProvider":
        from app.adapters.aws.session import env_of, region_of

        e = env_of(env)
        pool = (e.get("SAFEPATH_COGNITO_USER_POOL_ID") or "").strip()
        client_id = (e.get("SAFEPATH_COGNITO_CLIENT_ID") or "").strip()
        if not pool or not client_id:
            raise AdapterNotAvailable(
                "Cognito needs SAFEPATH_COGNITO_USER_POOL_ID and SAFEPATH_COGNITO_CLIENT_ID; if "
                "Cognito is unavailable use the labeled substitute SAFEPATH_AUTH=demo")
        region = (e.get("SAFEPATH_COGNITO_REGION") or "").strip() or (
            pool.split("_", 1)[0] if "_" in pool else "") or (region_of(e) or "")
        if not region:
            raise AdapterNotAvailable("Cognito region could not be determined")
        groups = dict(DEFAULT_GROUPS)
        for env_key, role in (("SAFEPATH_COGNITO_GROUP_MODERATOR", Role.MODERATOR),
                              ("SAFEPATH_COGNITO_GROUP_OFFICIAL", Role.AUTHORIZED_OFFICIAL)):
            name = (e.get(env_key) or "").strip().lower()
            if name:
                groups[name] = role
        from app.auth.provider import DemoTokenAuthProvider, load_roles

        guest = kw.pop("guest_provider", None)
        secret = (e.get("SAFEPATH_TOKEN_SECRET") or "").strip()
        if guest is None and secret:
            guest = DemoTokenAuthProvider(secret, allow_ephemeral=False)
        roles_file = (e.get("SAFEPATH_ROLES_FILE") or "").strip()
        return cls(region=region, user_pool_id=pool, client_id=client_id, group_roles=groups,
                   roles=load_roles(roles_file) if roles_file else None, guest_provider=guest, **kw)

    # ------------------------------------------------------------------ JWKS
    def _load_keys(self, *, force: bool) -> None:
        now = self._clock()
        with self._lock:
            fresh = self._keys and now - self._fetched_at < JWKS_TTL_SECONDS
            if fresh and not force:
                return
            if force and self._keys and now - self._fetched_at < JWKS_REFRESH_MIN_INTERVAL:
                return
            try:
                doc = self._fetch()
                keys = {k["kid"]: k for k in doc.get("keys", []) if k.get("kid") and k.get("kty") == "RSA"}
            except Exception:
                log.exception("could not fetch Cognito JWKS")
                if self._keys:  # keep serving with the stale set
                    self._fetched_at = now - JWKS_TTL_SECONDS + JWKS_REFRESH_MIN_INTERVAL
                    return
                raise Unauthenticated("signing keys unavailable") from None
            self._keys = keys
            self._fetched_at = now

    def _key_for(self, kid: str) -> Any:
        self._load_keys(force=False)
        if kid not in self._keys:
            self._load_keys(force=True)
        jwk = self._keys.get(kid)
        if jwk is None:
            raise Unauthenticated("unknown signing key")
        return PyJWK(jwk).key

    # ------------------------------------------------------------------ port
    def verify(self, token: str | None) -> Principal:
        if not token or not isinstance(token, str):
            raise Unauthenticated("missing or malformed token")
        if token.count(".") != 2:
            if self._guest is not None:
                return self._guest.verify(token)
            raise Unauthenticated("malformed token")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise Unauthenticated("unsupported token header")
            key = self._key_for(str(header["kid"]))
            claims = jwt.decode(
                token, key, algorithms=["RS256"], issuer=self.issuer, leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "sub"], "verify_aud": False},
            )
        except Unauthenticated:
            raise
        except jwt.ExpiredSignatureError:
            raise Unauthenticated("token expired") from None
        except (InvalidTokenError, ValueError, KeyError, TypeError):
            raise Unauthenticated("invalid token") from None
        use = claims.get("token_use")
        if use == "id":
            ok = claims.get("aud") == self.client_id
        elif use == "access":
            ok = claims.get("client_id") == self.client_id
        else:
            ok = False
        if not ok:
            raise Unauthenticated("token was not issued for this application")
        subject = str(claims["sub"])
        expires = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
        if expires <= datetime.fromtimestamp(self._clock(), tz=timezone.utc):
            raise Unauthenticated("token expired")
        return Principal(subject=subject, role=self._resolve_role(subject, claims),
                         is_guest=False, expires_at=expires)

    def _resolve_role(self, subject: str, claims: Mapping[str, Any]) -> Role:
        best = Role.USER
        groups = claims.get("cognito:groups") or []
        if isinstance(groups, str):
            groups = [groups]
        candidates = [self._groups.get(str(g).lower()) for g in groups if isinstance(g, str)]
        candidates.append(self._roles.get(subject))
        for role in candidates:
            if role is not None and role is not Role.GUEST and _RANK[role] > _RANK[best]:
                best = role
        return best

    def issue_guest(self) -> GuestSession:
        if self._guest is None:
            raise AdapterNotAvailable(
                "guest sessions need SAFEPATH_TOKEN_SECRET (backend-signed demo tokens)")
        return self._guest.issue_guest()

    def role_for(self, subject: str) -> Role:
        role = self._roles.get(subject)
        return role if role is not None else Role.USER
