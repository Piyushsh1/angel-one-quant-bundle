"""
================================================================================
services/auth_service.py  ▸  Authentication business logic
================================================================================
Framework-free logic: every function takes an AsyncSession and returns domain
objects or raises `AuthError`. The router translates those into HTTP.

Per-user isolation is enforced structurally: every query that resolves a
subject is filtered by the caller's own user_id or their own session token.
No function ever accepts a user_id from the client for a data lookup — the
subject is always derived from the authenticated session cookie.

Account-enumeration resistance: login returns the same generic failure whether
the email is unknown or the password is wrong, and still performs a dummy hash
verification on unknown emails so the response time does not reveal which case
occurred.
================================================================================
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import settings
from app.infrastructure.database.models import User, UserSession

log = logging.getLogger("auth")

# A precomputed argon2 hash of a random value. Verifying a submitted password
# against this on an unknown-email login keeps timing constant, defeating
# email-enumeration by response latency.
_DUMMY_HASH = security.hash_password("timing-equaliser-not-a-real-password")


class AuthError(Exception):
    """Domain-level auth failure. `code` maps to an HTTP status in the router."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Registration ────────────────────────────────────────────────────────────
async def register_user(
    db: AsyncSession, *, full_name: str, email: str, password: str
) -> User:
    """Create a user. Raises AuthError('conflict') if the email is taken.

    The unique constraint on email is the real guard: even if two signups race,
    the database rejects the second, and we translate that rather than relying
    on a check-then-insert that a concurrent request could slip between.
    """
    user = User(
        email=email,
        full_name=full_name,
        password_hash=security.hash_password(password),
    )
    db.add(user)
    try:
        await db.flush()  # surfaces the unique violation now, inside our TX
    except IntegrityError:
        await db.rollback()
        raise AuthError("conflict", "An account with that email already exists")
    return user


# ── Login ─────────────────────────────────────────────────────────────────
async def authenticate(db: AsyncSession, *, email: str, password: str) -> User:
    """Return the user on valid credentials, else raise AuthError('unauthorized').

    Uses one generic error and constant-time behaviour for both the
    unknown-email and wrong-password cases.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        # Spend the same work as a real verify so timing does not leak that the
        # email is unknown.
        security.verify_password(password, _DUMMY_HASH)
        raise AuthError("unauthorized", "Invalid email or password")

    if not user.is_active:
        raise AuthError("forbidden", "This account is disabled")

    if not security.verify_password(password, user.password_hash):
        raise AuthError("unauthorized", "Invalid email or password")

    # Transparent hash upgrade if the params have since been strengthened.
    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)

    return user


# ── Sessions ─────────────────────────────────────────────────────────────
async def create_session(
    db: AsyncSession,
    *,
    user: User,
    remember_device: bool,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[str, str]:
    """Create a server-side session. Returns ``(raw_token, csrf_token)``.

    The raw token is returned ONCE, to be set as the client cookie; only its
    hash is stored. Each login creates a distinct session row, so the same user
    signed in on two devices — or two different users — never share session
    state.
    """
    raw_token = security.generate_session_token()
    csrf_token = security.generate_csrf_token()
    now = _now()

    idle_window = (
        timedelta(days=settings.SESSION_REMEMBER_DAYS)
        if remember_device
        else timedelta(minutes=settings.SESSION_IDLE_MINUTES)
    )

    session = UserSession(
        user_id=user.id,
        token_hash=security.hash_token(raw_token),
        csrf_token=csrf_token,
        expires_at=now + idle_window,
        absolute_expires_at=now + timedelta(hours=settings.SESSION_ABSOLUTE_HOURS),
        ip_address=ip_address,
        user_agent=(user_agent or "")[:256] or None,
    )
    db.add(session)
    await db.flush()
    return raw_token, csrf_token


async def resolve_session(
    db: AsyncSession, *, raw_token: str
) -> tuple[User, UserSession] | None:
    """Look up a live session by its raw token, or None if invalid/expired.

    Refreshes the idle window on each hit (sliding expiry) while respecting the
    absolute cap. Expired sessions are deleted lazily here so stale rows do not
    accumulate unbounded.
    """
    token_hash = security.hash_token(raw_token)
    result = await db.execute(
        select(UserSession).where(UserSession.token_hash == token_hash)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return None

    now = _now()
    if now >= session.expires_at or now >= session.absolute_expires_at:
        # Expired — remove it and treat as unauthenticated.
        await db.delete(session)
        return None

    user_result = await db.execute(select(User).where(User.id == session.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        await db.delete(session)
        return None

    # Slide the idle window forward, capped by the absolute expiry.
    new_expiry = min(
        now + timedelta(minutes=settings.SESSION_IDLE_MINUTES),
        session.absolute_expires_at,
    )
    session.expires_at = new_expiry
    session.last_seen_at = now

    return user, session


async def revoke_session(db: AsyncSession, *, raw_token: str) -> None:
    """Delete the session row for this token. Idempotent."""
    await db.execute(
        delete(UserSession).where(
            UserSession.token_hash == security.hash_token(raw_token)
        )
    )
