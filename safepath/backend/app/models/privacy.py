"""Anonymous reporter aliases (HMAC) and coarse location helpers."""
from __future__ import annotations

import hashlib
import hmac
import math
import os
from typing import Mapping

ALIAS_SECRET_ENV = "SAFEPATH_ALIAS_SECRET"
MIN_GRID_M = 200.0
_M_PER_DEG_LAT = 111_320.0

_ADJ = (
    "Amber", "Brisk", "Calm", "Daring", "Eager", "Fleet", "Gentle", "Hardy",
    "Ivory", "Jolly", "Keen", "Lucky", "Mellow", "Noble", "Quiet", "Rapid",
    "Steady", "Tidy", "Upbeat", "Vivid", "Witty", "Zesty", "Bold", "Clever",
    "Dusty", "Early", "Frosty", "Golden", "Honest", "Lively", "Misty", "Nimble",
)
_NOUN = (
    "Otter", "Falcon", "Maple", "Harbor", "Comet", "Willow", "Badger", "Cedar",
    "Dolphin", "Ember", "Fox", "Garnet", "Heron", "Iris", "Juniper", "Kestrel",
    "Lynx", "Meadow", "Newt", "Orchid", "Panda", "Quail", "Robin", "Sparrow",
    "Tulip", "Umber", "Violet", "Wren", "Yarrow", "Zephyr", "Birch", "Pebble",
)


def load_alias_secret(env: Mapping[str, str] | None = None) -> bytes:
    """Read the alias secret from the environment; never hardcoded. Raises if unset."""
    e = os.environ if env is None else env
    value = e.get(ALIAS_SECRET_ENV, "")
    if not value:
        raise RuntimeError(f"{ALIAS_SECRET_ENV} is not set")
    return value.encode("utf-8")


def reporter_alias(secret: bytes | str, user_id: str, incident_id: str) -> str:
    """Word-pair alias from HMAC(secret, user_id, incident_id); reveals nothing about the user."""
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not key:
        raise ValueError("alias secret must not be empty")
    msg = f"{len(user_id)}:{user_id}|{incident_id}".encode("utf-8")
    d = hmac.new(key, msg, hashlib.sha256).digest()
    n = int.from_bytes(d[:4], "big")
    return f"{_ADJ[d[4] % len(_ADJ)]} {_NOUN[d[5] % len(_NOUN)]} {n % 1000:03d}"


def coarsen(lat: float, lon: float, grid_m: float = MIN_GRID_M) -> tuple[float, float]:
    """Snap to a grid of at least ``MIN_GRID_M`` metres (31.8). Idempotent."""
    grid_m = max(grid_m, MIN_GRID_M)
    lat_step = grid_m / _M_PER_DEG_LAT
    clat = round(round(lat / lat_step) * lat_step, 6)
    lon_step = grid_m / (_M_PER_DEG_LAT * max(math.cos(math.radians(clat)), 0.01))
    clon = round(round(lon / lon_step) * lon_step, 6)
    return clat, clon
