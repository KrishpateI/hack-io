"""Process-wide access to the loaded runtime Graph, cached on ``app.state.graph``."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request

from app.routing.graph import FALLBACK, Graph

GRAPH_ROOT_ENV = "SAFEPATH_GRAPH_ROOT"
DEFAULT_GRAPH_ROOT = Path(__file__).resolve().parents[3] / "data" / "processed"


def graph_root() -> Path:
    return Path(os.environ.get(GRAPH_ROOT_ENV) or DEFAULT_GRAPH_ROOT)


FIXTURE_GRAPH_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "graph"
FIXTURE_NOTICE = (
    "SIMULATED fixture graph in use (synthetic data, not real streets): no real graph artifact "
    "was found. See data/processed/fallbacks.json."
)


def _load(app: FastAPI) -> Graph:
    """Load the configured graph. Only when SAFEPATH_GRAPH_ROOT is unset and the default root
    has no loadable artifact, fall back to the SIMULATED fixture, clearly reported (7.4, 45.6)."""
    g = Graph.load(graph_root())
    app.state.graph_fallback = None
    if not os.environ.get(GRAPH_ROOT_ENV) and not g.loaded and FIXTURE_GRAPH_ROOT.is_dir():
        fx = Graph.load(FIXTURE_GRAPH_ROOT)
        if fx.loaded:
            fx.status = FALLBACK
            fx.warnings = [FIXTURE_NOTICE, *g.warnings, *fx.warnings]
            app.state.graph_fallback = {
                "simulated": True,
                "classification": "Simulated",
                "notice": FIXTURE_NOTICE,
            }
            return fx
    return g


def get_graph(app: FastAPI) -> Graph:
    """Return the cached Graph, loading it on first use (set ``app.state.graph`` to override)."""
    g = getattr(app.state, "graph", None)
    if g is None:
        g = _load(app)
        app.state.graph = g
    return g


def reload_graph(app: FastAPI) -> Graph:
    app.state.graph = _load(app)
    return app.state.graph


def graph_dependency(request: Request) -> Graph:
    return get_graph(request.app)
