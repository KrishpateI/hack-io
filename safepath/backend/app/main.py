"""FastAPI application factory and AWS Lambda handler."""
from __future__ import annotations

from fastapi import FastAPI
from mangum import Mangum

from app.api.registry import include_all_routers
from app.api.setup import install_api_infrastructure
from app.auth.deps import install_auth
from app.config import Settings
from app.routing.cost import load_weights_state
from app.services.sweeper import sweeper_lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    from app.config_secrets import resolve_secrets

    resolve_secrets()  # aws profile only: *_SECRET_ARN -> *_SECRET (no-op otherwise)
    settings = settings or Settings.from_env()
    app = FastAPI(title="SafePath API", version="0.1.0", lifespan=sweeper_lifespan)
    app.state.settings = settings
    app.state.routing_weights = load_weights_state()
    install_api_infrastructure(app, settings)
    install_auth(app)
    if getattr(app.state, "repository", None) is not None:
        # Routing reads incidents, notices, reports and overlays from the Repository (task 8.5).
        from app.services.overlays import RepositoryContextProvider

        app.state.routing_context_provider = RepositoryContextProvider(app.state.repository)
    include_all_routers(app)
    if settings.demo_mode and getattr(app.state, "repository", None) is not None:
        # SIMULATED demo scenarios (Req 40). Seeding runs in a background thread so startup stays
        # fast; demo requests that need the data wait on the same lock.
        from app.services.demo import DemoService

        app.state.demo_service = DemoService(app)
        if settings.demo_autoseed:
            app.state.demo_service.start_background_seed()
    return app


app = create_app()
handler = Mangum(app)
