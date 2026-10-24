"""Auth endpoints: guest sessions, registration/login (local profile), privacy, role requests."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.adapters.ports import Principal, Role, Unauthenticated
from app.api.sanitize import sanitize_text
from app.auth.audit import ROLE_ESCALATION_REJECTED
from app.auth.deps import current_principal
from app.auth.provider import PRIVILEGED_ROLES
from app.models.privacy import alias_secret_or_dev, guest_alias

router = APIRouter(prefix="/api/auth", tags=["auth"])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")  # client-supplied role/claims are rejected (Req 22.3)


class RegisterBody(_Body):
    display_name: str | None = Field(default=None, max_length=50)
    password: str = Field(min_length=8, max_length=128)


class LoginBody(_Body):
    user_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PrivacyBody(_Body):
    share_location: bool


class RoleRequestBody(_Body):
    role: str = Field(min_length=1, max_length=32)


def _services(request: Request) -> tuple[Any, Any, Any]:
    st = request.app.state
    if getattr(st, "auth", None) is None or getattr(st, "accounts", None) is None:
        raise HTTPException(status_code=503, detail="authentication service unavailable")
    return st.auth, st.accounts, st.audit


def _session(token: str, expires_at: datetime, subject: str, role: Role) -> dict[str, Any]:
    return {"token": token, "token_type": "bearer", "expires_at": expires_at.isoformat(),
            "subject": subject, "role": role.value}


@router.post("/guest")
def create_guest(request: Request) -> dict[str, Any]:
    auth, _, _ = _services(request)
    g = auth.issue_guest()
    alias = guest_alias(alias_secret_or_dev(), g.principal.subject)
    return {**_session(g.token, g.expires_at, g.principal.subject, g.principal.role),
            "display_name": alias, "alias": alias}


@router.post("/register", status_code=201)
def register(body: RegisterBody, request: Request) -> dict[str, Any]:
    auth, accounts, _ = _services(request)
    name = sanitize_text(body.display_name).strip() if body.display_name else None
    user = accounts.register(name or None, body.password)
    token, expires = auth.issue_token(user.user_id)
    return _session(token, expires, user.user_id, auth.role_for(user.user_id))


@router.post("/login")
def login(body: LoginBody, request: Request) -> dict[str, Any]:
    auth, accounts, _ = _services(request)
    try:
        user = accounts.authenticate(body.user_id, body.password)
    except Unauthenticated:
        raise HTTPException(status_code=401, detail="invalid credentials",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    token, expires = auth.issue_token(user.user_id)
    return _session(token, expires, user.user_id, auth.role_for(user.user_id))


@router.get("/me")
def me(request: Request, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _, accounts, _ = _services(request)
    user = accounts.ensure_user(principal.subject, principal.role)
    alias = guest_alias(alias_secret_or_dev(), principal.subject) if principal.is_guest else None
    return {"subject": principal.subject, "role": principal.role.value, "is_guest": principal.is_guest,
            "display_name": user.display_name or alias, "alias": alias,
            "privacy_preferences": user.privacy_preferences.model_dump(),
            "expires_at": principal.expires_at.isoformat() if principal.expires_at else None}


@router.put("/me/privacy")
def set_privacy(body: PrivacyBody, request: Request,
                principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _, accounts, _ = _services(request)
    user = accounts.set_share_location(principal.subject, principal.role, body.share_location)
    if not body.share_location:  # Req 31.9: drop stored check-in locations right away
        from app.api.routers.checkins import get_checkin_service
        try:
            get_checkin_service(request.app).revoke_locations(principal.subject)
        except HTTPException:  # check-in service not wired in this profile: nothing stored
            pass
    return {"privacy_preferences": user.privacy_preferences.model_dump()}


@router.post("/role-request")
def request_role(body: RoleRequestBody, request: Request,
                 principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """Roles come only from administrative configuration; self-assignment is always rejected.

    Attempts at Moderator or Authorized_Official are written to the audit log (Req 22.4).
    """
    _, _, audit = _services(request)
    requested = body.role.strip().lower()
    try:
        privileged = Role(requested) in PRIVILEGED_ROLES
    except ValueError:
        privileged = False
    if privileged:
        audit.record(principal.subject, principal.role.value, ROLE_ESCALATION_REJECTED, requested)
    raise HTTPException(status_code=403,
                        detail="roles are assigned only through administrative configuration")
