"""Local-profile accounts: registration, login, privacy preferences (Req 22.1, 22.6).

Passwords are hashed with hashlib.scrypt (per-user random salt) and kept in a separate
``credentials`` table; they never appear in ``UserRecord`` or any view. Users live in table
``users`` (pk = user_id).
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Callable

from app.adapters.ports import Conflict, Repository, Role, Unauthenticated
from app.models.ids import new_id
from app.models.records import PrivacyPreferences, UserRecord

USERS_TABLE = "users"
CREDENTIALS_TABLE = "credentials"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
_DUMMY_SALT = b"\0" * 16


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=64 * 1024 * 1024, **_SCRYPT)


def _strip(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("pk", "sk")}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AccountService:
    def __init__(self, repo: Repository, clock: Callable[[], datetime] = _now):
        self.repo = repo
        self._clock = clock

    def _key(self, user_id: str) -> dict:
        return {"pk": user_id, "sk": ""}

    def get_user(self, user_id: str) -> UserRecord | None:
        row = self.repo.get(USERS_TABLE, self._key(user_id))
        return UserRecord.model_validate(_strip(row)) if row else None

    def ensure_user(self, user_id: str, role: Role, display_name: str | None = None) -> UserRecord:
        """Return the stored account, creating it (e.g. for a guest) with default preferences."""
        existing = self.get_user(user_id)
        if existing is not None:
            return existing
        rec = UserRecord(user_id=user_id, display_name=display_name, role=role.value,
                         created_at=self._clock(), alias_seed=os.urandom(8).hex())
        try:
            self.repo.put(USERS_TABLE, {**rec.model_dump(mode="json"), **self._key(user_id)},
                          if_absent=True)
        except Conflict:
            return self.get_user(user_id) or rec
        return rec

    def register(self, display_name: str | None, password: str) -> UserRecord:
        user_id = new_id("user")
        salt = os.urandom(16)
        self.repo.put(CREDENTIALS_TABLE, {**self._key(user_id), "salt": salt.hex(),
                                          "hash": hash_password(password, salt).hex()},
                      if_absent=True)
        return self.ensure_user(user_id, Role.USER, display_name)

    def authenticate(self, user_id: str, password: str) -> UserRecord:
        row = self.repo.get(CREDENTIALS_TABLE, self._key(user_id))
        salt = bytes.fromhex(row["salt"]) if row else _DUMMY_SALT
        candidate = hash_password(password, salt)
        expected = bytes.fromhex(row["hash"]) if row else b"\1" * 32
        if row is None or not hmac.compare_digest(candidate, expected):
            raise Unauthenticated("invalid credentials")
        user = self.get_user(user_id)
        if user is None:
            raise Unauthenticated("invalid credentials")
        return user

    def set_share_location(self, user_id: str, role: Role, share: bool) -> UserRecord:
        user = self.ensure_user(user_id, role)
        updated = user.model_copy(update={"privacy_preferences": PrivacyPreferences(share_location=share)})
        self.repo.put(USERS_TABLE, {**updated.model_dump(mode="json"), **self._key(user_id)})
        return updated
