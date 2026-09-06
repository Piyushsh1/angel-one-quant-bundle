"""
================================================================================
strategy_engine/breakout.py  ▸  Opening-range breakout signal
================================================================================
A self-contained port of the opening-range breakout at the heart of the legacy
`src/option_predator.py`, adapted for the multi-user async backend. It is PURE
signal logic: given a user's live broker session, it decides whether to enter
a CALL or PUT on NIFTY / BANKNIFTY right now, and returns a concrete option
order — it does not place anything itself.

The rule (unchanged from the original engine):
  · Build the Opening Range (OR) from the first 15 minutes (09:15–09:30 IST):
    OR_high = max high, OR_low = min low of the 1-minute candles.
  · After 09:30, on each cycle read the latest price. A close above OR_high is a
    bullish breakout → buy an ATM CALL. A close below OR_low is bearish → buy an
    ATM PUT. No breakout → no trade.
  · One position per account at a time (enforced by the engine, not here).

Instrument tokens for the spot indices are the real Angel One tokens used by the
original bot. Option symbols are built for the nearest weekly expiry in Angel
One's format (e.g. ``NIFTY07AUG2524500CE``).
================================================================================
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from app.infrastructure.brokers import angelone
from app.infrastructure.brokers.angelone import BrokerSessionError

log = logging.getLogger("engine.breakout")

_IST = timezone(timedelta(hours=5, minutes=30))

SESSION_OPEN = time(9, 15)
OR_END = time(9, 30)
NO_NEW_ENTRY_AFTER = time(15, 0)   # stop opening new trades late in the session
SQUARE_OFF = time(15, 15)


# Real Angel One spot-index instruments + option meta. strike_step is the gap
# between listed strikes; lot_size is the F&O lot for one contract.
UNDERLYINGS = (
    {
        "name": "NIFTY",
        "token": "99926000",
        "exchange": "NSE",
        "strike_step": 50,
        "lot_size": 75,
        "weekly_expiry_weekday": 3,  # Thursday (kept simple; NSE weekly)
    },
    {
        "name": "BANKNIFTY",
        "token": "99926009",
        "exchange": "NSE",
        "strike_step": 100,
        "lot_size": 35,
        "weekly_expiry_weekday": 2,  # Wednesday
    },
)

_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


@dataclass(frozen=True)
class Signal:
    """A concrete entry the engine can place."""

    underlying: str
    direction: str          # "BULLISH" | "BEARISH"
    opt_type: str           # "CE" | "PE"
    spot: float
    strike: int
    trading_symbol: str      # e.g. NIFTY07AUG2524500CE
    exchange: str            # "NFO"
    lot_size: int
    qty: int                 # lot_size * lots
    reason: str


def now_ist() -> datetime:
    return datetime.now(_IST)


def market_is_open(nowt: datetime | None = None) -> bool:
    """NSE regular session, Mon–Fri 09:15–15:30 IST."""
    n = nowt or now_ist()
    if n.weekday() >= 5:
        return False
    return SESSION_OPEN <= n.time() <= time(15, 30)


def can_enter_new(nowt: datetime | None = None) -> bool:
    """Only open new trades after the OR forms and before the late cutoff."""
    n = nowt or now_ist()
    return OR_END <= n.time() < NO_NEW_ENTRY_AFTER and market_is_open(n)


def should_square_off(nowt: datetime | None = None) -> bool:
    n = nowt or now_ist()
    return n.time() >= SQUARE_OFF


def _nearest_weekly_expiry(weekday: int, today: date | None = None) -> date:
    """The next occurrence of `weekday` (Mon=0) on/after today."""
    d = today or now_ist().date()
    ahead = (weekday - d.weekday()) % 7
    return d + timedelta(days=ahead)


def _expiry_code(d: date) -> str:
    """Angel One weekly option date code, e.g. 07AUG25."""
    return f"{d.day:02d}{_MONTHS[d.month - 1]}{d.year % 100:02d}"


def _atm_strike(spot: float, step: int) -> int:
    return int(round(spot / step) * step)


def build_option_symbol(underlying: dict, strike: int, opt_type: str) -> str:
    """Angel One trading symbol for the nearest-weekly ATM option."""
    expiry = _nearest_weekly_expiry(underlying["weekly_expiry_weekday"])
    return f"{underlying['name']}{_expiry_code(expiry)}{strike}{opt_type}"


async def _fetch_or_and_last(
    *, api_key: str, jwt_token: str, underlying: dict
) -> tuple[float, float, float] | None:
    """Return (or_high, or_low, last_price) from today's 1-min candles.

    Fetches 09:15 → now so the OR can be rebuilt even if the engine started
    mid-session. Returns None when there aren't enough candles yet.
    """
    n = now_ist()
    start = datetime.combine(n.date(), SESSION_OPEN, tzinfo=_IST)
    frm = start.strftime("%Y-%m-%d %H:%M")
    to = n.strftime("%Y-%m-%d %H:%M")
    try:
        rows = await angelone.fetch_candles(
            api_key=api_key,
            jwt_token=jwt_token,
            exchange=underlying["exchange"],
            symbol_token=underlying["token"],
            interval="ONE_MINUTE",
            from_dt=frm,
            to_dt=to,
        )
    except BrokerSessionError:
        raise
    if not rows:
        return None

    # Rows: [ts, open, high, low, close, volume]. OR window = 09:15–09:30.
    or_highs: list[float] = []
    or_lows: list[float] = []
    last_close = 0.0
    for r in rows:
        try:
            ts = datetime.fromisoformat(r[0])
            high = float(r[2])
            low = float(r[3])
            close = float(r[4])
        except (ValueError, IndexError, TypeError):
            continue
        t = ts.time()
        if SESSION_OPEN <= t < OR_END:
            or_highs.append(high)
            or_lows.append(low)
        last_close = close  # last row wins → latest price

    if len(or_highs) < 5:
        return None  # OR not fully formed yet
    return max(or_highs), min(or_lows), last_close


async def generate_signal(
    *, api_key: str, jwt_token: str, lots: int = 1
) -> Signal | None:
    """Evaluate every underlying and return the first valid breakout entry.

    Returns None when there is no breakout on any underlying this cycle. Raises
    BrokerSessionError if the session is dead (caller refreshes/handles).
    """
    for u in UNDERLYINGS:
        res = await _fetch_or_and_last(
            api_key=api_key, jwt_token=jwt_token, underlying=u
        )
        if res is None:
            continue
        or_high, or_low, last = res
        if last <= 0:
            continue

        if last > or_high:
            direction, opt_type = "BULLISH", "CE"
        elif last < or_low:
            direction, opt_type = "BEARISH", "PE"
        else:
            continue  # inside the range — no breakout

        strike = _atm_strike(last, u["strike_step"])
        symbol = build_option_symbol(u, strike, opt_type)
        qty = u["lot_size"] * max(lots, 1)
        return Signal(
            underlying=u["name"],
            direction=direction,
            opt_type=opt_type,
            spot=round(last, 2),
            strike=strike,
            trading_symbol=symbol,
            exchange="NFO",
            lot_size=u["lot_size"],
            qty=qty,
            reason=(
                f"{u['name']} {direction} breakout: last {last:.2f} "
                f"{'>' if opt_type == 'CE' else '<'} OR "
                f"{or_high if opt_type == 'CE' else or_low:.2f}"
            ),
        )
    return None
