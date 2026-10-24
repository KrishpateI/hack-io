"""AWS Lambda entry point for the Sweeper (EventBridge rule, every minute)."""
from __future__ import annotations

from typing import Any

from app.services.sweeper import run_sweep


def sweeper_handler(event: Any = None, context: Any = None) -> dict[str, Any]:
    """Run one sweep and return its summary (also logged by CloudWatch via the return value)."""
    from app.main import app  # built once per container; shares the API's adapters

    return run_sweep(None, app=app)
