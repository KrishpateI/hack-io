"""FastAPI auth dependencies: ``current_principal`` and ``require(role_set)`` (Req 22.7, 42.3, 42.4).

401 for a missing, expired or invalid token; 403 when the authenticated role is not permitted.
Sets ``request.state.subject`` and ``request.state.principal`` (read by the rate limiter).
"""
from __future__ import annotations

from typing import Callable, Iterable

from fastapi import FastAPI, HTTPException, Request

from app.adapters.factory import make_auth_provider, make_repository
from app.adapters.ports import AdapterNotAvailable, AuthProvider, Principal, Repository, Role, Unauthenticated
from app.auth.accounts import AccountService
from app.auth.audit import AuditWriter


def install_auth(app: FastAPI, auth: AuthProvider | None = None,
                 repository: Repository | None = None) -> None:
    """Attach auth provider, repository, audit writer and account service to ``app.state``."""
    if repository is None:
        repository = getattr(app.state, "repository", None)
    if repository is None:
        try:
            repository = make_repository()
        except AdapterNotAvailable:
            repository = None
    if auth is None:
        try:
            auth = make_auth_provider()
        except AdapterNotAvailable:
            auth = None
    app.state.repository = repository
    app.state.auth = auth
    app.state.audit = AuditWriter(repository) if repository is not None else None
    app.state.accounts = AccountService(repository) if repository is not None else None


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def current_principal(request: Request) -> Principal:
    provider: AuthProvider | None = getattr(request.app.state, "auth", None)
    if provider is None:
        raise HTTPException(status_code=503, detail="authentication service unavailable")
    try:
        principal = provider.verify(_bearer(request))
    except Unauthenticated as exc:
        raise HTTPException(status_code=401, detail=str(exc) or "authentication required",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    request.state.subject = principal.subject
    request.state.principal = principal
    return principal


def require(roles: Iterable[Role]) -> Callable[[Request], Principal]:
    """Dependency factory: ``Depends(require({Role.MODERATOR, Role.AUTHORIZED_OFFICIAL}))``."""
    allowed = frozenset(Role(r) for r in roles)

    def dependency(request: Request) -> Principal:
        principal = current_principal(request)
        if principal.role not in allowed:
            raise HTTPException(status_code=403, detail="insufficient role for this operation")
        return principal

    return dependency
