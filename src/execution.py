"""
================================================================================
execution.py  ▸  Single order-execution gateway (paper ⇆ live)
================================================================================
Every engine places orders through THIS module instead of calling ``broker``
directly.  The gateway decides once, centrally, whether an order is sent to
Angel One or simulated:

    config.PAPER_TRADING is True   →  simulate (log + synthetic order id)
    config.PAPER_TRADING is False  →  forward to broker.py (real money)

``config.PAPER_TRADING`` is derived from ``APP_ENV`` (dev / preprod → paper,
prod + ALLOW_LIVE_ORDERS → live), so the deployment environment alone decides
whether money moves.  No engine needs its own paper/live branching.

WHY THIS EXISTS
───────────────
Previously only the options engine had a paper mode, implemented as an inline
``PaperBroker`` plus ``if paper is not None: … else: …`` branches duplicated at
every order site.  Macro and smallcap had no paper mode at all and would place
real orders in any environment.  Routing all three engines through one gateway
removes that duplication and makes dev/preprod safe by construction.

READ-ONLY CALLS ARE ALWAYS REAL
───────────────────────────────
Quote fetches (``fetch_ltp``) are pass-through in every mode: paper trading is
meant to be a rehearsal against genuine live market data.  Only state-changing
order calls are simulated.
================================================================================
"""
from __future__ import annotations

import itertools
import logging
from typing import Optional

import broker
import config
import costs

log = logging.getLogger("execution")

#: Monotonic counter backing synthetic paper order ids.
_paper_seq = itertools.count(1)

#: Tag prefixed to every simulated order id so it is unmistakable in logs
#: and in the ``trade_history`` ledger.
PAPER_PREFIX = "PAPER"


def _paper_oid(kind: str) -> str:
    """Return a unique, obviously-fake order id such as ``PAPER-BUY-0007``."""
    return f"{PAPER_PREFIX}-{kind}-{next(_paper_seq):04d}"


def is_paper() -> bool:
    """True when this process must simulate rather than transmit orders."""
    return config.PAPER_TRADING


