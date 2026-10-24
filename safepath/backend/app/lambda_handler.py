"""AWS Lambda entry points (Req 29.3, 30.7, 25.4).

* ``handler``           API Gateway HTTP API (payload v2) -> FastAPI via Mangum.
* ``sweeper_handler``   EventBridge schedule -> ``app.services.sweeper.run_sweep``.
* ``websocket_handler`` API Gateway WebSocket ``$connect`` / ``$disconnect`` / ``$default``
                        (the labeled fallback when AppSync Events is unavailable).

Cold start of the API function: when ``SAFEPATH_GRAPH_BUCKET`` is set, the newest verified
``<SAFEPATH_GRAPH_PREFIX>/<version>/`` graph is downloaded into ``/tmp`` (checksums verified) and
``SAFEPATH_GRAPH_ROOT`` points at it. If that fails, the data bundled in the package
(``data/processed`` then the SIMULATED fixture graph, see ``scripts/package_lambda.py``) is used
and reported as a fallback; the load time is logged.

Memory: ``Graph.load`` of the real downtown graph (~61k nodes) peaked around 0.9-1.0 GB
resident; configure the API function with 3008 MB (see docs/aws-deployment.md).
Set ``SAFEPATH_LAMBDA_EAGER_INIT=1`` on the API function to do this work in the INIT phase.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

log = logging.getLogger("safepath.lambda")
log.setLevel(logging.INFO)

GRAPH_ROOT_ENV = "SAFEPATH_GRAPH_ROOT"
_INCIDENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# --------------------------------------------------------------------------- graph bootstrap
def bootstrap_graph(env: dict[str, str] | Any = None, *, client: Any = None) -> dict[str, Any]:
    """Point ``SAFEPATH_GRAPH_ROOT`` at the downloaded graph. Never raises.

    On failure ``SAFEPATH_GRAPH_ROOT`` stays unset so the runtime uses the bundled data.
    """
    e = os.environ if env is None else env
    info: dict[str, Any] = {"source": "unchanged", "version": None, "seconds": 0.0, "error": None}
    if not (e.get("SAFEPATH_GRAPH_BUCKET") or "").strip():
        return info
    started = time.perf_counter()
    try:
        from app.adapters.aws.graph_source import load_graph_from_s3

        res = load_graph_from_s3(e, client)
        e[GRAPH_ROOT_ENV] = str(res["root"])
        info.update(source="s3", version=res["version"])
    except Exception as exc:  # fall back to bundled data; the app reports the fallback
        info["error"] = f"{type(exc).__name__}: {exc}"
        log.error("graph download failed, falling back to bundled data: %s", info["error"])
        e.pop(GRAPH_ROOT_ENV, None)
        info["source"] = "bundled"
    info["seconds"] = round(time.perf_counter() - started, 3)
    log.info("graph bootstrap: %s", json.dumps(info, default=str))
    return info


def _mark_fallback(app: Any, info: Mapping[str, Any]) -> None:
    """Load the graph now and label it when the bundled fallback is in use."""
    from app.routing.graph import FALLBACK
    from app.routing.runtime import get_graph

    started = time.perf_counter()
    g = get_graph(app)
    log.info("graph loaded in %.2fs (status=%s, nodes=%s, edges=%s)",
             time.perf_counter() - started, g.status, g.num_nodes, g.num_edges)
    if info.get("source") == "bundled" and g.loaded and getattr(app.state, "graph_fallback", None) is None:
        notice = ("Bundled fallback graph in use: the S3 graph could not be loaded "
                  f"({info.get('error')}). Data may be SIMULATED or outdated.")
        g.status = FALLBACK
        g.warnings = [notice, *g.warnings]
        app.state.graph_fallback = {"simulated": True, "classification": "Simulated", "notice": notice}


def build_api_handler(env: dict[str, str] | Any = None, client: Any = None) -> Callable[..., Any]:
    from mangum import Mangum

    info = bootstrap_graph(env, client=client)
    from app.main import app  # imported after the graph root is configured

    if info["source"] != "unchanged" or (os.environ if env is None else env).get("SAFEPATH_LAMBDA_EAGER_INIT"):
        try:
            _mark_fallback(app, info)
        except Exception:
            log.exception("graph pre-load failed; it will be retried on first use")
    return Mangum(app, lifespan="off")


_api: Callable[..., Any] | None = None


def handler(event: dict, context: Any = None) -> Any:
    """API Gateway HTTP API entry point."""
    global _api
    if _api is None:
        _api = build_api_handler()
    return _api(event, context)


if os.environ.get("SAFEPATH_LAMBDA_EAGER_INIT") == "1":  # pragma: no cover - Lambda INIT phase
    _api = build_api_handler()


# --------------------------------------------------------------------------- sweeper
def sweeper_handler(event: Mapping[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """EventBridge scheduled rule (every minute). The sweeper is idempotent."""
    from app.main import app
    from app.services.sweeper import run_sweep

    now = None
    raw = (event or {}).get("time")
    if isinstance(raw, str):
        try:
            now = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        except ValueError:
            now = None
    summary = run_sweep(now, app=app)
    log.info("sweep summary: %s", json.dumps(summary, default=str))
    return json.loads(json.dumps(summary, default=str))


# --------------------------------------------------------------------------- websocket fallback
_ws_repo: Any = None


def _repo() -> Any:
    global _ws_repo
    if _ws_repo is None:
        from app.adapters.factory import make_repository

        _ws_repo = make_repository()
    return _ws_repo


def _response(status: int, body: str = "") -> dict[str, Any]:
    return {"statusCode": status, "body": body}


def websocket_handler(event: Mapping[str, Any], context: Any = None, *, repo: Any = None,
                      auth: Any = None, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Route ``$connect`` / ``$disconnect`` / ``$default`` for the WebSocket fallback.

    ``$connect?incident_id=<id>[&token=<bearer>]`` registers the connection for that incident.
    Incident streams carry public views only, so a token is optional; if one is supplied it
    must verify (401 otherwise). Never raises: an unexpected failure returns 500.
    """
    from app.adapters.aws.publishers import register_connection, unregister_connection
    from app.adapters.ports import Unauthenticated

    ctx = event.get("requestContext") or {}
    route, conn = ctx.get("routeKey"), ctx.get("connectionId")
    if not conn:
        return _response(400, "missing connection id")
    try:
        repo = repo or _repo()
        if route == "$connect":
            q = event.get("queryStringParameters") or {}
            incident_id = str(q.get("incident_id") or "")
            if not _INCIDENT_ID.match(incident_id):
                return _response(400, "incident_id is required")
            token = q.get("token")
            if token:
                if auth is None:
                    from app.adapters.factory import make_auth_provider
                    from app.config_secrets import resolve_secrets

                    resolve_secrets()

                    auth = make_auth_provider()
                try:
                    auth.verify(token)
                except Unauthenticated:
                    return _response(401, "invalid token")
            register_connection(repo, conn, incident_id, clock())
            return _response(200)
        if route == "$disconnect":
            unregister_connection(repo, conn)
            return _response(200)
        return _response(200)  # $default: clients only receive; ignore anything they send
    except Exception:
        log.exception("websocket route %s failed", route)
        return _response(500, "internal error")
