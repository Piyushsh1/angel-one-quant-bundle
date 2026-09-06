"""algo-barbell  ▸  broker.py
================================================================================
Unified order-placement layer — supports NSE Cash, NSE F&O, and MCX.

Three product types in this codebase:

    DELIVERY      → CNC equity (macro_engine, smallcap_engine)
    INTRADAY      → MIS equity (not used currently — reserved)
    CARRYFORWARD  → NRML F&O / commodity (option_predator, MCX commodity)

LESSONS BAKED IN
────────────────
* SmartAPI ``placeOrder()`` returns bare ``None`` on rate-limit silent
  failures — we use ``placeOrderFullResponse()`` when available so we get
  the actual broker rejection text in every failure.
* Every price is rounded to ``TICK_SIZE`` to avoid the
  ``"trigger price is not a multiple of tick size"`` rejection.
* All API calls are wrapped in :func:`retry_api` so transient errors
  retry transparently with exponential back-off.

PUBLIC API
──────────
    place_market(api, symbol, token, exchange, side, quantity, product_type)
        Returns ``(order_id, avg_fill_price)`` on accepted+filled,
        else ``None``.

    place_stoploss_market(api, symbol, token, exchange, side, quantity,
                          trigger_price, product_type)
        Returns ``order_id`` on accepted, else ``None``.

    create_gtt_sell_rule(api, tradingsymbol, symboltoken, exchange, qty,
                         trigger_price, limit_price, product_type, timeperiod)
        Persistent GTT SELL rule (DELIVERY SL that survives EOD).
        Returns ``rule_id`` on accepted, else ``None``.

    cancel_gtt_rule(api, rule_id, symboltoken, exchange)
        Cancel a resting GTT rule.  Returns True / False.

    cancel_order(api, order_id, variety='NORMAL')
        Returns True / False.

    modify_stop_loss(api, order_id, new_trigger, …)
        Returns True / False.

    fetch_candles(api, exchange, token, interval, lookback_days)
        Returns list[dict] from getCandleData.

    wait_for_fill(api, order_id, timeout_s=10)
        Returns ``(filled, avg_price, status_text)``.
================================================================================
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from SmartApi import SmartConnect

import config
from retry import retry_api, is_margin_reject

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════════════
def round_to_tick(price: float, tick: float = None) -> float:
    """Round price to the nearest valid exchange tick (default 0.05 NSE)."""
    t = tick if tick is not None else config.TICK_SIZE
    if t <= 0:
        return round(float(price), 2)
    return round(round(float(price) / t) * t, 2)


# ════════════════════════════════════════════════════════════════════════════
#  RETRY-WRAPPED SmartAPI CALLS
# ════════════════════════════════════════════════════════════════════════════
@retry_api()
def _place_order_raw(api: SmartConnect, params: dict) -> Any:
    """Internal: call placeOrderFullResponse if available, else placeOrder.

    On bare-None response (silent rate-limit), the retry decorator's
    ``raise_on_none`` path engages and retries up to 3 times.
    """
    full_fn = getattr(api, "placeOrderFullResponse", None)
    if callable(full_fn):
        full = full_fn(params)
        if isinstance(full, dict):
            if full.get("status") and (full.get("data") or {}).get("orderid"):
                return (full.get("data") or {}).get("orderid")
            # Soft-failure: re-raise so retry decorator can decide retry
            raise RuntimeError(
                f"placeOrder rejected: {full.get('message') or full}"
            )
        return full

    resp = api.placeOrder(params)
    if resp is None:
        raise RuntimeError(
            "placeOrder returned None (likely 'exceeding access rate' / non-JSON)"
        )
    return resp


@retry_api()
def _modify_order_raw(api: SmartConnect, params: dict) -> Any:
    return api.modifyOrder(params)


@retry_api()
def _cancel_order_raw(api: SmartConnect, order_id: str, variety: str) -> Any:
    return api.cancelOrder(order_id, variety)


@retry_api()
def _order_book_raw(api: SmartConnect) -> Any:
    return api.orderBook()


@retry_api()
def _candle_raw(api: SmartConnect, params: dict) -> Any:
    return api.getCandleData(params)


@retry_api()
def _ltp_raw(api: SmartConnect, exchange: str, symbol: str, token: str) -> Any:
    return api.ltpData(exchange, symbol, token)


@retry_api()
def _gtt_create_raw(api: SmartConnect, params: dict) -> Any:
    """Internal: hit Angel's GTT createRule endpoint via SmartAPI SDK.

    The SDK exposes the method as ``gttCreateRule`` on recent versions and
    ``createRule`` on older ones — we try both for forward-compatibility.
    """
    fn = getattr(api, "gttCreateRule", None) or getattr(api, "createRule", None)
    if not callable(fn):
        raise RuntimeError(
            "SmartAPI build lacks gttCreateRule/createRule — upgrade smartapi-python"
        )
    resp = fn(params)
    if resp is None:
        raise RuntimeError("gttCreateRule returned None (likely rate-limit / non-JSON)")
    return resp


@retry_api()
def _gtt_cancel_raw(api: SmartConnect, params: dict) -> Any:
    fn = getattr(api, "gttCancelRule", None) or getattr(api, "cancelRule", None)
    if not callable(fn):
        raise RuntimeError(
            "SmartAPI build lacks gttCancelRule/cancelRule — upgrade smartapi-python"
        )
    resp = fn(params)
    if resp is None:
        raise RuntimeError("gttCancelRule returned None (likely rate-limit / non-JSON)")
    return resp


# ════════════════════════════════════════════════════════════════════════════
#  REJECTION TRACKING  (so callers can react to insufficient-funds, etc.)
# ════════════════════════════════════════════════════════════════════════════
# Module-level cache of the last broker-side rejection per (symbol, side).
# Populated by `place_market` when wait_for_fill reports a non-complete
# terminal state.  Callers (e.g. option_predator's partial-sell loop) read
# this immediately after a None return to decide whether the failure is
# transient (retry) or structural (cool down).
_LAST_REJECT: dict[tuple[str, str], str] = {}


def get_last_reject(symbol: str, side: str) -> Optional[str]:
    """Return the broker's last reject text for ``(symbol, side.upper())``.

    Returns ``None`` if no rejection has been recorded.  The string is
    whatever Angel One placed in the order-book ``text`` field (e.g.
    ``"Your order has been rejected due to Insufficient Funds. ..."``).
    """
    return _LAST_REJECT.get((symbol, side.upper()))


def clear_last_reject(symbol: str, side: str) -> None:
    """Forget any previously recorded reject for this (symbol, side)."""
    _LAST_REJECT.pop((symbol, side.upper()), None)


# ════════════════════════════════════════════════════════════════════════════
#  ORDER VERIFICATION  (poll order book until status is terminal)
# ════════════════════════════════════════════════════════════════════════════
def wait_for_fill(
    api: SmartConnect,
    order_id: str,
    timeout_s: float = 10.0,
    poll_interval_s: float = 0.5,
) -> tuple[bool, float, str]:
    """Poll the order book until the order reaches a terminal state.

    Returns
    -------
    tuple[bool, float, str]
        ``(filled, avg_fill_price, detail)``.
        ``filled`` is True only if order status == 'complete'.
        ``avg_fill_price`` is the broker's reported averageprice (0 if not filled).
        ``detail`` is a human-readable status string for logging.
    """
    deadline = time.monotonic() + timeout_s
    last_status = "unknown"
    while time.monotonic() < deadline:
        try:
            book = _order_book_raw(api)
        except Exception as exc:
            last_status = f"orderBook exception {exc}"
            time.sleep(poll_interval_s)
            continue
        if not book or not book.get("status"):
            last_status = f"orderBook bad: {book}"
            time.sleep(poll_interval_s)
            continue
        for o in book.get("data", []) or []:
            if str(o.get("orderid")) == str(order_id):
                status = (o.get("orderstatus") or "").lower()
                last_status = (
                    f"status={status} filled={o.get('filledshares')} "
                    f"text={o.get('text', '')!r}"
                )
                if status == "complete":
                    avg = float(o.get("averageprice") or 0.0)
                    return True, avg, last_status
                if status in ("rejected", "cancelled"):
                    return False, 0.0, last_status
        time.sleep(poll_interval_s)
    return False, 0.0, f"timeout after {timeout_s}s (last: {last_status})"


# ════════════════════════════════════════════════════════════════════════════
#  PUBLIC ORDER FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════
def place_market(
    api: SmartConnect,
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
    """Place a MARKET order.  Returns ``(order_id, avg_fill_price)`` or ``None``."""
    if quantity <= 0:
        log.error("place_market refused: quantity must be > 0 (got %s)", quantity)
        return None

    params = {
        "variety":         variety,
        "tradingsymbol":   symbol,
        "symboltoken":     token,
        "transactiontype": side.upper(),
        "exchange":        exchange,
        "ordertype":       "MARKET",
        "producttype":     product_type,
        "duration":        "DAY",
        "price":           "0",
        "squareoff":       "0",
        "stoploss":        "0",
        "quantity":        str(quantity),
    }

    try:
        order_id = _place_order_raw(api, params)
    except Exception as exc:
        log.critical(
            "ORDER FAILED | %s %dx %s @ MKT %s | %s",
            side, quantity, symbol, product_type, exc,
        )
        return None

    if not order_id:
        log.critical("ORDER FAILED | %s %dx %s @ MKT — empty response", side, quantity, symbol)
        return None

    log.info(
        "ORDER PLACED | %s %dx %s @ MKT %s/%s | oid=%s",
        side, quantity, symbol, exchange, product_type, order_id,
    )
    if wait_for_fill_s > 0:
        filled, avg, detail = wait_for_fill(api, order_id, timeout_s=wait_for_fill_s)
        if not filled:
            _LAST_REJECT[(symbol, side.upper())] = detail
            # ── MARGIN GUARDRAIL ─────────────────────────────────────────
            # Angel One's pre-trade risk engine evaluates every SELL leg
            # as a standalone short and demands full SPAN+ELM cash even
            # when an offsetting long exists in the same product.  If the
            # account is under-funded this manifests as "Insufficient
            # Funds" / "You require Rs. ..." in the order-book text.
            # That failure mode is STRUCTURAL — it will never self-heal
            # mid-session — so we must NOT continue tripping the same
            # rejection repeatedly (rate-limit budget, log noise, alerts).
            # Emit the named guardrail line so the operator can grep for
            # it and treat it as an actionable funding incident, then
            # bail out fast.  The per-symbol cooldown in option_predator
            # picks up via get_last_reject() and suppresses subsequent
            # SELL attempts for OPTIONS_PARTIAL_SELL_FUND_COOLDOWN_MIN min.
            if side.upper() == "SELL" and is_margin_reject(detail):
                log.critical(
                    "[MARGIN GUARDRAIL] Broker API evaluating SELL as "
                    "naked short. Insufficient funds. Aborting retry "
                    "storm to protect rate limits."
                )
                log.critical(
                    "[MARGIN GUARDRAIL] context: symbol=%s qty=%d oid=%s "
                    "reject_text=%r",
                    symbol, quantity, order_id, detail,
                )
            else:
                log.critical(
                    "ORDER NOT FILLED | %s %dx %s oid=%s | %s",
                    side, quantity, symbol, order_id, detail,
                )
            return None
        # Order filled cleanly — wipe any stale reject record for this (symbol, side).
        _LAST_REJECT.pop((symbol, side.upper()), None)
        log.info(
            "ORDER FILLED  | %s %dx %s @ ₹%.2f | oid=%s",
            side, quantity, symbol, avg, order_id,
        )
        return str(order_id), avg
    return str(order_id), 0.0


def place_stoploss_market(
    api: SmartConnect,
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
    """Place a STOPLOSS_MARKET order to act as the broker-side hard stop."""
    if quantity <= 0:
        log.error("SL refused: quantity must be > 0 (got %s)", quantity)
        return None

    trigger_price = round_to_tick(trigger_price)
    params = {
        "variety":         variety,
        "tradingsymbol":   symbol,
        "symboltoken":     token,
        "transactiontype": side.upper(),
        "exchange":        exchange,
        "ordertype":       "STOPLOSS_MARKET",
        "producttype":     product_type,
        "duration":        "DAY",
        "price":           "0",
        "triggerprice":    str(trigger_price),
        "squareoff":       "0",
        "stoploss":        "0",
        "quantity":        str(quantity),
    }

    try:
        order_id = _place_order_raw(api, params)
    except Exception as exc:
        log.critical("SL FAILED | %s %dx %s @ trig=%.2f | %s",
                     side, quantity, symbol, trigger_price, exc)
        return None

    if not order_id:
        log.critical("SL FAILED | %s %dx %s — empty response", side, quantity, symbol)
        return None

    log.info(
        "SL PLACED | %s %dx %s @ trig=%.2f %s | oid=%s",
        side, quantity, symbol, trigger_price, exchange, order_id,
    )
    return str(order_id)


# ════════════════════════════════════════════════════════════════════════════
#  GTT (Good-Till-Triggered) RULES — DELIVERY SL persistence
# ════════════════════════════════════════════════════════════════════════════
#  Angel auto-cancels every STOPLOSS_MARKET order at ~15:30 IST EOD, leaving
#  DELIVERY positions (macro_engine ETFs) unhedged overnight against gap-down.
#  GTT rules sit at the exchange for up to `timeperiod` days and survive
#  session boundaries.  Macro bot uses these as its persistent hard stop.
# ════════════════════════════════════════════════════════════════════════════
def create_gtt_sell_rule(
    api: SmartConnect,
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
    """Create a single-leg GTT SELL rule.  Returns the rule ID or None.

    Pricing
    -------
    * ``trigger_price`` — exchange-side trigger (e.g. ATR-based SL).
    * ``limit_price``   — price at which the SELL is placed once triggered.
      For a gap-down sweep guarantee, the caller should pass a value ~0.5%
      below the trigger so the order behaves like a market-on-trigger fill.

    Both prices are tick-rounded before submission.
    """
    if qty <= 0:
        log.error("GTT refused: qty must be > 0 (got %s)", qty)
        return None

    trigger_price = round_to_tick(trigger_price)
    limit_price   = round_to_tick(limit_price)
    if limit_price >= trigger_price:
        log.warning(
            "GTT-CREATE | limit_price %.2f >= trigger %.2f for SELL — "
            "rule may sit un-fillable on gap-down (caller bug?)",
            limit_price, trigger_price,
        )

    params = {
        "strategyCode":    "SINGLE",
        "tradingsymbol":   tradingsymbol,
        "symboltoken":     str(symboltoken),
        "exchange":        exchange,
        "transactiontype": "SELL",
        "producttype":     product_type,
        "price":           str(limit_price),
        "qty":             str(int(qty)),
        "triggerprice":    str(trigger_price),
        "disclosedqty":    "0",
        "timeperiod":      str(int(timeperiod)),
    }

    try:
        resp = _gtt_create_raw(api, params)
    except Exception as exc:
        log.critical(
            "GTT-CREATE FAILED | SELL %dx %s @ trig=%.2f lim=%.2f | %s",
            qty, tradingsymbol, trigger_price, limit_price, exc,
        )
        return None

    if not isinstance(resp, dict) or not resp.get("status"):
        log.critical(
            "GTT-CREATE rejected | SELL %dx %s @ trig=%.2f | resp=%s",
            qty, tradingsymbol, trigger_price, resp,
        )
        return None

    data = resp.get("data") or {}
    rule_id = str(data.get("id") or data.get("rule_id") or "").strip()
    if not rule_id:
        log.critical("GTT-CREATE returned no rule id | resp=%s", resp)
        return None

    log.info(
        "GTT PLACED | SELL %dx %s @ trig=%.2f lim=%.2f %s/%s | rule_id=%s | tp=%dd",
        qty, tradingsymbol, trigger_price, limit_price,
        exchange, product_type, rule_id, timeperiod,
    )
    return rule_id


def cancel_gtt_rule(
    api: SmartConnect,
    *,
    rule_id: str,
    symboltoken: str,
    exchange: str,
) -> bool:
    """Cancel a resting GTT rule.  Returns True on Angel ACK, else False.

    MUST be called before any force-close MARKET sell in macro_engine's
    safety-net path — otherwise the GTT would stay armed and could trigger
    a phantom short-sell (insufficient holdings → exchange rejection / risk).
    """
    if not rule_id:
        log.warning("cancel_gtt_rule refused: empty rule_id")
        return False

    params = {
        "id":          str(rule_id),
        "symboltoken": str(symboltoken),
        "exchange":    exchange,
    }
    try:
        resp = _gtt_cancel_raw(api, params)
    except Exception as exc:
        log.warning("cancel_gtt_rule failed for rule_id=%s: %s", rule_id, exc)
        return False

    ok = bool(isinstance(resp, dict) and resp.get("status"))
    log.info(
        "GTT CANCEL | rule_id=%s → %s",
        rule_id, "OK" if ok else f"FAIL ({resp})",
    )
    return ok


def cancel_order(api: SmartConnect, order_id: str, variety: str = "NORMAL") -> bool:
    try:
        resp = _cancel_order_raw(api, order_id, variety)
    except Exception as exc:
        log.warning("cancel_order failed for %s: %s", order_id, exc)
        return False
    ok = bool(resp and (resp.get("status") if isinstance(resp, dict) else True))
    log.info("cancel_order %s → %s", order_id, "OK" if ok else f"FAIL ({resp})")
    return ok


# ════════════════════════════════════════════════════════════════════════════
#  HISTORICAL CANDLES
# ════════════════════════════════════════════════════════════════════════════
def fetch_candles(
    api: SmartConnect,
    *,
    exchange: str,
    token: str,
    interval: str = "ONE_DAY",
    lookback_days: int = 60,
) -> list[list]:
    """Fetch OHLCV candles.  Returns the raw list-of-lists from Angel.

    Each candle is ``[timestamp, open, high, low, close, volume]``.
    """
    end = datetime.now(config.IST)
    start = end - timedelta(days=lookback_days)
    fmt = "%Y-%m-%d %H:%M"
    params = {
        "exchange":    exchange,
        "symboltoken": token,
        "interval":    interval,
        "fromdate":    start.strftime(fmt),
        "todate":      end.strftime(fmt),
    }
    resp = _candle_raw(api, params)
    if not resp or not resp.get("status"):
        log.warning("fetch_candles failed: %s", resp)
        return []
    return resp.get("data", []) or []


def fetch_ltp(
    api: SmartConnect, *, symbol: str, token: str, exchange: str
) -> Optional[float]:
    """Return last traded price or None on failure."""
    try:
        resp = _ltp_raw(api, exchange, symbol, token)
    except Exception as exc:
        log.warning("fetch_ltp exception: %s", exc)
        return None
    if not resp or not resp.get("status"):
        return None
    return float((resp.get("data") or {}).get("ltp") or 0.0) or None
