"""FastAPI application factory and AWS Lambda handler."""
from __future__ import annotations

from fastapi import FastAPI
from mangum import Mangum

from app.api.registry import include_all_routers
from app.api.setup import install_api_infrastructure
from app.auth.deps import install_auth
from app.config import Settings
from app.routing.cost import load_weights_state


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="SafePath API", version="0.1.0")
    app.state.settings = settings
    app.state.routing_weights = load_weights_state()
    install_api_infrastructure(app, settings)
    install_auth(app)
    include_all_routers(app)
    return app


app = create_app()
handler = Mangum(app)
