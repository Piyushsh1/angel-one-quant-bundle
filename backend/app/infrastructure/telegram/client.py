"""
================================================================================
telegram/client.py  ▸  Telegram Bot API client
================================================================================
A thin async client over the Telegram Bot HTTP API for ONE shared bot that
serves every user of the platform. The bot token identifies the bot, never a
person; individual users are reached by their own `chat_id`.

Key Telegram facts this client is built around:
  · A bot CANNOT message a user by phone number or username out of the blue.
    The user must first contact the bot (tap Start). That first message is how
    we learn their chat_id — see `get_updates`.
  · `getUpdates` long-polls the update queue. Passing `offset` acknowledges
    everything up to that id so the same update is not returned twice.

Only three endpoints are used:
    GET  /getMe        → sanity/health of the token
    GET  /getUpdates   → receive incoming messages (the /start link handshake)
    POST /sendMessage  → deliver an alert to one chat_id
================================================================================

NOTE: no `from __future__ import annotations` is required here (this module is
not a FastAPI dependency), but we keep types concrete for clarity.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

log = logging.getLogger("telegram.client")

_API_ROOT = "https://api.telegram.org"


class TelegramError(Exception):
    """Raised when the Telegram API is unreachable or rejects a call."""


@dataclass(frozen=True)
class TelegramUpdate:
    """A single inbound update we care about: a text message from a chat."""

    update_id: int
    chat_id: str
    text: str
    # Best-effort display info for logging/audit; never trusted for auth.
    username: str | None
    first_name: str | None


def _base_url() -> str:
    token = settings.TELEGRAM_BOT_TOKEN.strip()
    if not token:
        raise TelegramError("Telegram bot token is not configured")
    return f"{_API_ROOT}/bot{token}"


async def get_me() -> dict[str, Any]:
    """Return the bot's own profile. Used to validate the token at startup."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{_base_url()}/getMe")
        data = resp.json()
    if not data.get("ok"):
        raise TelegramError(str(data.get("description") or "getMe failed"))
    return data["result"]


async def get_updates(
    *, offset: int | None, timeout: int = 25
) -> list[TelegramUpdate]:
    """Long-poll for new updates.

    `offset` should be (last handled update_id + 1) so Telegram drops updates we
    already processed. Only text messages are surfaced; other update types
    (edited messages, callbacks) are ignored for this handshake.
    """
    params: dict[str, Any] = {"timeout": timeout, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset

    # The HTTP timeout must exceed the long-poll timeout, or the client aborts
    # a perfectly healthy poll.
    async with httpx.AsyncClient(timeout=timeout + 10) as client:
        resp = await client.get(f"{_base_url()}/getUpdates", params=params)
        data = resp.json()

    if not data.get("ok"):
        raise TelegramError(str(data.get("description") or "getUpdates failed"))

    updates: list[TelegramUpdate] = []
    for raw in data.get("result", []):
        message = raw.get("message")
        if not message:
            continue
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        updates.append(
            TelegramUpdate(
                update_id=raw["update_id"],
                chat_id=str(chat_id),
                text=(message.get("text") or "").strip(),
                username=chat.get("username"),
                first_name=chat.get("first_name"),
            )
        )
    return updates


async def send_message(*, chat_id: str, text: str) -> bool:
    """Send a message to one chat. Returns True on success.

    Fire-and-forget by design: a delivery failure (user blocked the bot, network
    blip) is logged and swallowed so it never breaks the caller (e.g. the trade
    engine must not fail a trade because a notification didn't send).
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_base_url()}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            data = resp.json()
        if not data.get("ok"):
            log.warning(
                "Telegram sendMessage rejected chat=%s: %s",
                chat_id,
                data.get("description"),
            )
            return False
        return True
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Telegram sendMessage failed chat=%s: %s", chat_id, exc)
        return False
