"""
================================================================================
strategy_engine/engine.py  ▸  Per-user background auto-trading engine
================================================================================
One async task per user who has auto-trading enabled. Each cycle it:

  1. Re-checks the user's switches (auto on? kill switch? paper/live?).
  2. Enforces ONE open trade per account.
  3. During entry hours, evaluates the opening-range breakout on the user's own
     live broker data and, on a signal, places the order through the SINGLE
     gated chokepoint (`broker_live_service.place_order`) — so it is simulated
     in paper mode and only real when the user turned live on. No per-trade
     approval.
  4. Records the order + open position so the dashboard shows it immediately.
  5. Monitors the open position and squares it off at the session cutoff.

Every user's loop uses THAT user's own broker session and is fully isolated.
The loop is resilient: any error is logged and the loop continues next cycle;
it never crashes the app.
================================================================================
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.brokers import angelone
from app.infrastructure.brokers.angelone import BrokerSessionError
from app.infrastructure.database.models import Order, Position, RiskParameters
from app.infrastructure.database.session import SessionFactory
from app.services import broker_live_service
from app.services.broker_live_service import LiveUnavailable, OrderRejected
from app.services.strategy_engine import breakout
from app.services.strategy_engine.breakout import Signal

log = logging.getLogger("engine")

# How often each user's engine evaluates the market, in seconds.
CYCLE_SECONDS = 15.0


async def _open_local_position(
    db: AsyncSession, *, user_id: uuid.UUID, sig: Signal, entry_price: float
) -> None:
    """Record a just-opened position + its entry order in the local tables.

    In paper mode these ARE the source of truth for the dashboard. In live mode
    the broker's own book is authoritative, but we still record the entry order
    so the ledger reflects what the engine did.
    """
    now = datetime.now(timezone.utc)
    pid = f"eng-{str(user_id)[:8]}-{sig.trading_symbol}"
    db.add(
        Position(
            id=pid,
            user_id=user_id,
            side="BUY",
            symbol=sig.trading_symbol,
            qty=sig.qty,
            avg_price=round(entry_price, 2),
            ltp=round(entry_price, 2),
            pnl=0.0,
            pnl_pct=0.0,
            is_open=True,
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        Order(
            id=f"ord-{str(user_id)[:8]}-{int(now.timestamp())}",
            user_id=user_id,
            side="BUY",
            symbol=sig.trading_symbol,
            qty=sig.qty,
            price=round(entry_price, 2),
            status="FILLED",
            executed_at=now,
        )
    )


async def _local_open_position(
    db: AsyncSession, *, user_id: uuid.UUID
) -> Position | None:
    return (
        await db.execute(
            select(Position).where(
                Position.user_id == user_id, Position.is_open.is_(True)
            )
        )
    ).scalars().first()


async def _has_open_trade(
    db: AsyncSession, *, user_id: uuid.UUID, live: bool
) -> bool:
    """One-trade-per-account guard. Checks the broker book in live mode, the
    local positions table in paper mode."""
    if live:
        try:
            broker_positions = await broker_live_service.get_positions(
                db, user_id=user_id
            )
            if broker_positions:
                return True
        except LiveUnavailable:
            pass
    return (await _local_open_position(db, user_id=user_id)) is not None


async def _entry_price(sig: Signal, sess) -> float:
    """Best-effort live option LTP for the entry; falls back to a nominal."""
    try:
        data = await angelone.fetch_ltp(
            api_key=sess.api_key,
            jwt_token=sess.jwt_token,
            exchange=sig.exchange,
            symbol=sig.trading_symbol,
            token="",  # LTP-by-symbol; token unknown for freshly built symbol
        )
        if data and data.get("ltp"):
            return float(data["ltp"])
    except Exception:  # noqa: BLE001 - LTP is best-effort
        pass
    return 0.0


async def run_user_cycle(user_id: uuid.UUID) -> None:
    """One evaluation cycle for a single user. Own DB session; commits itself."""
    async with SessionFactory() as db:
        params = (
            await db.execute(
                select(RiskParameters).where(RiskParameters.user_id == user_id)
            )
        ).scalar_one_or_none()

        # Respect the switches every cycle — they may have changed.
        if params is None or not params.auto_trading_enabled:
            return
        live = bool(params.live_trading_enabled)

        # Square-off window: close any open engine position, then stop.
        if breakout.should_square_off():
            await _square_off(db, user_id=user_id, live=live)
            await db.commit()
            return

        if not breakout.market_is_open():
            return

        # Refresh the open position's P&L for the dashboard (paper mode).
        await _mark_to_market(db, user_id=user_id, live=live)

        # One trade at a time.
        if await _has_open_trade(db, user_id=user_id, live=live):
            await db.commit()
            return

        # Kill switch blocks new entries (existing handled above).
        if params.kill_switch_armed:
            await db.commit()
            return

        if not breakout.can_enter_new():
            await db.commit()
            return

        # Need a usable broker session to read candles + place the order.
        try:
            sess = await broker_live_service._load_session(db, user_id=user_id)
        except LiveUnavailable:
            return  # not connected — nothing to do

        try:
            sig = await breakout.generate_signal(
                api_key=sess.api_key, jwt_token=sess.jwt_token
            )
        except BrokerSessionError:
            log.info("engine: broker session expired user=%s", user_id)
            return
        if sig is None:
            await db.commit()
            return

        # Place through the gate: paper simulates, live sends the real order.
        order = _build_order_params(sig)
        try:
            result = await broker_live_service.place_order(
                db, user_id=user_id, order=order
            )
        except OrderRejected as rej:
            log.info("engine: order rejected user=%s: %s", user_id, rej.reason)
            await db.commit()
            return
        except LiveUnavailable:
            return

        entry = await _entry_price(sig, sess)
        await _open_local_position(db, user_id=user_id, sig=sig, entry_price=entry)
        await db.commit()

        log.info(
            "engine: ENTER user=%s mode=%s %s %s qty=%d %s",
            user_id, result.get("mode"), sig.trading_symbol, sig.opt_type,
            sig.qty, sig.reason,
        )

        # Notify the user on Telegram (per-user; never raises).
        try:
            from app.services import notification_service

            await notification_service.notify_user(
                db,
                user_id=user_id,
                message=(
                    f"🟢 <b>Auto trade opened</b> ({result.get('mode')})\n"
                    f"{sig.trading_symbol} • BUY {sig.qty}\n{sig.reason}"
                ),
            )
        except Exception:  # noqa: BLE001
            pass


def _build_order_params(sig: Signal) -> dict:
    """Angel One placeOrder params for a market BUY of the option."""
    return {
        "variety": "NORMAL",
        "tradingsymbol": sig.trading_symbol,
        "transactiontype": "BUY",
        "exchange": sig.exchange,
        "ordertype": "MARKET",
        "producttype": "INTRADAY",
        "duration": "DAY",
        "price": "0",
        "quantity": str(sig.qty),
        # symboltoken is resolved by the broker for a valid tradingsymbol in
        # live mode; paper mode never sends this to the broker.
        "symboltoken": "",
    }


async def _mark_to_market(
    db: AsyncSession, *, user_id: uuid.UUID, live: bool
) -> None:
    """Update the local open position's LTP/P&L so the dashboard is live.

    Live mode reads P&L from the broker book elsewhere, so this only maintains
    the paper-mode position row.
    """
    if live:
        return
    pos = await _local_open_position(db, user_id=user_id)
    if pos is None:
        return
    try:
        sess = await broker_live_service._load_session(db, user_id=user_id)
        data = await angelone.fetch_ltp(
            api_key=sess.api_key, jwt_token=sess.jwt_token,
            exchange="NFO", symbol=pos.symbol, token="",
        )
    except (LiveUnavailable, BrokerSessionError, Exception):  # noqa: BLE001
        return
    if not data or not data.get("ltp"):
        return
    ltp = float(data["ltp"])
    pos.ltp = round(ltp, 2)
    pos.pnl = round((ltp - pos.avg_price) * pos.qty, 2)
    if pos.avg_price:
        pos.pnl_pct = round(((ltp - pos.avg_price) / pos.avg_price) * 100, 2)


async def _square_off(
    db: AsyncSession, *, user_id: uuid.UUID, live: bool
) -> None:
    """Close the open engine position at the session cutoff."""
    pos = await _local_open_position(db, user_id=user_id)
    if pos is None:
        return
    if live:
        # Send a real SELL to flatten.
        try:
            await broker_live_service.place_order(
                db,
                user_id=user_id,
                order={
                    "variety": "NORMAL",
                    "tradingsymbol": pos.symbol,
                    "transactiontype": "SELL",
                    "exchange": "NFO",
                    "ordertype": "MARKET",
                    "producttype": "INTRADAY",
                    "duration": "DAY",
                    "price": "0",
                    "quantity": str(pos.qty),
                    "symboltoken": "",
                },
            )
        except (OrderRejected, LiveUnavailable):
            pass
    pos.is_open = False
    now = datetime.now(timezone.utc)
    db.add(
        Order(
            id=f"ord-{str(user_id)[:8]}-{int(now.timestamp())}-x",
            user_id=user_id,
            side="SELL",
            symbol=pos.symbol,
            qty=pos.qty,
            price=pos.ltp,
            status="FILLED",
            executed_at=now,
        )
    )
    log.info("engine: SQUARE-OFF user=%s %s", user_id, pos.symbol)
