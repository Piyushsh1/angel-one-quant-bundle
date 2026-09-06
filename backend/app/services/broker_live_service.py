"""
================================================================================
services/broker_live_service.py  ▸  Live broker data (per user, real-time)
================================================================================
Reads REAL data from a user's connected broker using the session tokens stored
(encrypted) at connect time. Everything the dashboard shows — wallet/available
margin, open positions, order book, holdings, and the index ticker — flows
through here.

Guarantees:
  · Per-user isolation: every call decrypts and uses THAT user's own tokens.
  · No fabrication: if the user has no broker connected, or a live call fails,
    the functions return an explicit "unavailable" result. The caller renders an
    honest empty/zero state — never invented numbers.
  · Self-healing session: Angel One JWTs expire; on an auth failure we mint a
    fresh JWT from the stored refresh token and persist it, transparently.

This module maps Angel One's raw payloads into the internal shapes the dashboard
service already understands (Position/Order-like dicts, margin floats, quotes).
================================================================================
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.infrastructure.brokers import angelone
from app.infrastructure.brokers.angelone import BrokerSessionError
from app.infrastructure.database.models import BrokerConnection, RiskParameters

log = logging.getLogger("broker.live")


# Real Angel One spot-index instrument tokens (from the trading engine's map).
# Used to pull live LTP for the top-of-screen ticker.
INDEX_INSTRUMENTS = (
    {"label": "NIFTY 50", "exchange": "NSE", "symbol": "Nifty 50", "token": "99926000"},
    {"label": "BANKNIFTY", "exchange": "NSE", "symbol": "Nifty Bank", "token": "99926009"},
    {"label": "SENSEX", "exchange": "BSE", "symbol": "SENSEX", "token": "99919000"},
)


@dataclass
class _Session:
    """A usable, decrypted broker session for one user."""

    conn: BrokerConnection
    api_key: str
    jwt_token: str
    refresh_token: str | None


class LiveUnavailable(Exception):
    """No live broker data is available (not connected / session dead)."""

    def __init__(self, reason: str, *, needs_reconnect: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.needs_reconnect = needs_reconnect


async def _load_session(
    db: AsyncSession, *, user_id: uuid.UUID
) -> _Session:
    """Decrypt the caller's stored broker session, or raise LiveUnavailable."""
    conn = (
        await db.execute(
            select(BrokerConnection)
            .where(
                BrokerConnection.user_id == user_id,
                BrokerConnection.is_active.is_(True),
            )
            .order_by(BrokerConnection.last_verified_at.desc())
        )
    ).scalars().first()

    if conn is None:
        raise LiveUnavailable("No broker connected")
    if not conn.enc_api_key or not conn.enc_jwt_token:
        raise LiveUnavailable("Broker session missing", needs_reconnect=True)

    try:
        api_key = security.decrypt_secret(conn.enc_api_key)
        jwt_token = security.decrypt_secret(conn.enc_jwt_token)
        refresh_token = (
            security.decrypt_secret(conn.enc_refresh_token)
            if conn.enc_refresh_token
            else None
        )
    except ValueError as exc:
        raise LiveUnavailable("Broker session unreadable", needs_reconnect=True) from exc

    return _Session(
        conn=conn, api_key=api_key, jwt_token=jwt_token, refresh_token=refresh_token
    )


async def _with_session(db: AsyncSession, sess: _Session, call):
    """Run an authenticated broker call, refreshing the JWT once on expiry.

    `call` is an async callable taking a jwt token. On a session-expiry error we
    mint a new JWT from the refresh token, persist it, and retry once.
    """
    try:
        return await call(sess.jwt_token)
    except BrokerSessionError as first:
        if not first.needs_reconnect or not sess.refresh_token:
            raise LiveUnavailable(first.message, needs_reconnect=True) from first
        # Try a single refresh.
        new_jwt = await angelone.refresh_jwt(
            api_key=sess.api_key, refresh_token=sess.refresh_token
        )
        if not new_jwt:
            raise LiveUnavailable(
                "Broker session expired — please reconnect.", needs_reconnect=True
            ) from first
        sess.jwt_token = new_jwt
        sess.conn.enc_jwt_token = security.encrypt_secret(new_jwt)
        sess.conn.last_verified_at = datetime.now(timezone.utc)
        try:
            return await call(new_jwt)
        except BrokerSessionError as second:
            raise LiveUnavailable(
                second.message, needs_reconnect=True
            ) from second


# ── Public API ────────────────────────────────────────────────────────────────

async def is_connected(db: AsyncSession, *, user_id: uuid.UUID) -> bool:
    try:
        await _load_session(db, user_id=user_id)
        return True
    except LiveUnavailable:
        return False


