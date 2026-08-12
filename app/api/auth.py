from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import get_access_gate, get_login_rate_limiter
from app.domain.settings import AuthConfig, AuthSession, LoginRequest
from app.errors import AppError
from app.security.access import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    AccessGate,
    LoginRateLimiter,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

GateDependency = Annotated[AccessGate, Depends(get_access_gate)]
LimiterDependency = Annotated[LoginRateLimiter, Depends(get_login_rate_limiter)]


@router.get("/config", response_model=AuthConfig)
async def auth_config(gate: GateDependency) -> AuthConfig:
    return AuthConfig(enabled=gate.enabled)


@router.get("/session", response_model=AuthSession)
async def auth_session(request: Request, gate: GateDependency) -> AuthSession:
    authenticated = gate.validate_session(request.cookies.get(SESSION_COOKIE_NAME))
    return AuthSession(enabled=gate.enabled, authenticated=authenticated)


@router.post("/login", response_model=AuthSession)
async def auth_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    gate: GateDependency,
    limiter: LimiterDependency,
) -> AuthSession:
    if not gate.enabled:
        return AuthSession(enabled=False, authenticated=True)

    identity = request.client.host if request.client else "unknown"
    if not limiter.allowed(identity):
        raise AppError(
            "LOGIN_RATE_LIMITED",
            "登录尝试过多，请稍后重试",
            429,
            True,
        )
    if not gate.verify_password(payload.password):
        limiter.record_failure(identity)
        raise AppError("INVALID_PASSWORD", "访问密码不正确", 401, False)

    limiter.reset(identity)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        gate.issue_session(),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return AuthSession(enabled=True, authenticated=True)


@router.post("/logout", response_model=AuthSession)
async def auth_logout(response: Response, gate: GateDependency) -> AuthSession:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return AuthSession(enabled=gate.enabled, authenticated=not gate.enabled)
