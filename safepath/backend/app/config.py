"""Application settings, driven by the SAFEPATH_PROFILE environment variable."""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict

Profile = Literal["local", "aws"]
VALID_PROFILES: tuple[str, ...] = ("local", "aws")
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")


class Settings(BaseModel):
    """Immutable runtime settings. ``profile`` selects local or AWS adapters."""

    model_config = ConfigDict(frozen=True)

    profile: Profile = "local"
    graph_version: str = "unbuilt"
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    max_body_bytes: int = 1_000_000

    @property
    def cors_allow_credentials(self) -> bool:
        """Credentials are never combined with a wildcard origin."""
        return "*" not in self.cors_origins

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        profile = e.get("SAFEPATH_PROFILE", "local").strip().lower() or "local"
        if profile not in VALID_PROFILES:
            raise ValueError(
                f"SAFEPATH_PROFILE must be one of {VALID_PROFILES}, got {profile!r}"
            )
        return cls(
            profile=profile,  # type: ignore[arg-type]
            graph_version=e.get("SAFEPATH_GRAPH_VERSION", "unbuilt"),
            cors_origins=_parse_origins(e.get("SAFEPATH_CORS_ORIGINS")),
        )


def _parse_origins(raw: str | None) -> tuple[str, ...]:
    if raw is None or not raw.strip():
        return DEFAULT_CORS_ORIGINS
    return tuple(o.strip().rstrip("/") for o in raw.split(",") if o.strip())
