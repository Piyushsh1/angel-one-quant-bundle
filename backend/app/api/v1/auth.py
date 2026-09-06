"""
================================================================================
api/v1/auth.py  ▸  Auth endpoints
================================================================================
Matches the frontend contract:
    POST /api/v1/auth/signup   → 201 { user, nextStep } + Set-Cookie
    POST /api/v1/auth/login    → 200 { user, nextStep } + Set-Cookie
    POST /api/v1/auth/logout   → 204, clears cookies
    GET  /api/v1/auth/me       → 200 user   | 401

signup and login create a session and log the user in immediately, so the
frontend can route straight to onboarding.

NOTE: this module intentionally does NOT use `from __future__ import
annotations`. Under FastAPI, stringised annotations on route parameters
(especially `Request`/`Response`) can fail to resolve during dependency
inspection, which makes them get misread as query parameters. Keeping runtime
annotations avoids that class of bug.
================================================================================
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.cookies import clear_session_cookies, set_session_cookies
from app.api.dependencies import CurrentUser, DbSession, client_ip
from app.api.rate_limit import RateLimit
from app.core.config import settings
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    SignupRequest,
    compute_next_step,
    user_to_response,
)
from app.services import auth_service
from app.services.auth_service import AuthError

log = logging.getLogger("auth.api")

router = APIRouter(prefix="/auth", tags=["auth"])

# Map domain error codes → HTTP status. One place, so the service stays
# framework-free.
_STATUS_BY_CODE = {
    "conflict": status.HTTP_409_CONFLICT,
    "unauthorized": status.HTTP_401_UNAUTHORIZED,
    "forbidden": status.HTTP_403_FORBIDDEN,
}


def _raise_http(err: AuthError) -> None:
    raise HTTPException(
        status_code=_STATUS_BY_CODE.get(err.code, status.HTTP_400_BAD_REQUEST),
        detail=err.message,
    )


def _cookie_max_age(remember: bool) -> int:
    if remember:
        return settings.SESSION_REMEMBER_DAYS * 24 * 3600
    return settings.SESSION_IDLE_MINUTES * 60


@router.post(
    "/signup",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RateLimit(settings.SIGNUP_RATE_LIMIT))],
)
async def signup(
    request: Request,
    response: Response,
    payload: SignupRequest,
    db: DbSession,
) -> AuthResponse:
    try:
        user = await auth_service.register_user(
            db,
            full_name=payload.full_name,
            email=payload.email,
            password=payload.password,
        )
        raw_token, csrf_token = await auth_service.create_session(
            db,
            user=user,
            remember_device=False,
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        await db.commit()
    except AuthError as err:
        _raise_http(err)

    set_session_cookies(
        response,
        session_token=raw_token,
        csrf_token=csrf_token,
        max_age=_cookie_max_age(False),
    )
    log.info("signup ok user=%s", user.email)
    return AuthResponse(
        user=user_to_response(user),
        next_step=compute_next_step(user),
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    dependencies=[Depends(RateLimit(settings.LOGIN_RATE_LIMIT))],
)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: DbSession,
) -> AuthResponse:
    try:
        user = await auth_service.authenticate(
            db, email=payload.email, password=payload.password
        )
        raw_token, csrf_token = await auth_service.create_session(
            db,
            user=user,
            remember_device=payload.remember_device,
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        await db.commit()
    except AuthError as err:
        _raise_http(err)

    set_session_cookies(
        response,
        session_token=raw_token,
        csrf_token=csrf_token,
        max_age=_cookie_max_age(payload.remember_device),
    )
    log.info("login ok user=%s", user.email)
    return AuthResponse(
        user=user_to_response(user),
        next_step=compute_next_step(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: DbSession,
) -> Response:
    # Logout is intentionally lenient: it revokes whatever session cookie is
    # present and always clears cookies, so a stale/expired cookie still logs
    # the browser out cleanly. No CSRF requirement — there is nothing to protect
    # by keeping a user logged in against their will.
    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if raw_token:
        await auth_service.revoke_session(db, raw_token=raw_token)
        await db.commit()
    clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=None)
async def me(current: CurrentUser):
    """Return the authenticated user. 401 if there is no valid session.

    GET, so no CSRF requirement. `current` resolves the caller's OWN cookie —
    it can never return another user.
    """
    return user_to_response(current.user).model_dump(by_alias=True)
