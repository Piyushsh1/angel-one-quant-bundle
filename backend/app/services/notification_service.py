"""
================================================================================
services/notification_service.py  ▸  Telegram linking + per-user delivery
================================================================================
Business logic for connecting a user's Telegram and sending them alerts. The
platform runs ONE shared bot; each user is isolated by their own `chat_id`.

The linking handshake (why it works for many users at once):
  1. `begin_link` issues a short-lived, single-use code to the logged-in user
     and returns the deep link  t.me/<bot>?start=<code>.
  2. The user opens that link and taps Start; Telegram sends the bot a message
     "/start <code>" from THAT user's chat.
  3. The polling worker calls `consume_start_command`, which matches the code to
     the user row, stores that chat's id on the user, and marks them linked.
  4. From then on `notify_user` sends only to that user's chat_id — never any
     other user's. This is the same per-user isolation the rest of the platform
     enforces.

No code here ever messages a user we have not been contacted by first: Telegram
forbids it, and `notify_user` is a no-op until a chat_id exists.
================================================================================
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.database.models import User
from app.infrastructure.telegram import client as tg

log = logging.getLogger("notifications")

# A /start payload Telegram delivers as "/start <code>". Codes are urlsafe.
_START_RE = re.compile(r"^/start(?:\s+(?P<code>[A-Za-z0-9_-]{6,64}))?$")


class NotificationError(Exception):
    """Domain-level failure. `code` maps to an HTTP status in the router."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    cleaned = phone.strip()
    return cleaned or None


def _deep_link(code: str) -> str:
    username = settings.TELEGRAM_BOT_USERNAME.strip().lstrip("@")
    if not username:
        # Fall back to a generic link the user can still use by searching the
        # bot; but this should be configured.
        log.warning("TELEGRAM_BOT_USERNAME not set — deep link will be generic")
        return f"https://t.me/?start={code}"
    return f"https://t.me/{username}?start={code}"


async def begin_link(
    db: AsyncSession, *, user: User, phone: str | None
) -> dict[str, str | bool]:
    """Issue a one-time link code for `user` and return the bot deep link.

    Also stores the (optional) phone number. Idempotent: calling again reissues
    a fresh code, invalidating any previous unused one for this user.
    """
    if not settings.telegram_enabled:
        raise NotificationError(
            "unavailable",
            "Telegram notifications are not configured on the server.",
        )

    # Already linked? Return that state so the client can advance without a
    # second handshake.
    if user.telegram_chat_id:
        return {
            "linked": True,
            "deepLink": _deep_link(user.telegram_link_code or ""),
            "botUsername": settings.TELEGRAM_BOT_USERNAME.strip().lstrip("@"),
        }

    code = secrets.token_urlsafe(9)  # ~12 urlsafe chars, single-use
    user.telegram_link_code = code
    user.telegram_link_code_expires_at = _now() + timedelta(
        minutes=settings.TELEGRAM_LINK_CODE_TTL_MINUTES
    )
    phone_clean = _normalise_phone(phone)
    if phone_clean:
        user.phone_number = phone_clean

    log.info("telegram link code issued user=%s", user.email)
    return {
        "linked": False,
        "deepLink": _deep_link(code),
        "botUsername": settings.TELEGRAM_BOT_USERNAME.strip().lstrip("@"),
    }


async def consume_start_command(
    db: AsyncSession, *, code: str, chat_id: str
) -> User | None:
    """Link a Telegram chat to the user who owns `code`.

    Called by the polling worker for each inbound "/start <code>". Returns the
    linked User on success (so the worker can send a welcome), or None if the
    code is unknown/expired/already used.
    """
    if not code:
        return None

    user = (
        await db.execute(select(User).where(User.telegram_link_code == code))
    ).scalar_one_or_none()
    if user is None:
        log.info("telegram /start with unknown code")
        return None

    expires = user.telegram_link_code_expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires is not None and expires < _now():
        log.info("telegram /start with expired code user=%s", user.email)
        # Clear the stale code so it can't linger.
        user.telegram_link_code = None
        user.telegram_link_code_expires_at = None
        return None

    # If this Telegram chat is already bound to a different user, refuse — one
    # Telegram account maps to at most one platform user.
    clash = (
        await db.execute(
            select(User).where(
                User.telegram_chat_id == chat_id, User.id != user.id
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        log.warning(
            "telegram chat already linked to another user; refusing rebind"
        )
        return None

    user.telegram_chat_id = chat_id
    user.has_telegram_linked = True
    # Code is single-use — burn it now that it's consumed.
    user.telegram_link_code = None
    user.telegram_link_code_expires_at = None

    log.info("telegram linked user=%s chat=%s", user.email, chat_id)
    return user


async def link_status(db: AsyncSession, *, user_id: uuid.UUID) -> dict[str, bool]:
    """Whether the user has completed the Telegram handshake. Used for polling."""
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    linked = bool(user and user.telegram_chat_id and user.has_telegram_linked)
    return {"linked": linked}


async def notify_user(db: AsyncSession, *, user_id: uuid.UUID, message: str) -> bool:
    """Send `message` to a single user's Telegram, if they've linked one.

    This is the entry point the trade engine calls per user. Returns True if a
    message was actually delivered. Never raises — a notification failure must
    not affect trading.
    """
    if not settings.telegram_enabled:
        return False
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None or not user.telegram_chat_id:
        return False
    return await tg.send_message(chat_id=user.telegram_chat_id, text=message)


def parse_start_code(text: str) -> str | None:
    """Extract the code from a '/start <code>' message, or None."""
    match = _START_RE.match(text.strip())
    if not match:
        return None
    return match.group("code")
