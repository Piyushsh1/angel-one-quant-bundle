"""
================================================================================
telegram/worker.py  ▸  Background link-handshake poller
================================================================================
One asyncio task, started at app startup, that long-polls Telegram getUpdates
and completes the linking handshake for EVERY user of the shared bot.

For each inbound "/start <code>" message it:
  1. parses the one-time code,
  2. binds that chat to the code's owner (notification_service),
  3. commits, and
  4. sends that user a real "you're linked" confirmation — the test message the
     onboarding UI promises.

Why polling (getUpdates) and not a webhook: a webhook needs a public HTTPS URL.
In local/dev the API is on localhost, so polling is the portable choice. A prod
deployment behind TLS can switch to a webhook without touching the service layer.

The loop is resilient: any transient Telegram/DB error is logged and the loop
continues after a short backoff. It never crashes the app.
================================================================================
"""
from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.infrastructure.database.session import SessionFactory
from app.infrastructure.telegram import client as tg
from app.services import notification_service

log = logging.getLogger("telegram.worker")

_WELCOME = (
    "✅ <b>Telegram linked to Barbell.</b>\n\n"
    "You'll get a message here for every trade we take on your account. "
    "In Auto mode that's each entry and exit as it happens."
)

# Backoff after an error so a persistent outage doesn't hot-loop the CPU.
_ERROR_BACKOFF_SEC = 5.0


async def _handle_update(update: tg.TelegramUpdate) -> None:
    """Process one inbound Telegram message; link if it's a valid /start code."""
    code = notification_service.parse_start_code(update.text)
    if code is None:
        # Not a /start with a code — could be a bare /start or chit-chat. Nudge
        # the user toward the deep link from the app.
        if update.text.strip() in ("/start", "start"):
            await tg.send_message(
                chat_id=update.chat_id,
                text=(
                    "👋 To link this Telegram to your Barbell account, open the "
                    "app and use the <b>Link Telegram</b> button on the "
                    "Notifications step — it opens a link that finishes the "
                    "connection automatically."
                ),
            )
        return

    async with SessionFactory() as db:
        try:
            user = await notification_service.consume_start_command(
                db, code=code, chat_id=update.chat_id
            )
            await db.commit()
        except Exception:  # noqa: BLE001 — never let one bad update kill the loop
            await db.rollback()
            log.exception("failed handling /start update")
            return

    if user is not None:
        await tg.send_message(chat_id=update.chat_id, text=_WELCOME)
        log.info("telegram linked via worker user=%s", user.email)
    else:
        # Unknown/expired/clashing code — tell the user so they retry from app.
        await tg.send_message(
            chat_id=update.chat_id,
            text=(
                "⚠️ That link has expired or was already used. Please open the "
                "app and tap <b>Link Telegram</b> again to get a fresh link."
            ),
        )


async def _poll_loop() -> None:
    offset: int | None = None
    log.info("telegram poll loop started")
    while True:
        try:
            updates = await tg.get_updates(offset=offset)
            for update in updates:
                await _handle_update(update)
                # Acknowledge up to and including this update so it isn't
                # redelivered on the next poll.
                offset = update.update_id + 1
        except asyncio.CancelledError:
            log.info("telegram poll loop cancelled")
            raise
        except Exception:  # noqa: BLE001 — resilient loop
            log.exception("telegram poll error; backing off")
            await asyncio.sleep(_ERROR_BACKOFF_SEC)


async def start_worker() -> asyncio.Task | None:
    """Validate the token and launch the poll loop. Returns the task, or None
    when Telegram is not configured (the app runs fine without it)."""
    if not settings.telegram_enabled:
        log.info("Telegram not configured — notification worker disabled")
        return None
    try:
        me = await tg.get_me()
        log.info("Telegram bot ready: @%s", me.get("username"))
    except tg.TelegramError:
        log.exception("Telegram token invalid — notification worker not started")
        return None

    return asyncio.create_task(_poll_loop(), name="telegram-poll")


async def stop_worker(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