def entry_gate(db, *, engine_label: str, date_str: str) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for opening NEW positions.

    Shared by every engine so all three honour the same two safety latches:

        position_halt    — the database and the broker disagreed about what is
                           held. Existing positions still get managed and
                           exited; new entries are refused until an operator
                           reconciles and clears the flag.
        kill_switch_hit  — today's realised loss breached the engine's limit.
                           Refuses new entries for the rest of the day and
                           survives a restart because it lives in the database.

    Only options honoured these before; macro and smallcap would keep buying
    through both conditions. Exits are deliberately NOT gated — a halted or
    killed engine must still be able to close what it already holds.
    """
    halted, halt_reason = db.is_halted()
    if halted:
        return False, f"position_halt latched: {halt_reason!r}"

    _realised, ks_hit = db.get_daily_pnl(date_str)
    if ks_hit:
        return False, f"kill switch tripped for {date_str}"

    return True, "ok"


def mode_banner() -> str:
    """One-line description of the active execution mode, for boot logs."""
    if config.PAPER_TRADING:
        return (
            f"EXECUTION: PAPER (simulated) | env={config.APP_ENV} — "
            f"no orders will reach the broker"
        )
    return (
        f"EXECUTION: LIVE (real money) | env={config.APP_ENV} — "
        f"orders WILL be transmitted to Angel One"
    )


# ════════════════════════════════════════════════════════════════════════════
#  MARKET ORDERS
# ════════════════════════════════════════════════════════════════════════════
def place_market(
    api,
    *,
    symbol: str,
    token: str,
    exchange: str,
    side: str,
    quantity: int,
    product_type: str = "DELIVERY",
    variety: str = "NORMAL",
    wait_for_fill_s: float = 10.0,
) -> Optional[tuple[str, float]]:
    """Place a MARKET order.  Returns ``(order_id, avg_fill_price)`` or ``None``.

    Signature mirrors :func:`broker.place_market` exactly so engines can swap
    ``broker.`` for ``execution.`` with no other change.

    In paper mode the fill is modelled at the current LTP — the most honest
    simple assumption for a liquid market order.  If the quote is unavailable
    the simulated order is refused (returns ``None``) rather than inventing a
    price, so paper results never depend on fabricated data.
    """
    if quantity <= 0:
        log.error("place_market refused: quantity must be > 0 (got %s)", quantity)
        return None

    if not config.PAPER_TRADING:
        return broker.place_market(
            api,
            symbol=symbol,
            token=token,
            exchange=exchange,
            side=side,
            quantity=quantity,
            product_type=product_type,
            variety=variety,
            wait_for_fill_s=wait_for_fill_s,
        )

    ltp = broker.fetch_ltp(api, symbol=symbol, token=token, exchange=exchange)
    if not ltp:
        log.error(
            "[PAPER] %s %s refused — no LTP available, refusing to invent a "
            "fill price", side.upper(), symbol,
        )
        return None

    # A real MARKET order does not fill at the quoted LTP: it crosses the
    # spread. Model that adversely so paper results stay comparable to a live
    # account — a paper fill at the exact LTP silently flatters every trade.
    segment = costs.segment_for(exchange)
    fill_px = costs.apply_slippage(float(ltp), side=side, segment=segment)

    oid = _paper_oid(side.upper())
    log.info(
        "[PAPER] %s %d×%s @ ₹%.2f (ltp ₹%.2f + slippage) oid=%s",
        side.upper(), quantity, symbol, fill_px, ltp, oid,
    )
    return oid, fill_px


def place_stoploss_market(
    api,
    *,
    symbol: str,
    token: str,
    exchange: str,
    side: str,
    quantity: int,
    trigger_price: float,
    product_type: str = "DELIVERY",
    variety: str = "STOPLOSS",
) -> Optional[str]:
    """Place a broker-side STOPLOSS_MARKET order.  Returns the order id.

    In paper mode no resting order exists at the exchange; the engine's own
    monitor loop enforces the stop.  A synthetic id is returned so position
    metadata keeps the same shape in every environment.
    """
    if quantity <= 0:
        log.error("stoploss refused: quantity must be > 0 (got %s)", quantity)
        return None

    if not config.PAPER_TRADING:
        return broker.place_stoploss_market(
            api,
            symbol=symbol,
            token=token,
            exchange=exchange,
            side=side,
            quantity=quantity,
            trigger_price=trigger_price,
            product_type=product_type,
            variety=variety,
        )

    oid = _paper_oid("SL")
    log.info(
        "[PAPER] SL %d×%s trigger=₹%.2f (simulated) oid=%s",
        quantity, symbol, trigger_price, oid,
    )
    return oid


# ════════════════════════════════════════════════════════════════════════════
#  GTT RULES  (macro engine's persistent hard stop)
# ════════════════════════════════════════════════════════════════════════════
def create_gtt_sell_rule(
    api,
    *,
    tradingsymbol: str,
    symboltoken: str,
    exchange: str,
    qty: int,
    trigger_price: float,
    limit_price: float,
    product_type: str = "DELIVERY",
    timeperiod: int = 365,
) -> Optional[str]:
    """Create a single-leg GTT SELL rule.  Returns the rule id or ``None``."""
    if qty <= 0:
        log.error("GTT refused: qty must be > 0 (got %s)", qty)
        return None

    if not config.PAPER_TRADING:
        return broker.create_gtt_sell_rule(
            api,
            tradingsymbol=tradingsymbol,
            symboltoken=symboltoken,
            exchange=exchange,
            qty=qty,
            trigger_price=trigger_price,
            limit_price=limit_price,
            product_type=product_type,
            timeperiod=timeperiod,
        )

    rid = _paper_oid("GTT")
    log.info(
        "[PAPER] GTT SELL %d×%s trigger=₹%.2f limit=₹%.2f (simulated) rule=%s",
        qty, tradingsymbol, trigger_price, limit_price, rid,
    )
    return rid


def cancel_gtt_rule(
    api,
    *,
    rule_id: str,
    symboltoken: str,
    exchange: str,
) -> bool:
    """Cancel a resting GTT rule.  Returns True on success."""
    if not rule_id:
        log.warning("cancel_gtt_rule refused: empty rule_id")
        return False

    if not config.PAPER_TRADING:
        return broker.cancel_gtt_rule(
            api, rule_id=rule_id, symboltoken=symboltoken, exchange=exchange
        )

    log.info("[PAPER] GTT rule %s cancelled (simulated)", rule_id)
    return True


def cancel_order(api, order_id: str, variety: str = "NORMAL") -> bool:
    """Cancel a resting order.  Returns True on success."""
    if not order_id:
        return False

    if not config.PAPER_TRADING:
        return broker.cancel_order(api, order_id, variety=variety)

    log.info("[PAPER] order %s cancelled (simulated)", order_id)
    return True


# ════════════════════════════════════════════════════════════════════════════
#  READ-ONLY PASS-THROUGHS  (real market data in every mode)
# ════════════════════════════════════════════════════════════════════════════
def fetch_ltp(api, *, symbol: str, token: str, exchange: str) -> Optional[float]:
    """Return the last traded price.  Always a real quote, in every mode."""
    return broker.fetch_ltp(api, symbol=symbol, token=token, exchange=exchange)


def get_last_reject(symbol: str, side: str) -> Optional[str]:
    """Return the broker's last reject text.  Always empty in paper mode."""
    if config.PAPER_TRADING:
        return None
    return broker.get_last_reject(symbol, side)


