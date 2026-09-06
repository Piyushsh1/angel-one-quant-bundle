"""
================================================================================
services/dashboard_service.py  ▸  Trading terminal business logic
================================================================================
Framework-free logic for the dashboard panels. Every function takes an
AsyncSession and a `user_id`, and every query is filtered by that user_id —
one user can never read or mutate another user's positions, orders, strategies,
or P&L. The subject is always the authenticated caller resolved from their
session cookie; it is never accepted from the request body.
================================================================================
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import (
    BrokerAccount,
    IntradayPnlPoint,
    Order,
    Position,
    RiskParameters,
    Strategy,
    User,
)
from app.services import broker_live_service
from app.services.broker_live_service import LiveUnavailable

log = logging.getLogger("dashboard")

# Human labels for connected brokers, keyed by the broker_id we store.
_BROKER_LABELS = {
    "angelone": "Angel One SmartAPI",
    "zerodha": "Zerodha Kite",
    "upstox": "Upstox",
    "groww": "Groww",
    "dhan": "Dhan",
    "fyers": "Fyers",
}


class DashboardError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# India Standard Time is UTC+5:30 (no DST). The trading date is the IST date,
# which is also what the frontend date-picker uses — keep them in lockstep.
_IST = timezone(timedelta(hours=5, minutes=30))


def _today() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


def normalise_date(value: str | None) -> str:
    """Validate an optional ?date=YYYY-MM-DD, defaulting to today.

    Raises DashboardError on a malformed date so the API returns 422 rather than
    silently ignoring a bad filter.
    """
    if not value:
        return _today()
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise DashboardError("invalid", "date must be YYYY-MM-DD") from exc
    return value


def is_today(date_str: str) -> bool:
    return date_str == _today()


# ── Positions ────────────────────────────────────────────────────────────────

async def list_open_positions(db: AsyncSession, *, user_id: uuid.UUID) -> list[Position]:
    result = await db.execute(
        select(Position)
        .where(Position.user_id == user_id, Position.is_open.is_(True))
        .order_by(Position.created_at.desc())
    )
    return list(result.scalars().all())


async def close_position(db: AsyncSession, *, user_id: uuid.UUID, position_id: str) -> Position:
    """Market-close a single open position owned by the caller."""
    result = await db.execute(
        select(Position).where(
            Position.id == position_id, Position.user_id == user_id
        )
    )
    position = result.scalar_one_or_none()
    if position is None or not position.is_open:
        raise DashboardError("not_found", "Position not found or already closed")
    position.is_open = False
    return position


async def close_all_positions(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Panic square-off: close every open position for the caller. Returns count."""
    result = await db.execute(
        update(Position)
        .where(Position.user_id == user_id, Position.is_open.is_(True))
        .values(is_open=False)
    )
    return result.rowcount or 0


