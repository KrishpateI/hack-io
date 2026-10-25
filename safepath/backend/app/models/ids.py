"""Prefixed ULID identifiers, unique per record type (no external dependency)."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")

# One prefix per persistent record type.
PREFIXES: dict[str, str] = {
    "user": "usr",
    "incident": "inc",
    "report": "rpt",
    "vote": "vot",
    "message": "msg",
    "checkin": "chk",
    "notice": "ntc",
    "overlay": "ovl",
    "event": "evt",
    "audit": "aud",
    "ratelimit": "rlm",
}
assert len(set(PREFIXES.values())) == len(PREFIXES)

NS_REPORT = uuid.UUID("5a3f7c2e-8b1d-4e5a-9c63-2f0d7a1b4c88")

_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_ulid(now_ms: int | None = None) -> str:
    """26-char ULID. Monotonic within a process: same-ms calls increment the random part."""
    global _last_ms, _last_rand
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    with _lock:
        if ms <= _last_ms:
            ms = _last_ms
            _last_rand += 1
            if _last_rand >= 1 << 80:  # overflow: move to next millisecond
                ms += 1
                _last_rand = int.from_bytes(os.urandom(10), "big")
        else:
            _last_rand = int.from_bytes(os.urandom(10), "big")
        _last_ms = ms
        rand = _last_rand
    return _encode(ms, 10) + _encode(rand, 16)


def new_id(record_type: str, now_ms: int | None = None) -> str:
    """Return ``<prefix>_<ULID>`` for a record type (KeyError on unknown type)."""
    return f"{PREFIXES[record_type]}_{new_ulid(now_ms)}"


def record_type_of(identifier: str) -> str | None:
    """Return the record type for a well-formed prefixed id, else None."""
    prefix, sep, ulid = identifier.partition("_")
    if not sep or not _ULID_RE.match(ulid):
        return None
    for rtype, p in PREFIXES.items():
        if p == prefix:
            return rtype
    return None


def report_id_for(reporter_id: str, client_report_id: str | None) -> str:
    """Deterministic ``rpt_`` id (uuid5) when the client supplies an idempotency key (41.6)."""
    if client_report_id is None:
        return new_id("report")
    # JSON-encode the pair so the boundary is unambiguous: ("a", "bc") != ("ab", "c").
    name = json.dumps([reporter_id, client_report_id], ensure_ascii=False, separators=(",", ":"))
    return f"{PREFIXES['report']}_{uuid.uuid5(NS_REPORT, name)}"
