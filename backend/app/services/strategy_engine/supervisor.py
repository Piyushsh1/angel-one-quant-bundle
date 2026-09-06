"""
================================================================================
strategy_engine/supervisor.py  ▸  Auto-trading engine supervisor
================================================================================
One background task, started at app startup, that drives every user's
auto-trading engine. On a fixed tick it:

  · loads the set of users who currently have auto_trading_enabled, and
  · runs one evaluation cycle for each of them (see engine.run_user_cycle).

Per-user isolation: each cycle opens its own DB session and acts only on that
user's account and broker connection, so users never interfere with each other.

Enabling/disabling is dynamic: turning the toggle on/off in the UI is picked up
on the next tick — no restart needed. The whole thing is opt-in and defaults
off, so an account is only ever traded after the user deliberately enables it.

Resilience: a failure in one user's cycle is logged and never stops the others
or the supervisor.
================================================================================
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.infrastructure.database.models import RiskParameters
from app.infrastructure.database.session import SessionFactory
from app.services.strategy_engine import engine

log = logging.getLogger("engine.supervisor")

TICK_SECONDS = engine.CYCLE_SECONDS


async def _active_user_ids() -> list:
    async with SessionFactory() as db:
        rows = (
            await db.execute(
                select(RiskParameters.user_id).where(
                    RiskParameters.auto_trading_enabled.is_(True)
                )
            )
        ).scalars().all()
        return list(rows)


async def _loop() -> None:
    log.info("auto-trading supervisor started")
    while True:
        try:
            user_ids = await _active_user_ids()
            if user_ids:
                # Run each user's cycle; isolate failures per user.
                results = await asyncio.gather(
                    *(engine.run_user_cycle(uid) for uid in user_ids),
                    return_exceptions=True,
                )
                for uid, res in zip(user_ids, results):
                    if isinstance(res, Exception):
                        log.exception(
                            "engine cycle failed user=%s: %s", uid, res
                        )
        except asyncio.CancelledError:
            log.info("auto-trading supervisor cancelled")
            raise
        except Exception:  # noqa: BLE001 — resilient supervisor
            log.exception("auto-trading supervisor error; continuing")
        await asyncio.sleep(TICK_SECONDS)


async def start() -> asyncio.Task:
    """Launch the supervisor loop. Runs regardless of whether any user is
    active — it simply idles when none have auto-trading on."""
    return asyncio.create_task(_loop(), name="auto-trading-supervisor")


async def stop(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
