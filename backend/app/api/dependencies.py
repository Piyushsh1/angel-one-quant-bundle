"""
================================================================================
api/dependencies.py  ▸  Request-scoped auth dependencies
================================================================================
`get_current_user` is the gate every protected endpoint depends on. It derives
the subject SOLELY from the session cookie — a client can never pass a user_id
to act as someone else. This is the mechanism that keeps concurrent users
isolated: request A and request B each resolve their own cookie to their own
user, on their own database session.

CSRF is enforced here for state-changing methods using the double-submit
pattern: the request must echo the session's CSRF token in a header, and it must
match the token bound to that session server-side.
================================================================================

NOTE: no `from __future__ import annotations` — see the note in rate_limit.py.
FastAPI must see the real `Request` type on dependency callables, not a string.
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import constant_time_equals
from app.infrastructure.database.models import User, UserSession
from app.infrastructure.database.session import get_db_session
from app.services import auth_service

DbSession = Annotated[AsyncSession, Depends(get_db_session)]

CSRF_HEADER = "X-CSRF-Token"
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class AuthContext:
    """The authenticated subject for one request."""

    def __init__(self, user: User, session: UserSession):
        self.user = user
        self.session = session


async def _resolve(request: Request, db: AsyncSession) -> AuthContext | None:
    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not raw_token:
        return None
    resolved = await auth_service.resolve_session(db, raw_token=raw_token)
    if resolved is None:
        return None
    user, session = resolved
    return AuthContext(user, session)


async def get_current_user(request: Request, db: DbSession) -> AuthContext:
    """Require a valid session; enforce CSRF on unsafe methods.

    Raises 401 when unauthenticated and 403 when the CSRF check fails.
    """
    # Import here to avoid a circular import with the router's error mapping.
    from fastapi import HTTPException, status

    ctx = await _resolve(request, db)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    if request.method in _UNSAFE_METHODS:
        header_token = request.headers.get(CSRF_HEADER, "")
        if not header_token or not constant_time_equals(
            header_token, ctx.session.csrf_token
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing CSRF token",
            )

    return ctx


CurrentUser = Annotated[AuthContext, Depends(get_current_user)]


def client_ip(request: Request) -> str | None:
    """Best-effort client IP, honouring one proxy hop via X-Forwarded-For."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
