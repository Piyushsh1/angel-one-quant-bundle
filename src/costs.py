"""
================================================================================
costs.py  ▸  Transaction-cost model (Indian equity + F&O)
================================================================================
WHY THIS EXISTS
───────────────
Paper trading is only useful if its P&L resembles what a real account would
have made. Before this module, a simulated fill happened at the exact LTP with
zero charges, which flatters every result: a strategy showing a small positive
expectancy on paper can be solidly negative once brokerage, STT, exchange fees,
GST, stamp duty and slippage are taken out.

For an intraday options buyer this is not a rounding error. On a ₹5,000 silo,
round-trip costs can consume a large share of a small winner, and they are
charged on losers too.

So paper and pre-prod now apply the SAME cost model that a live account would
incur. The number you read on the dashboard is then comparable to a real
brokerage statement, which is the whole point of a rehearsal.

WHAT IT MODELS
──────────────
    slippage   — market orders do not fill at the quoted LTP
    brokerage  — flat per executed order (Angel One style)
    STT        — securities transaction tax
    exchange   — NSE transaction charges
    SEBI       — turnover fee
    GST        — on (brokerage + exchange + SEBI)
    stamp duty — on the buy leg only

⚠️  RATES CHANGE. Every rate below is an env-overridable default, not a promise.
    Verify against your own contract notes and update the env file. Treat the
    defaults as "approximately right as of 2026", not as authoritative. If the
    modelled charge disagrees with your broker's statement, the statement wins.

USAGE
─────
    import costs

    # cost of one leg
    c = costs.leg_cost(segment="OPTIONS", side="BUY", price=74.65, qty=65)

    # full round trip, and P&L net of every charge
    rt = costs.round_trip(segment="OPTIONS", entry=74.65, exit_=80.10, qty=65)
    rt.net_pnl        # what actually lands in the account
    rt.total_charges  # what the round trip cost you

    # simulated fill price for a paper market order
    px = costs.apply_slippage(74.65, side="BUY", segment="OPTIONS")
================================================================================
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger("costs")


def _f(key: str, default: float) -> float:
    """Read a float from the environment, falling back to ``default``."""
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("%s=%r is not a float — using default %s", key, raw, default)
        return default


# ════════════════════════════════════════════════════════════════════════════
#  RATE TABLE  (all env-overridable — verify against your contract notes)
# ════════════════════════════════════════════════════════════════════════════
#: Flat brokerage per executed order. Angel One charges ₹20/order on F&O.
#: Equity delivery is often ₹0, hence the separate keys.
BROKERAGE_PER_ORDER_FO: float = _f("COST_BROKERAGE_FO", 20.0)
BROKERAGE_PER_ORDER_EQ: float = _f("COST_BROKERAGE_EQ", 0.0)

#: Brokerage is capped as a % of turnover (whichever is lower applies).
BROKERAGE_PCT_CAP: float = _f("COST_BROKERAGE_PCT_CAP", 0.0025)   # 0.25%

#: STT — options: on the SELL leg, charged on premium.
STT_OPTIONS_SELL_PCT: float = _f("COST_STT_OPTIONS_SELL", 0.001)   # 0.10%
#: STT — equity delivery: BOTH legs.
STT_EQUITY_DELIVERY_PCT: float = _f("COST_STT_EQUITY_DELIVERY", 0.001)  # 0.10%

#: NSE exchange transaction charges, on premium for options.
EXCHANGE_TXN_OPTIONS_PCT: float = _f("COST_EXCH_OPTIONS", 0.0003503)  # 0.03503%
EXCHANGE_TXN_EQUITY_PCT: float = _f("COST_EXCH_EQUITY", 0.0000297)    # 0.00297%

#: SEBI turnover fee — ₹10 per crore.
SEBI_FEE_PCT: float = _f("COST_SEBI_FEE", 0.000001)

#: GST on (brokerage + exchange charges + SEBI fee).
GST_PCT: float = _f("COST_GST", 0.18)

#: Stamp duty — BUY leg only.
STAMP_DUTY_OPTIONS_PCT: float = _f("COST_STAMP_OPTIONS", 0.00003)   # 0.003%
STAMP_DUTY_EQUITY_PCT: float = _f("COST_STAMP_EQUITY", 0.00015)     # 0.015%

# ── SLIPPAGE ──────────────────────────────────────────────────────────────
# A MARKET order does not fill at the quoted LTP. It crosses the spread and,
# on a fast move, worse than that. Modelled as a percentage of price applied
# ADVERSELY: buys fill higher, sells fill lower. Never favourably — an
# optimistic slippage model is worse than none, because it hides the cost.
#
# 0.25% is a deliberately conservative default for index options, which can
# carry wide spreads away from ATM. Tighten it only if your own fills justify
# it — compare modelled vs actual fills after a few live sessions.
SLIPPAGE_PCT_OPTIONS: float = _f("COST_SLIPPAGE_OPTIONS", 0.0025)   # 0.25%
SLIPPAGE_PCT_EQUITY: float = _f("COST_SLIPPAGE_EQUITY", 0.0005)     # 0.05%

#: Set COST_MODEL_ENABLED=false to get raw gross P&L (not recommended —
#: it makes paper results incomparable to a real account).
ENABLED: bool = os.getenv("COST_MODEL_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "y",
)

OPTIONS_SEGMENTS = ("OPTIONS", "FO", "NFO")


# ════════════════════════════════════════════════════════════════════════════
#  RESULT TYPES
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class LegCost:
    """Itemised charges for a single order leg."""
    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    gst: float = 0.0
    stamp_duty: float = 0.0

    @property
    def total(self) -> float:
        return round(
            self.brokerage + self.stt + self.exchange
            + self.sebi + self.gst + self.stamp_duty,
            2,
        )

    def as_dict(self) -> dict:
        return {
            "brokerage": round(self.brokerage, 2),
            "stt": round(self.stt, 2),
            "exchange": round(self.exchange, 2),
            "sebi": round(self.sebi, 2),
            "gst": round(self.gst, 2),
            "stamp_duty": round(self.stamp_duty, 2),
            "total": self.total,
        }


@dataclass
class RoundTrip:
    """Entry + exit charges and the resulting net P&L."""
    gross_pnl: float
    entry_cost: LegCost = field(default_factory=LegCost)
    exit_cost: LegCost = field(default_factory=LegCost)

    @property
    def total_charges(self) -> float:
        return round(self.entry_cost.total + self.exit_cost.total, 2)

    @property
    def net_pnl(self) -> float:
        return round(self.gross_pnl - self.total_charges, 2)

    def as_dict(self) -> dict:
        return {
            "gross_pnl": round(self.gross_pnl, 2),
            "total_charges": self.total_charges,
            "net_pnl": self.net_pnl,
            "entry_charges": self.entry_cost.as_dict(),
            "exit_charges": self.exit_cost.as_dict(),
        }


# ════════════════════════════════════════════════════════════════════════════
#  CORE CALCULATIONS
# ════════════════════════════════════════════════════════════════════════════
def _is_options(segment: str) -> bool:
    return str(segment).upper() in OPTIONS_SEGMENTS


def leg_cost(*, segment: str, side: str, price: float, qty: int) -> LegCost:
    """Return itemised charges for ONE leg (a single executed order).

    ``segment`` is ``"OPTIONS"`` (premium-based charges) or ``"EQUITY"``.
    ``side`` is ``"BUY"`` or ``"SELL"`` — several charges are side-specific.
    """
    c = LegCost()
    if not ENABLED:
        return c

    turnover = abs(float(price) * int(qty))
    if turnover <= 0:
        return c

    is_opt = _is_options(segment)
    is_buy = str(side).upper() == "BUY"

    # Brokerage — flat per order, capped at a % of turnover.
    flat = BROKERAGE_PER_ORDER_FO if is_opt else BROKERAGE_PER_ORDER_EQ
    c.brokerage = min(flat, turnover * BROKERAGE_PCT_CAP) if flat else 0.0

    if is_opt:
        # STT on options is charged on the SELL leg only.
        c.stt = 0.0 if is_buy else turnover * STT_OPTIONS_SELL_PCT
        c.exchange = turnover * EXCHANGE_TXN_OPTIONS_PCT
        c.stamp_duty = turnover * STAMP_DUTY_OPTIONS_PCT if is_buy else 0.0
    else:
        # Equity delivery STT applies to both legs.
        c.stt = turnover * STT_EQUITY_DELIVERY_PCT
        c.exchange = turnover * EXCHANGE_TXN_EQUITY_PCT
        c.stamp_duty = turnover * STAMP_DUTY_EQUITY_PCT if is_buy else 0.0

    c.sebi = turnover * SEBI_FEE_PCT
    # GST applies to brokerage + exchange + SEBI, NOT to STT or stamp duty.
    c.gst = (c.brokerage + c.exchange + c.sebi) * GST_PCT
    return c


def round_trip(*, segment: str, entry: float, exit_: float, qty: int,
               side: str = "BUY") -> RoundTrip:
    """Charges and net P&L for a complete round trip.

    ``side`` is the direction of the OPENING leg — ``"BUY"`` for a long
    (every strategy in this system is long-only: it buys options or equity).
    """
    opening = str(side).upper()
    closing = "SELL" if opening == "BUY" else "BUY"
    sign = 1.0 if opening == "BUY" else -1.0
    gross = (float(exit_) - float(entry)) * int(qty) * sign
    return RoundTrip(
        gross_pnl=gross,
        entry_cost=leg_cost(segment=segment, side=opening, price=entry, qty=qty),
        exit_cost=leg_cost(segment=segment, side=closing, price=exit_, qty=qty),
    )


def net_pnl(*, segment: str, entry: float, exit_: float, qty: int,
            side: str = "BUY") -> float:
    """Convenience: round-trip P&L after all charges."""
    return round_trip(
        segment=segment, entry=entry, exit_=exit_, qty=qty, side=side
    ).net_pnl


def exit_charges(*, segment: str, price: float, qty: int,
                 side: str = "SELL") -> float:
    """Charges for a closing leg alone.

    Used by partial exits, where the entry cost was already accounted for
    when the position was opened.
    """
    return leg_cost(segment=segment, side=side, price=price, qty=qty).total


# ════════════════════════════════════════════════════════════════════════════
#  SLIPPAGE
# ════════════════════════════════════════════════════════════════════════════
def apply_slippage(price: float, *, side: str, segment: str = "OPTIONS") -> float:
    """Return the realistic fill price for a MARKET order.

    Always applied ADVERSELY — buys fill higher, sells fill lower — because a
    model that ever helps you would understate real trading costs.
    """
    if not ENABLED:
        return round(float(price), 2)

    pct = SLIPPAGE_PCT_OPTIONS if _is_options(segment) else SLIPPAGE_PCT_EQUITY
    direction = 1.0 if str(side).upper() == "BUY" else -1.0
    filled = float(price) * (1.0 + direction * pct)
    # Never allow slippage to push a price to zero or below.
    return round(max(filled, 0.05), 2)


def segment_for(exchange: str) -> str:
    """Map a broker exchange code to a cost segment."""
    return "OPTIONS" if str(exchange).upper() in ("NFO", "MCX", "BFO") else "EQUITY"


# ════════════════════════════════════════════════════════════════════════════
#  SELF-TEST  (python src/costs.py — shows the real cost of a round trip)
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _log = logging.getLogger("costs")

    _log.info("Cost model enabled : %s", ENABLED)
    _log.info("Slippage (options) : %.3f%%", SLIPPAGE_PCT_OPTIONS * 100)
    _log.info("")

    # A real trade from the archived paper session: 65 × NIFTY CE @ 74.65 → 70.15
    _log.info("── Example: the recorded 2026-09-04 paper trade ──")
    rt = round_trip(segment="OPTIONS", entry=74.65, exit_=70.15, qty=65)
    _log.info("  65 × NIFTY CE  entry ₹74.65 → exit ₹70.15")
    _log.info("  gross P&L      : ₹%+.2f", rt.gross_pnl)
    _log.info("  entry charges  : ₹%.2f  %s", rt.entry_cost.total,
              rt.entry_cost.as_dict())
    _log.info("  exit charges   : ₹%.2f  %s", rt.exit_cost.total,
              rt.exit_cost.as_dict())
    _log.info("  total charges  : ₹%.2f", rt.total_charges)
    _log.info("  NET P&L        : ₹%+.2f", rt.net_pnl)
    _log.info("")

    # How much the underlying must move just to break even.
    _log.info("── Break-even move required (costs only, no slippage) ──")
    for prem, qty in ((74.65, 65), (150.0, 50), (250.0, 25)):
        rt2 = round_trip(segment="OPTIONS", entry=prem, exit_=prem, qty=qty)
        move_needed = rt2.total_charges / qty
        _log.info(
            "  %3d × ₹%-7.2f → charges ₹%7.2f  = ₹%.2f/unit (%.2f%% of premium)",
            qty, prem, rt2.total_charges, move_needed, move_needed / prem * 100,
        )
    _log.info("")
    _log.info("⚠️  Verify these rates against your own contract notes.")
