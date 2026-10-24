"""Router registry: auto-includes every module in ``app.api.routers``.

A router module only needs to expose a module-level ``router`` (``APIRouter``).
Later tasks add files to ``routers/`` and never edit ``main.py``.
"""
from __future__ import annotations

import importlib
import pkgutil

from fastapi import APIRouter, FastAPI

from app.api import routers as routers_pkg


def discover_routers() -> list[APIRouter]:
    found: list[APIRouter] = []
    for info in sorted(pkgutil.iter_modules(routers_pkg.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{routers_pkg.__name__}.{info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            found.append(router)
    return found


def include_all_routers(app: FastAPI) -> None:
    for router in discover_routers():
        app.include_router(router)