# ════════════════════════════════════════════════════════════════════════════
#  BACK-COMPAT SHIM for option_predator's existing paper call sites
# ════════════════════════════════════════════════════════════════════════════
class PaperBroker:
    """Paper-fill adapter used by option_predator's ``paper.*`` call sites.

    Fills are modelled at the quoted price PLUS adverse slippage, matching
    :func:`place_market`. Without that, the options engine — the one that
    trades most often — would be the only component still getting
    unrealistically perfect fills, which is exactly where optimism does the
    most damage to a paper record.
    """

    #: Options engine trades NFO exclusively, so this is the slippage segment.
    SEGMENT = "OPTIONS"

    def __init__(self, api=None):
        self._api = api

    def bind(self, api) -> "PaperBroker":
        """Attach a broker session used only for read-only quote lookups."""
        self._api = api
        return self

    def next_oid(self, prefix: str) -> str:
        return _paper_oid(prefix.upper())

    def buy_market(self, symbol: str, ltp: float, qty: int) -> tuple[str, float]:
        oid = self.next_oid("BUY")
        fill = costs.apply_slippage(ltp, side="BUY", segment=self.SEGMENT)
        log.info("[PAPER] BUY %d×%s @ ₹%.2f (ltp ₹%.2f + slippage) oid=%s",
                 qty, symbol, fill, ltp, oid)
        return oid, fill

    def sell_market(self, symbol: str, ltp: float, qty: int) -> tuple[str, float]:
        oid = self.next_oid("SELL")
        fill = costs.apply_slippage(ltp, side="SELL", segment=self.SEGMENT)
        log.info("[PAPER] SELL %d×%s @ ₹%.2f (ltp ₹%.2f + slippage) oid=%s",
                 qty, symbol, fill, ltp, oid)
        return oid, fill

    def stoploss(self, symbol: str, trigger: float, qty: int) -> str:
        oid = self.next_oid("SL")
        log.info("[PAPER] SL %d×%s @ trig=%.2f (simulated) oid=%s",
                 qty, symbol, trigger, oid)
        return oid
