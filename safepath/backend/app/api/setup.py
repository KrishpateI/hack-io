"""Wire API infrastructure (errors, body cap, CORS, rate limiter, OpenAPI) into an app."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import BodySizeLimitMiddleware, install_error_handlers
from app.api.ratelimit import InMemoryRateLimiter
from app.config import Settings

API_VERSION = "0.1.0"
API_TITLE = "SafePath API"
API_DESCRIPTION = (
    "SafePath is a prototype evacuation-navigation demo. It is not an official "
    "emergency service, does not contact or dispatch 911, and its routes are "
    "algorithm-generated estimates, not safety guarantees. In an emergency call 911 "
    "and follow instructions from official authorities."
)
OPENAPI_TAGS = [
    {"name": "health", "description": "Service and graph status."},
    {"name": "regions", "description": "Supported regions and graph metadata."},
    {"name": "routes", "description": "Disaster-aware route calculation."},
    {"name": "incidents", "description": "Incidents and official notices."},
    {"name": "reports", "description": "Community hazard reports and votes."},
    {"name": "messages", "description": "Incident messages and check-ins."},
    {"name": "sources", "description": "Source catalog and data limitations."},
    {"name": "auth", "description": "Guest sessions and roles."},
]


def install_api_infrastructure(app: FastAPI, settings: Settings) -> None:
    app.title = API_TITLE
    app.version = API_VERSION
    app.description = API_DESCRIPTION
    app.openapi_tags = OPENAPI_TAGS
    install_error_handlers(app)
    app.state.rate_limiter = InMemoryRateLimiter()
    # Added first = inner; CORS is outermost so error responses carry CORS headers too.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Retry-After"],
    )