async def cancel_pending_orders(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Cancel every PENDING order for the caller. Returns count."""
    result = await db.execute(
        update(Order)
        .where(Order.user_id == user_id, Order.status == "PENDING")
        .values(status="CANCELLED")
    )
    return result.rowcount or 0


# ── Strategies ───────────────────────────────────────────────────────────────

async def list_strategies(db: AsyncSession, *, user_id: uuid.UUID) -> list[Strategy]:
    result = await db.execute(
        select(Strategy)
        .where(Strategy.user_id == user_id)
        .order_by(Strategy.created_at.asc())
    )
    return list(result.scalars().all())


async def set_strategy_status(
    db: AsyncSession, *, user_id: uuid.UUID, strategy_id: str, status: str
) -> Strategy:
    result = await db.execute(
        select(Strategy).where(
            Strategy.id == strategy_id, Strategy.user_id == user_id
        )
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        raise DashboardError("not_found", "Strategy not found")
    strategy.status = status
    return strategy


async def halt_all_strategies(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    result = await db.execute(
        update(Strategy)
        .where(Strategy.user_id == user_id, Strategy.status == "running")
        .values(status="stopped")
    )
    return result.rowcount or 0


# ── Orders ───────────────────────────────────────────────────────────────────

async def list_orders(
    db: AsyncSession, *, user_id: uuid.UUID, limit: int = 20
) -> list[Order]:
    result = await db.execute(
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.executed_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ══════════════════════════════════════════════════════════════════════════════
#  LIVE views — the real, broker-backed data the dashboard renders
# ══════════════════════════════════════════════════════════════════════════════
#  These are the functions the API calls. For TODAY they read live from the
#  connected broker (positions, orders, margin, index quotes). When the broker
#  is not connected or a live call fails, they return an explicit, honest empty
#  state (live=False + a notice) — never fabricated numbers. For PAST dates,
#  there is no stored per-trade broker history in this service, so history
#  queries return empty with a clear notice rather than inventing data.


async def positions_view(
    db: AsyncSession, *, user_id: uuid.UUID, date: str | None = None
) -> dict:
    """Live open positions for today; empty for past dates (no broker history)."""
    date = normalise_date(date)
    if not is_today(date):
        return {"items": [], "aggregate_unrealized": 0.0, "live": False,
                "notice": "Historical position snapshots are not available."}
    try:
        items = await broker_live_service.get_positions(db, user_id=user_id)
    except LiveUnavailable as exc:
        return {"items": [], "aggregate_unrealized": 0.0, "live": False,
                "notice": _live_notice(exc)}
    aggregate = round(sum(p["pnl"] for p in items), 2)
    return {"items": items, "aggregate_unrealized": aggregate, "live": True,
            "notice": None}


async def orders_view(
    db: AsyncSession, *, user_id: uuid.UUID, date: str | None = None
) -> dict:
    """Live order book for today; empty for past dates."""
    date = normalise_date(date)
    if not is_today(date):
        return {"items": [], "live": False,
                "notice": "Historical order book is not available."}
    try:
        items = await broker_live_service.get_orders(db, user_id=user_id)
    except LiveUnavailable as exc:
        return {"items": [], "live": False, "notice": _live_notice(exc)}
    return {"items": items, "live": True, "notice": None}


async def holdings_view(db: AsyncSession, *, user_id: uuid.UUID) -> dict:
    """Live demat holdings."""
    try:
        items = await broker_live_service.get_holdings(db, user_id=user_id)
    except LiveUnavailable as exc:
        return {"items": [], "live": False, "notice": _live_notice(exc)}
    return {"items": items, "live": True, "notice": None}


def _live_notice(exc: LiveUnavailable) -> str:
    if exc.needs_reconnect:
        return "Broker session expired — reconnect your broker to see live data."
    return "Connect your broker to see live data."


# ── Market indices (live broker quotes) ──────────────────────────────────────


async def market_indices(db: AsyncSession, *, user_id: uuid.UUID) -> list[dict]:
    """Live NIFTY / BANKNIFTY / SENSEX quotes from the connected broker.

    Returns real LTP + day change. When no broker is connected (or the market
    feed is unavailable) it returns an empty list — the ticker then simply shows
    nothing rather than stale or invented numbers.
    """
    try:
        return await broker_live_service.get_index_quotes(db, user_id=user_id)
    except LiveUnavailable:
        return []


# ── Metrics ──────────────────────────────────────────────────────────────────

async def compute_metrics(
    db: AsyncSession, *, user_id: uuid.UUID, date: str | None = None
) -> dict:
    """Executive-summary metrics, computed from LIVE broker data for today.

    Every number here is real: positions/orders come from the broker's live
    books, margin from live RMS. When no broker is connected, everything is a
    truthful zero and the gateway shows "Not connected" — nothing is invented.
    For a past date there is no live snapshot, so figures are zero with the
    gateway state still reflecting the connection.
    """
    date = normalise_date(date)
    today = is_today(date)

    from app.services import broker_service

    connection = await broker_service.active_connection(db, user_id=user_id)
    gateway_connected = connection is not None
    gateway_name = _BROKER_LABELS.get(
        connection.broker_id if connection else "", "No broker connected"
    )

    # Trading mode: paper by default, live only when the user opted in. Read
    # from the persisted per-user risk parameters (creates the row if absent).
    risk = await get_risk_parameters(db, user_id=user_id)
    live_trading_enabled = bool(risk.live_trading_enabled)
    auto_trading_enabled = bool(risk.auto_trading_enabled)

    # Strategies are user-defined engines; they remain DB-backed until the
    # trading engine writes live runtime state. Count only for the hero.
    strategies = await list_strategies(db, user_id=user_id)
    active_strategies = sum(1 for s in strategies if s.status == "running")

    # Defaults = honest zero (used when not connected or a live call fails).
    positions: list[dict] = []
    orders: list[dict] = []
    available_margin = 0.0
    total_capital = 0.0

    if today and gateway_connected:
        try:
            positions = await broker_live_service.get_positions(db, user_id=user_id)
        except LiveUnavailable:
            positions = []
        try:
            orders = await broker_live_service.get_orders(db, user_id=user_id)
        except LiveUnavailable:
            orders = []
        try:
            margin = await broker_live_service.get_margin(db, user_id=user_id)
            available_margin = margin.get("available_cash") or 0.0
            total_capital = margin.get("total") or available_margin
        except LiveUnavailable:
            available_margin = 0.0
            total_capital = 0.0

    # P&L on open positions = live unrealized (Angel One reports intraday pnl).
    total_pnl = round(sum(p["pnl"] for p in positions), 2)
    net_exposure = round(sum(abs(p["qty"] * p["ltp"]) for p in positions), 2)
    long_count = sum(1 for p in positions if p["side"] == "BUY")
    short_count = sum(1 for p in positions if p["side"] == "SELL")

    filled = [o for o in orders if o["status"] == "FILLED"]
    executed_trades = len(filled)

    # Real brokerage charges aren't itemised on the position book; report gross
    # = net (no invented charge model). Charges become real once the trade
    # engine records them per fill.
    charges = 0.0
    gross_pnl = total_pnl

    wins = sum(1 for p in positions if p["pnl"] > 0)
    losses = sum(1 for p in positions if p["pnl"] < 0)
    decided = wins + losses
    win_rate = round((wins / decided) * 100, 1) if decided else 0.0

    total_pnl_pct = (
        round((total_pnl / total_capital) * 100, 2) if total_capital else 0.0
    )

    return {
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "gross_pnl": gross_pnl,
        "charges": charges,
        "open_positions_count": len(positions),
        "net_exposure": net_exposure,
        "long_count": long_count,
        "short_count": short_count,
        "executed_trades": executed_trades,
        "win_rate": win_rate,
        "wins": wins,
        "losses": losses,
        "available_margin": round(available_margin, 2),
        "total_capital": round(total_capital, 2),
        "gateway_name": gateway_name,
        "gateway_connected": gateway_connected,
        "gateway_mode": (
            "Not connected"
            if not gateway_connected
            else "Live"
            if live_trading_enabled
            else "Paper"
        ),
        "live_trading_enabled": live_trading_enabled,
        "auto_trading_enabled": auto_trading_enabled,
        "active_strategies": active_strategies,
        "engine_online": auto_trading_enabled,
    }


# ── Intraday P&L curve ───────────────────────────────────────────────────────

async def intraday_pnl(
    db: AsyncSession, *, user_id: uuid.UUID, date: str | None = None
) -> dict:
    """Real intraday P&L curve.

    For today: reads the user's recorded intraday P&L points and appends a fresh
    snapshot taken from the LIVE broker position book, so the curve is built from
    real data as the day progresses (each dashboard poll records one point). For
    a past date: returns whatever real points were recorded that day (empty if
    none). No synthetic curve is ever generated.
    """
    date = normalise_date(date)

    # Take (and persist) a live snapshot for today so the curve accumulates real
    # points over the session.
    unrealized = 0.0
    if is_today(date):
        try:
            live_positions = await broker_live_service.get_positions(
                db, user_id=user_id
            )
            unrealized = round(sum(p["pnl"] for p in live_positions), 2)
            await _record_pnl_point(db, user_id=user_id, pnl=unrealized, date=date)
        except LiveUnavailable:
            unrealized = 0.0

    rows = (
        await db.execute(
            select(IntradayPnlPoint)
            .where(
                IntradayPnlPoint.user_id == user_id,
                IntradayPnlPoint.trading_date == date,
            )
            .order_by(IntradayPnlPoint.ts.asc())
        )
    ).scalars().all()

    if not rows:
        points: list[list[float]] = []
        current = day_high = day_low = 0.0
    else:
        base = rows[0].ts
        points = [
            [round((r.ts - base).total_seconds() / 60, 1), round(r.pnl, 2)]
            for r in rows
        ]
        pnls = [r.pnl for r in rows]
        current = round(pnls[-1], 2)
        day_high = round(max(pnls), 2)
        day_low = round(min(pnls), 2)

    # Current P&L is live unrealized on open positions; realized (closed today)
    # isn't itemised on the position book, so it is reported as 0 until the
    # trade engine records fills. current = realized + unrealized.
    realized = round(current - unrealized, 2) if rows else 0.0

    return {
        "points": points,
        "current_pnl": current if rows else round(unrealized, 2),
        "day_high": day_high,
        "day_low": day_low,
        "realized": realized,
        "unrealized": round(unrealized, 2),
    }


# Minimum spacing between recorded intraday points, so rapid polling doesn't
# flood the table. One point per ~minute is plenty for a smooth curve.
_PNL_POINT_MIN_GAP_SEC = 55.0


async def _record_pnl_point(
    db: AsyncSession, *, user_id: uuid.UUID, pnl: float, date: str
) -> None:
    """Persist a live P&L snapshot, throttled to at most one per minute."""
    last = (
        await db.execute(
            select(IntradayPnlPoint)
            .where(
                IntradayPnlPoint.user_id == user_id,
                IntradayPnlPoint.trading_date == date,
            )
            .order_by(IntradayPnlPoint.ts.desc())
            .limit(1)
        )
    ).scalars().first()

    now = datetime.now(timezone.utc)
    if last is not None:
        last_ts = last.ts if last.ts.tzinfo else last.ts.replace(tzinfo=timezone.utc)
        if (now - last_ts).total_seconds() < _PNL_POINT_MIN_GAP_SEC:
            return  # too soon — keep the curve to ~1 point/min

    db.add(
        IntradayPnlPoint(
            user_id=user_id, ts=now, pnl=round(pnl, 2), trading_date=date
        )
    )


# ── Order helpers ────────────────────────────────────────────────────────────

def order_time_str(order: Order) -> str:
    return order.executed_at.astimezone(timezone.utc).strftime("%H:%M:%S")


# ── Broker accounts ──────────────────────────────────────────────────────────

def _mask_client_id(client_id: str) -> str:
    """Mask a broker client id for display: 'A12345678' → 'A12***678'."""
    cid = client_id.strip()
    if len(cid) <= 5:
        return cid[:1] + "***"
    return f"{cid[:3]}***{cid[-2:]}"


async def list_broker_accounts(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[BrokerAccount]:
    result = await db.execute(
        select(BrokerAccount)
        .where(BrokerAccount.user_id == user_id)
        .order_by(BrokerAccount.connected_at.desc())
    )
    return list(result.scalars().all())


async def connect_broker(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    broker_id: str,
    credentials: dict,
) -> BrokerAccount:
    """Record a broker connection for the caller.

    In this cut it validates that a client identifier was supplied and stores
    the non-secret connection state. A real integration performs a live broker
    login and encrypts the secrets via KMS; the credentials dict is deliberately
    never persisted or logged here.
    """
    client_id = (
        credentials.get("clientId")
        or credentials.get("client_id")
        or credentials.get("apiKey")
        or ""
    ).strip()
    if not client_id:
        raise DashboardError("bad_request", "Missing broker client identifier")

    # Upsert: reconnecting the same broker replaces the prior row.
    existing = await db.execute(
        select(BrokerAccount).where(
            BrokerAccount.user_id == user_id, BrokerAccount.broker_id == broker_id
        )
    )
    account = existing.scalar_one_or_none()
    if account is None:
        account = BrokerAccount(user_id=user_id, broker_id=broker_id)
        db.add(account)

    account.masked_client_id = _mask_client_id(client_id)
    account.account_name = f"{broker_id.title()} Account"
    account.status = "connected"

    # Advance the user's onboarding flag so nextStep routing progresses.
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is not None:
        user.has_broker_connected = True

    return account


async def disconnect_broker(
    db: AsyncSession, *, user_id: uuid.UUID, account_id: str
) -> None:
    result = await db.execute(
        select(BrokerAccount).where(
            BrokerAccount.id == account_id, BrokerAccount.user_id == user_id
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise DashboardError("not_found", "Broker account not found")
    await db.delete(account)


# ── Risk parameters ──────────────────────────────────────────────────────────

async def get_risk_parameters(
    db: AsyncSession, *, user_id: uuid.UUID
) -> RiskParameters:
    """Return the caller's risk parameters, creating defaults on first access."""
    result = await db.execute(
        select(RiskParameters).where(RiskParameters.user_id == user_id)
    )
    params = result.scalar_one_or_none()
    if params is None:
        params = RiskParameters(user_id=user_id)
        db.add(params)
        await db.flush()
    return params


async def update_risk_parameters(
    db: AsyncSession, *, user_id: uuid.UUID, changes: dict
) -> RiskParameters:
    """Apply a partial update. Only keys present in `changes` are written."""
    params = await get_risk_parameters(db, user_id=user_id)
    for key, value in changes.items():
        if value is not None and hasattr(params, key):
            setattr(params, key, value)
    return params


# ── Quant reports (performance analytics) ────────────────────────────────────

async def compute_reports(
    db: AsyncSession, *, user_id: uuid.UUID, date: str | None = None
) -> dict:
    """Real performance analytics for a trading date.

    Built from live broker data for today (open positions + order book) and the
    real recorded intraday P&L curve. No modelled/seeded figures: when there is
    no broker or no activity, every stat is a truthful zero. Realized-trade
    analytics (win/loss per closed trade) become richer once the trade engine
    records fills; today they derive from live open-position P&L.
    """
    date = normalise_date(date)
    today = is_today(date)

    positions: list[dict] = []
    orders: list[dict] = []
    if today:
        try:
            positions = await broker_live_service.get_positions(db, user_id=user_id)
        except LiveUnavailable:
            positions = []
        try:
            orders = await broker_live_service.get_orders(db, user_id=user_id)
        except LiveUnavailable:
            orders = []

    net_pnl = round(sum(p["pnl"] for p in positions), 2)

    filled = [o for o in orders if o["status"] == "FILLED"]
    trades_total = len(filled)
    # No invented charge model — gross equals net until real fills carry costs.
    total_charges = 0.0
    gross_pnl = net_pnl

    # Win/loss from live position P&L signs (real, per open position).
    win_pnls = [p["pnl"] for p in positions if p["pnl"] > 0]
    loss_pnls = [-p["pnl"] for p in positions if p["pnl"] < 0]
    wins = len(win_pnls)
    losses = len(loss_pnls)
    decided = wins + losses
    win_rate = round((wins / decided) * 100, 1) if decided else 0.0

    gross_profit = sum(win_pnls)
    gross_loss = sum(loss_pnls)
    avg_win = round(gross_profit / len(win_pnls), 2) if win_pnls else 0.0
    avg_loss = round(gross_loss / len(loss_pnls), 2) if loss_pnls else 0.0
    profit_factor = (
        round(gross_profit / gross_loss, 2)
        if gross_loss > 0
        else (float(len(win_pnls)) if gross_profit > 0 else 0.0)
    )
    expectancy = round(net_pnl / decided, 2) if decided else 0.0

    # Equity curve from the REAL recorded intraday P&L points for that date.
    pnl_rows = (
        await db.execute(
            select(IntradayPnlPoint)
            .where(
                IntradayPnlPoint.user_id == user_id,
                IntradayPnlPoint.trading_date == date,
            )
            .order_by(IntradayPnlPoint.ts.asc())
        )
    ).scalars().all()
    if pnl_rows:
        base = pnl_rows[0].ts
        equity_pts = [
            (round((r.ts - base).total_seconds() / 60, 1), round(r.pnl, 2))
            for r in pnl_rows
        ]
        equity_curve = [[x, y] for x, y in equity_pts]
        pnl_series = [r.pnl for r in pnl_rows]
        max_drawdown = _max_drawdown(pnl_series)
    else:
        equity_pts = []
        equity_curve = []
        pnl_series = []
        max_drawdown = 0.0

    # Per-instrument breakdown from live positions (real symbols + P&L).
    by_strategy = [
        {
            "name": p["symbol"],
            "trades": 1,
            "pnl": round(p["pnl"], 2),
            "win_rate": 100.0 if p["pnl"] > 0 else 0.0,
        }
        for p in positions
    ]

    # ── Alpha telemetry ───────────────────────────────────────────────────────
    # Risk-adjusted ratios from the increments of the real equity curve. With
    # too few points these are not meaningful, so they stay at 0.
    sharpe, sortino = _risk_adjusted_ratios(pnl_series)

    # Underwater curve + peak drawdown percentage, aligned to the equity x-axis.
    drawdown_curve, max_drawdown_pct = _drawdown_curve(equity_pts)

    # Recovery factor: how many times net profit covers the worst drawdown.
    recovery_factor = (
        round(net_pnl / max_drawdown, 2) if max_drawdown > 0 else 0.0
    )

    # Return on deployed capital (percent) — real only when capital is known.
    deployed = round(sum(abs(p["qty"] * p["avg_price"]) for p in positions), 2) \
        if positions and all("avg_price" in p for p in positions) else 0.0
    return_on_capital_pct = (
        round((net_pnl / deployed) * 100, 2) if deployed > 0 else 0.0
    )

    # Alpha attribution: each instrument's share of the gross contribution.
    alpha_attribution = _alpha_attribution(by_strategy)

    # Model correlation across active strategies. Without a runtime returns
    # stream per strategy this is not computable, so report a truthful 0.
    model_correlation = 0.0

    # Daily heatmap over the trailing 30 calendar days from recorded P&L.
    daily_heatmap = await _daily_heatmap(db, user_id=user_id, anchor_date=date)

    # Benchmark (NIFTY) overlay: only if a live index quote is available. We do
    # not synthesise a benchmark path, so this stays empty until intraday index
    # history is recorded — the UI simply omits the benchmark line.
    benchmark_curve: list[list[float]] = []

    net_yield = round(net_pnl - total_charges, 2)

    expectancy_rows = _expectancy_rows(
        avg_win=avg_win,
        avg_loss=avg_loss,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        max_drawdown=max_drawdown,
    )

    tone = "profit" if net_pnl > 0 else "loss" if net_pnl < 0 else "neutral"
    kpis = [
        {"label": "Cumulative Realized Alpha", "value": f"₹{net_pnl:,.2f}",
         "tone": tone, "change_pct": return_on_capital_pct or None},
        {"label": "Overall Win Rate", "value": f"{win_rate:.1f}%", "tone": "neutral"},
        {"label": "Profit Factor", "value": (
            "∞" if profit_factor == float("inf") else f"{profit_factor:.2f}"
        ), "tone": "neutral"},
        {"label": "Sharpe / Sortino",
         "value": f"{sharpe:.2f} / {sortino:.2f}", "tone": "neutral"},
        {"label": "Max Drawdown", "value": f"{max_drawdown_pct:.1f}%",
         "tone": "loss" if max_drawdown_pct < 0 else "neutral"},
        {"label": "Brokerage & Taxes", "value": f"₹{total_charges:,.2f}",
         "tone": "neutral"},
    ]

    return {
        "kpis": kpis,
        "net_pnl": net_pnl,
        "gross_pnl": gross_pnl,
        "total_charges": total_charges,
        "expectancy": expectancy,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "trades_total": trades_total,
        "wins": wins,
        "losses": losses,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "equity_curve": equity_curve,
        "by_strategy": by_strategy,
        # Alpha telemetry
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": max_drawdown_pct,
        "recovery_factor": recovery_factor,
        "return_on_capital_pct": return_on_capital_pct,
        "net_yield": net_yield,
        "benchmark_curve": benchmark_curve,
        "drawdown_curve": drawdown_curve,
        "alpha_attribution": alpha_attribution,
        "model_correlation": model_correlation,
        "daily_heatmap": daily_heatmap,
        "expectancy_rows": expectancy_rows,
        "small_sample": trades_total < 30,
    }


def _max_drawdown(pnls: list[float]) -> float:
    """Worst peak-to-trough decline on a cumulative curve, as a positive number.

    The opening equity (0) is the initial high-water mark, so being underwater
    from the start counts as drawdown.
    """
    peak = 0.0
    worst = 0.0
    for equity in pnls:
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return round(worst, 2)


def _risk_adjusted_ratios(equity: list[float]) -> tuple[float, float]:
    """Annualised-free Sharpe and Sortino from a cumulative equity series.

    We difference the cumulative P&L into per-interval increments (the actual
    returns of the run), then Sharpe = mean/stdev and Sortino = mean/downside
    deviation. With a zero risk-free rate this is the raw reward-to-variability
    ratio. Fewer than 3 increments is not enough signal → return (0, 0).
    """
    if len(equity) < 4:
        return 0.0, 0.0
    increments = [equity[i] - equity[i - 1] for i in range(1, len(equity))]
    n = len(increments)
    mean = sum(increments) / n
    var = sum((r - mean) ** 2 for r in increments) / n
    std = var ** 0.5
    downside = [min(0.0, r) for r in increments]
    dvar = sum(d * d for d in downside) / n
    ddev = dvar ** 0.5
    sharpe = round(mean / std, 2) if std > 1e-9 else 0.0
    sortino = round(mean / ddev, 2) if ddev > 1e-9 else 0.0
    return sharpe, sortino


def _drawdown_curve(
    equity_pts: list[tuple[float, float]],
) -> tuple[list[list[float]], float]:
    """Underwater curve as [x, drawdown_pct] where drawdown_pct ≤ 0.

    Drawdown is measured against the running high-water mark. Because a P&L
    curve can start at 0, we express drawdown as a percentage of the peak
    equity's magnitude; when the peak is ~0 (no gains yet) the percentage is 0
    to avoid a divide-by-noise. Returns the curve and the worst (most negative)
    percentage seen.
    """
    if not equity_pts:
        return [], 0.0
    peak = 0.0
    curve: list[list[float]] = []
    worst_pct = 0.0
    for x, equity in equity_pts:
        peak = max(peak, equity)
        dd = equity - peak                      # ≤ 0
        denom = abs(peak) if abs(peak) > 1e-6 else 0.0
        dd_pct = round((dd / denom) * 100, 2) if denom else 0.0
        curve.append([x, dd_pct])
        worst_pct = min(worst_pct, dd_pct)
    return curve, worst_pct


def _alpha_attribution(by_strategy: list[dict]) -> list[dict]:
    """Each row's share of the total gross (absolute) contribution, 0-100."""
    gross = sum(abs(r["pnl"]) for r in by_strategy)
    if gross <= 0:
        return [
            {"name": r["name"], "pnl": r["pnl"], "contribution_pct": 0.0}
            for r in by_strategy
        ]
    rows = [
        {
            "name": r["name"],
            "pnl": r["pnl"],
            "contribution_pct": round((abs(r["pnl"]) / gross) * 100, 1),
        }
        for r in by_strategy
    ]
    rows.sort(key=lambda r: r["contribution_pct"], reverse=True)
    return rows


async def _daily_heatmap(
    db: AsyncSession, *, user_id: uuid.UUID, anchor_date: str, days: int = 30
) -> list[dict]:
    """Trailing 30-calendar-day P&L heatmap from recorded intraday points.

    A day's P&L is the last recorded cumulative value for that trading_date
    (the day's closing session P&L). Days with no recorded session render as
    'none' (weekend/holiday/no activity) — never invented.
    """
    from datetime import timedelta

    anchor = datetime.strptime(anchor_date, "%Y-%m-%d").date()
    start = anchor - timedelta(days=days - 1)

    rows = (
        await db.execute(
            select(
                IntradayPnlPoint.trading_date,
                func.max(IntradayPnlPoint.ts).label("last_ts"),
            )
            .where(
                IntradayPnlPoint.user_id == user_id,
                IntradayPnlPoint.trading_date >= start.strftime("%Y-%m-%d"),
                IntradayPnlPoint.trading_date <= anchor_date,
            )
            .group_by(IntradayPnlPoint.trading_date)
        )
    ).all()

    # Resolve the closing P&L for each date that has data.
    closing: dict[str, float] = {}
    for trading_date, last_ts in rows:
        point = (
            await db.execute(
                select(IntradayPnlPoint.pnl).where(
                    IntradayPnlPoint.user_id == user_id,
                    IntradayPnlPoint.trading_date == trading_date,
                    IntradayPnlPoint.ts == last_ts,
                )
            )
        ).scalars().first()
        if point is not None:
            closing[trading_date] = round(point, 2)

    cells: list[dict] = []
    for i in range(days):
        d = start + timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        if key in closing:
            pnl = closing[key]
            tone = "profit" if pnl > 0 else "loss" if pnl < 0 else "flat"
            cells.append({"date": key, "day": d.day, "pnl": pnl, "tone": tone})
        else:
            cells.append({"date": key, "day": d.day, "pnl": None, "tone": "none"})
    return cells


def reports_to_csv(data: dict) -> str:
    """Render a computed reports payload as a CSV document.

    Sections are written one after another with blank-line separators so the
    file opens cleanly in a spreadsheet. Only real computed values are written.
    """
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(["Quant Reports & Alpha Telemetry"])
    writer.writerow([])

    writer.writerow(["Summary Metric", "Value"])
    summary = [
        ("Net P&L", f"{data['net_pnl']:.2f}"),
        ("Gross P&L", f"{data['gross_pnl']:.2f}"),
        ("Total Charges", f"{data['total_charges']:.2f}"),
        ("Net Yield", f"{data.get('net_yield', 0.0):.2f}"),
        ("Win Rate %", f"{data['win_rate']:.1f}"),
        ("Profit Factor", f"{data['profit_factor']:.2f}"),
        ("Expectancy / Trade", f"{data['expectancy']:.2f}"),
        ("Sharpe", f"{data.get('sharpe', 0.0):.2f}"),
        ("Sortino", f"{data.get('sortino', 0.0):.2f}"),
        ("Max Drawdown", f"{data['max_drawdown']:.2f}"),
        ("Max Drawdown %", f"{data.get('max_drawdown_pct', 0.0):.2f}"),
        ("Recovery Factor", f"{data.get('recovery_factor', 0.0):.2f}"),
        ("Return on Capital %", f"{data.get('return_on_capital_pct', 0.0):.2f}"),
        ("Trades Total", str(data["trades_total"])),
        ("Wins", str(data["wins"])),
        ("Losses", str(data["losses"])),
        ("Avg Win", f"{data['avg_win']:.2f}"),
        ("Avg Loss", f"{data['avg_loss']:.2f}"),
    ]
    for label, value in summary:
        writer.writerow([label, value])

    writer.writerow([])
    writer.writerow(["Strategy Alpha Attribution", "P&L", "Contribution %"])
    for row in data.get("alpha_attribution", []):
        writer.writerow(
            [row["name"], f"{row['pnl']:.2f}", f"{row['contribution_pct']:.1f}"]
        )

    writer.writerow([])
    writer.writerow(["Execution Metric", "Value", "Benchmark", "Status", "Risk Score"])
    for row in data.get("expectancy_rows", []):
        writer.writerow(
            [row["metric"], row["value"], row["benchmark"], row["status"],
             f"{row['risk_score']:.2f}"]
        )

    writer.writerow([])
    writer.writerow(["Date", "Daily Net P&L"])
    for cell in data.get("daily_heatmap", []):
        if cell["pnl"] is not None:
            writer.writerow([cell["date"], f"{cell['pnl']:.2f}"])

    return buf.getvalue()


def _expectancy_rows(
    *,
    avg_win: float,
    avg_loss: float,
    win_rate: float,
    profit_factor: float,
    expectancy: float,
    max_drawdown: float,
) -> list[dict]:
    """Trade-expectancy / execution breakdown rows, computed from real stats.

    Benchmarks are conventional retail-desk reference points; status labels are
    derived by comparing the real value to that reference. Nothing here is a
    fabricated measurement — each 'value' comes from the user's own numbers.
    """
    payoff = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0
    rows = [
        {
            "metric": "Average Trade Win",
            "value": f"+₹{avg_win:,.2f}",
            "benchmark": "₹850.00 Median",
            "status": "OPTIMAL" if avg_win >= 850 else "TRACKING",
            "tone": "profit" if avg_win > 0 else "neutral",
            "risk_score": round(min(avg_win / 8000, 1.0), 2) if avg_win > 0 else 0.0,
        },
        {
            "metric": "Average Trade Loss",
            "value": f"−₹{avg_loss:,.2f}",
            "benchmark": "−₹500.00 Stop Threshold",
            "status": "CONTROLLED" if avg_loss <= 500 else "REVIEW",
            "tone": "loss" if avg_loss > 0 else "neutral",
            "risk_score": round(min(avg_loss / 5000, 1.0), 2) if avg_loss > 0 else 0.0,
        },
        {
            "metric": "Win / Loss Payoff Ratio",
            "value": f"{payoff:.2f} : 1",
            "benchmark": "> 1.80 Target",
            "status": "SUPERIOR" if payoff >= 1.8 else "BUILDING",
            "tone": "profit" if payoff >= 1.0 else "neutral",
            "risk_score": round(max(0.0, min((2.0 - payoff) / 2.0, 1.0)), 2)
            if payoff > 0 else 0.0,
        },
        {
            "metric": "Expectancy / Trade",
            "value": f"₹{expectancy:,.2f}",
            "benchmark": "> ₹0 Positive Edge",
            "status": "POSITIVE" if expectancy > 0 else "NEGATIVE"
            if expectancy < 0 else "FLAT",
            "tone": "profit" if expectancy > 0 else "loss"
            if expectancy < 0 else "neutral",
            "risk_score": 0.0,
        },
        {
            "metric": "Profit Factor",
            "value": "∞" if profit_factor == float("inf") else f"{profit_factor:.2f}",
            "benchmark": "> 1.50 Institutional",
            "status": "SUPERIOR" if profit_factor >= 1.5 else "BUILDING",
            "tone": "profit" if profit_factor >= 1.0 else "neutral",
            "risk_score": 0.0,
        },
    ]
    return rows