async def get_margin(db: AsyncSession, *, user_id: uuid.UUID) -> dict:
    """Live RMS margin. Returns the raw useful fields, all real from the broker.

    Keys: available_cash, net, utilised_debits, collateral, total (available +
    utilised). Raises LiveUnavailable when there is no usable session.
    """
    sess = await _load_session(db, user_id=user_id)

    async def _call(jwt: str) -> dict:
        return await angelone.fetch_rms(api_key=sess.api_key, jwt_token=jwt)

    rms = await _with_session(db, sess, _call)
    available = _f(rms.get("availablecash"))
    utilised = _f(rms.get("utiliseddebits"))
    net = _f(rms.get("net"))
    collateral = _f(rms.get("collateral"))
    total = None
    if available is not None or utilised is not None:
        total = round((available or 0.0) + (utilised or 0.0), 2)
    return {
        "available_cash": available,
        "utilised_debits": utilised,
        "net": net,
        "collateral": collateral,
        "total": total,
    }


async def get_positions(db: AsyncSession, *, user_id: uuid.UUID) -> list[dict]:
    """Live positions mapped to the dashboard's internal shape."""
    sess = await _load_session(db, user_id=user_id)

    async def _call(jwt: str) -> list[dict]:
        return await angelone.fetch_positions(api_key=sess.api_key, jwt_token=jwt)

    raw = await _with_session(db, sess, _call)
    out: list[dict] = []
    for p in raw:
        net_qty = _i(p.get("netqty"))
        if net_qty == 0:
            continue  # only currently-open positions
        avg = _f(p.get("netprice")) or _f(p.get("avgnetprice")) or 0.0
        ltp = _f(p.get("ltp")) or 0.0
        pnl = _f(p.get("pnl"))
        if pnl is None:
            pnl = round((ltp - avg) * net_qty, 2)
        pnl_pct = round((pnl / (abs(avg * net_qty))) * 100, 2) if avg and net_qty else 0.0
        out.append(
            {
                "id": f"{p.get('symboltoken', '')}-{p.get('tradingsymbol', '')}",
                "side": "BUY" if net_qty > 0 else "SELL",
                "symbol": p.get("tradingsymbol") or p.get("symbolname") or "—",
                "qty": abs(net_qty),
                "avg_price": round(avg, 2),
                "ltp": round(ltp, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": pnl_pct,
            }
        )
    return out


async def get_orders(db: AsyncSession, *, user_id: uuid.UUID) -> list[dict]:
    """Live order book mapped to the dashboard's internal order shape."""
    sess = await _load_session(db, user_id=user_id)

    async def _call(jwt: str) -> list[dict]:
        return await angelone.fetch_orderbook(api_key=sess.api_key, jwt_token=jwt)

    raw = await _with_session(db, sess, _call)
    out: list[dict] = []
    for o in raw:
        out.append(
            {
                "id": str(o.get("orderid") or o.get("uniqueorderid") or ""),
                "side": (o.get("transactiontype") or "").upper(),
                "symbol": o.get("tradingsymbol") or "—",
                "time": _order_time(o.get("updatetime") or o.get("exchtime") or ""),
                "qty": _i(o.get("quantity")),
                "price": _f(o.get("averageprice")) or _f(o.get("price")) or 0.0,
                "status": _map_status(o.get("orderstatus") or o.get("status") or ""),
            }
        )
    return out


async def get_holdings(db: AsyncSession, *, user_id: uuid.UUID) -> list[dict]:
    """Live demat holdings."""
    sess = await _load_session(db, user_id=user_id)

    async def _call(jwt: str) -> list[dict]:
        return await angelone.fetch_holdings(api_key=sess.api_key, jwt_token=jwt)

    raw = await _with_session(db, sess, _call)
    out: list[dict] = []
    for h in raw:
        qty = _i(h.get("quantity"))
        avg = _f(h.get("averageprice")) or 0.0
        ltp = _f(h.get("ltp")) or 0.0
        pnl = _f(h.get("profitandloss"))
        if pnl is None:
            pnl = round((ltp - avg) * qty, 2)
        out.append(
            {
                "symbol": h.get("tradingsymbol") or "—",
                "qty": qty,
                "avg_price": round(avg, 2),
                "ltp": round(ltp, 2),
                "pnl": round(pnl, 2),
            }
        )
    return out


async def get_index_quotes(db: AsyncSession, *, user_id: uuid.UUID) -> list[dict]:
    """Live NIFTY/BANKNIFTY/SENSEX quotes for the ticker, all real LTP."""
    sess = await _load_session(db, user_id=user_id)
    out: list[dict] = []
    for inst in INDEX_INSTRUMENTS:
        async def _call(jwt: str, inst=inst) -> dict | None:
            return await angelone.fetch_ltp(
                api_key=sess.api_key,
                jwt_token=jwt,
                exchange=inst["exchange"],
                symbol=inst["symbol"],
                token=inst["token"],
            )

        try:
            data = await _with_session(db, sess, _call)
        except LiveUnavailable:
            data = None
        if not data:
            continue
        ltp = _f(data.get("ltp"))
        close = _f(data.get("close"))
        change_pct = 0.0
        if ltp is not None and close:
            change_pct = round(((ltp - close) / close) * 100, 2)
        out.append(
            {
                "label": inst["label"],
                "value": f"{ltp:,.2f}" if ltp is not None else "—",
                "change_pct": change_pct,
            }
        )
    return out


# ── Mapping helpers ────────────────────────────────────────────────────────────

_STATUS_MAP = {
    "complete": "FILLED",
    "traded": "FILLED",
    "executed": "FILLED",
    "rejected": "REJECTED",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "open": "PENDING",
    "pending": "PENDING",
    "trigger pending": "PENDING",
    "open pending": "PENDING",
    "modified": "PENDING",
}


def _map_status(raw: str) -> str:
    return _STATUS_MAP.get(raw.strip().lower(), (raw or "PENDING").upper())


def _order_time(raw: str) -> str:
    """Angel One times look like 'DD-Mon-YYYY HH:MM:SS' or ISO. Return HH:MM:SS."""
    raw = (raw or "").strip()
    if not raw:
        return "—"
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    # Fall back to the trailing time component if present.
    parts = raw.split(" ")
    return parts[-1] if parts else raw


def _f(value) -> float | None:  # type: ignore[no-untyped-def]
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _i(value) -> int:  # type: ignore[no-untyped-def]
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


# ══════════════════════════════════════════════════════════════════════════════
#  ORDER PLACEMENT — the single gated chokepoint (paper vs live)
# ══════════════════════════════════════════════════════════════════════════════
#  EVERY order — from a dashboard action or the future strategy/trade engine —
#  MUST go through place_order() here. It reads the user's persisted trading
#  mode and, unless live trading is explicitly enabled, NEVER contacts the
#  broker: it returns a simulated fill instead. This is enforced server-side, so
#  a compromised or buggy client cannot place a real order while in paper mode.


class OrderRejected(Exception):
    """Raised when an order cannot be placed (e.g. kill switch armed)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


async def is_live_trading(db: AsyncSession, *, user_id: uuid.UUID) -> bool:
    """Whether the user has explicitly enabled live trading. Default: False."""
    params = (
        await db.execute(
            select(RiskParameters).where(RiskParameters.user_id == user_id)
        )
    ).scalar_one_or_none()
    return bool(params and params.live_trading_enabled)


async def place_order(
    db: AsyncSession, *, user_id: uuid.UUID, order: dict
) -> dict:
    """Place an order, honoring the user's paper/live mode.

    Returns a result dict with a ``mode`` of ``"paper"`` or ``"live"``.

    - Kill switch armed  → OrderRejected (no order in any mode).
    - Live NOT enabled   → PAPER: simulate the fill, never touch the broker.
    - Live enabled       → route the real order to the broker.

    This is the only place an order can become real. There is no other path.
    """
    params = (
        await db.execute(
            select(RiskParameters).where(RiskParameters.user_id == user_id)
        )
    ).scalar_one_or_none()

    # Kill switch overrides everything, in either mode.
    if params is not None and params.kill_switch_armed:
        raise OrderRejected("Kill switch is armed — new orders are blocked.")

    live = bool(params and params.live_trading_enabled)

    if not live:
        # PAPER MODE: never contact the broker. Report a simulated acceptance.
        log.info("PAPER order (not sent to broker) user=%s symbol=%s",
                 user_id, order.get("tradingsymbol"))
        return {
            "mode": "paper",
            "status": "simulated",
            "order": order,
            "message": "Paper mode — order simulated, not sent to the broker.",
        }

    # LIVE MODE: load the session and send the real order to the broker.
    sess = await _load_session(db, user_id=user_id)

    async def _call(jwt: str) -> dict:
        return await angelone.place_order(
            api_key=sess.api_key, jwt_token=jwt, order=order
        )

    result = await _with_session(db, sess, _call)
    log.warning("LIVE order sent to broker user=%s symbol=%s",
                user_id, order.get("tradingsymbol"))
    return {"mode": "live", "status": "placed", "broker": result}
