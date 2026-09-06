"""angel-one-quant-bundle  ▸  execution.py
==============================================================================
REST order layer.  Every order — market, limit, stop-loss, GTT — goes through
this module.  Strategies never call SmartAPI directly.

WHY A WRAPPER
─────────────
*  Angel's `placeOrder()` quietly returns `None` on rate-limit failures.
   `placeOrderFullResponse()` returns the real broker rejection text.  We
   prefer the latter and re-raise on soft failures so the retry layer can
   see them.
*  Tick-rounding is non-negotiable.  An order with a price that isn't a
   multiple of TICK_SIZE is rejected at the exchange edge, not at the
   broker — and the rejection message is famously unhelpful.
*  Paper mode short-circuits every order.  No "comment out the actual
   send" — flip `PAPER_TRADING=True` in `.env` and the bot literally
   cannot place an order.

PRODUCT TYPES (Angel schema)
────────────────────────────
    DELIVERY      CNC equity (overnight holds)
    INTRADAY      MIS equity (auto square-off intraday)
    CARRYFORWARD  NRML F&O / commodity (overnight positions)
    BO / CO       Bracket / Cover (not used here — too inflexible)

ORDER VARIETIES
───────────────
    NORMAL        Standard market / limit
    STOPLOSS      SL with trigger (use ordertype=STOPLOSS_MARKET or _LIMIT)

PUBLIC SURFACE
──────────────
    place_market(api, …)            → (order_id, avg_fill_price) | None
    place_limit(api, …)              → order_id | None
    place_stoploss_market(api, …)    → order_id | None
    cancel_order(api, order_id, …)   → bool
    modify_stoploss(api, …)          → bool
    wait_for_fill(api, order_id, …)  → (filled, avg_price, status)
    fetch_ltp(api, …)                → float | None
    fetch_candles(api, …)            → list[list]
==============================================================================
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from SmartApi import SmartConnect
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from . import config

log = logging.getLogger("execution")


class OrderError(RuntimeError):
    """Raised on a hard order failure — never on retryable transients."""


# ════════════════════════════════════════════════════════════════════════════
#  PACING & RETRY
# ════════════════════════════════════════════════════════════════════════════
# Fix B1 (26 May 2026): the module-global `_last_call_ts` is shared by every
# strategy that imports this module.  Without a lock, two coroutines firing
# orders within the same millisecond would both pass the `wait > 0` check
# and hit the broker at zero pacing — exactly the 429 we're trying to avoid.
# `_pace_lock` serialises read-modify-write of `_last_call_ts`.
_pace_lock = threading.Lock()
_last_call_ts: float = 0.0


def _pace() -> None:
    """Block until at least `API_PACING_SECONDS` has elapsed since the last
    broker call.  Thread-safe: the entire read-sleep-write sequence is
    serialised by `_pace_lock`.
    """
    global _last_call_ts
    with _pace_lock:
        now = time.monotonic()
        wait = config.API_PACING_SECONDS - (now - _last_call_ts)
        if wait > 0:
            time.sleep(wait)
        _last_call_ts = time.monotonic()


def _paper_ltp(api: SmartConnect, *, symbol: str, token: str, exchange: str) -> float:
    """Best-effort LTP fetch for paper-mode synthetic fills.

    Unlike `fetch_ltp()`, this helper does NOT retry and does NOT raise —
    a paper order must always succeed even if the LTP probe fails.  When
    the broker is unreachable we return 0.0 and the caller logs it; that's
    safer than crashing a paper session over a non-critical lookup.
    """
    try:
        resp = api.ltpData(exchange, symbol, str(token))
    except Exception as exc:                                                       # noqa: BLE001
        log.debug("paper LTP probe failed for %s: %s", symbol, exc)
        return 0.0
    if not isinstance(resp, dict) or not resp.get("status"):
        return 0.0
    return float((resp.get("data") or {}).get("ltp") or 0.0)


_retry = retry(
    stop=stop_after_attempt(config.API_MAX_RETRIES),
    wait=wait_exponential(
        multiplier=config.API_RETRY_BACKOFF, min=1.0, max=8.0
    ),
    retry=retry_if_exception_type((RuntimeError, ConnectionError, TimeoutError)),
    reraise=True,
)


# ════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════════════
def round_to_tick(price: float, tick: float | None = None) -> float:
    """Snap a price to the nearest valid exchange tick (default 0.05 NSE)."""
    t = tick if tick is not None else config.TICK_SIZE
    if t <= 0:
        return round(float(price), 2)
    return round(round(float(price) / t) * t, 2)


# ════════════════════════════════════════════════════════════════════════════
#  RAW WRAPPERS  (retry-decorated SmartAPI calls)
# ════════════════════════════════════════════════════════════════════════════
@_retry
def _place_order_raw(api: SmartConnect, params: dict) -> str:
    """Submit one order.  Returns the broker order id or raises."""
    _pace()
    full_fn = getattr(api, "placeOrderFullResponse", None)
    if callable(full_fn):
        full = full_fn(params)
        if not isinstance(full, dict):
            raise RuntimeError(f"placeOrderFullResponse returned {type(full).__name__}")
        if full.get("status") and (full.get("data") or {}).get("orderid"):
            return str((full["data"])["orderid"])
        raise RuntimeError(f"placeOrder rejected: {full.get('message') or full}")

    resp = api.placeOrder(params)
    if not resp:
        raise RuntimeError("placeOrder returned None (silent rate-limit?)")
    return str(resp)


@_retry
def _modify_order_raw(api: SmartConnect, params: dict) -> Any:
    _pace()
    return api.modifyOrder(params)


@_retry
def _cancel_order_raw(api: SmartConnect, order_id: str, variety: str) -> Any:
    _pace()
    return api.cancelOrder(order_id, variety)


@_retry
def _order_book_raw(api: SmartConnect) -> Any:
    _pace()
    return api.orderBook()


@_retry
def _ltp_raw(api: SmartConnect, exchange: str, symbol: str, token: str) -> Any:
    _pace()
    return api.ltpData(exchange, symbol, token)


@_retry
def _candle_raw(api: SmartConnect, params: dict) -> Any:
    _pace()
    return api.getCandleData(params)


# ════════════════════════════════════════════════════════════════════════════
#  FILL VERIFICATION
# ════════════════════════════════════════════════════════════════════════════
def wait_for_fill(
    api: SmartConnect,
    order_id: str,
    *,
    timeout_s: float = 10.0,
    poll_interval_s: float = 0.5,
) -> tuple[bool, float, str]:
    """Poll the order book until the order hits a terminal state.

    Returns
    ───────
    (filled, avg_fill_price, status_detail)

    `filled` is True only if status == 'complete'.  Cancelled/rejected return
    False with the broker's `text` field in `status_detail` for diagnostics.
    """
    deadline = time.monotonic() + timeout_s
    last = "unknown"
    while time.monotonic() < deadline:
        try:
            book = _order_book_raw(api)
        except Exception as exc:
            last = f"orderBook exception: {exc}"
            time.sleep(poll_interval_s)
            continue
        if not isinstance(book, dict) or not book.get("status"):
            last = f"orderBook bad: {book}"
            time.sleep(poll_interval_s)
            continue
        for o in book.get("data") or []:
            if str(o.get("orderid")) == str(order_id):
                status = (o.get("orderstatus") or "").lower()
                last = (
                    f"status={status} filled={o.get('filledshares')} "
                    f"text={o.get('text', '')!r}"
                )
                if status == "complete":
                    avg = float(o.get("averageprice") or 0.0)
                    return True, avg, last
                if status in ("rejected", "cancelled"):
                    return False, 0.0, last
        time.sleep(poll_interval_s)
    return False, 0.0, f"timeout after {timeout_s}s (last: {last})"


# ════════════════════════════════════════════════════════════════════════════
#  PUBLIC ORDER API
# ════════════════════════════════════════════════════════════════════════════
def place_market(
    api: SmartConnect,
    *,
    symbol: str,
    token: str,
    exchange: str,
    side: str,
    quantity: int,
    product_type: str = "INTRADAY",
    variety: str = "NORMAL",
    wait_for_fill_s: float = 10.0,
) -> Optional[tuple[str, float]]:
    """MARKET order.  Returns `(order_id, avg_fill_price)` or `None`."""
    if quantity <= 0:
        log.error("place_market refused: quantity must be > 0 (got %s)", quantity)
        return None

    params = {
        "variety":         variety,
        "tradingsymbol":   symbol,
        "symboltoken":     str(token),
        "transactiontype": side.upper(),
        "exchange":        exchange,
        "ordertype":       "MARKET",
        "producttype":     product_type,
        "duration":        "DAY",
        "price":           "0",
        "squareoff":       "0",
        "stoploss":        "0",
        "quantity":        str(int(quantity)),
    }

    if config.PAPER_TRADING:
        # Fix B6: return a SYNTHETIC fill price (real LTP at order time) so
        # downstream cost/PnL math doesn't break on a zero fill.
        paper_px = _paper_ltp(api, symbol=symbol, token=token, exchange=exchange)
        log.info(
            "[PAPER] MARKET %s %dx %s %s/%s @ ~₹%.2f — order suppressed",
            side, quantity, symbol, exchange, product_type, paper_px,
        )
        return f"PAPER-{int(time.time() * 1000)}", paper_px

    try:
        order_id = _place_order_raw(api, params)
    except Exception as exc:
        log.critical(
            "ORDER FAILED | %s %dx %s @ MKT %s | %s",
            side, quantity, symbol, product_type, exc,
        )
        return None

    log.info(
        "ORDER PLACED | %s %dx %s @ MKT %s/%s | oid=%s",
        side, quantity, symbol, exchange, product_type, order_id,
    )
    if wait_for_fill_s > 0:
        filled, avg, detail = wait_for_fill(api, order_id, timeout_s=wait_for_fill_s)
        if not filled:
            log.critical(
                "ORDER NOT FILLED | %s %dx %s oid=%s | %s",
                side, quantity, symbol, order_id, detail,
            )
            return None
        log.info(
            "ORDER FILLED  | %s %dx %s @ ₹%.2f | oid=%s",
            side, quantity, symbol, avg, order_id,
        )
        return order_id, avg
    return order_id, 0.0


def place_limit(
    api: SmartConnect,
    *,
    symbol: str,
    token: str,
    exchange: str,
    side: str,
    quantity: int,
    price: float,
    product_type: str = "INTRADAY",
    variety: str = "NORMAL",
) -> Optional[str]:
    """LIMIT order.  Returns order_id or None.  Tick-rounded automatically."""
    if quantity <= 0:
        log.error("place_limit refused: quantity must be > 0 (got %s)", quantity)
        return None
    price = round_to_tick(price)

    params = {
        "variety":         variety,
        "tradingsymbol":   symbol,
        "symboltoken":     str(token),
        "transactiontype": side.upper(),
        "exchange":        exchange,
        "ordertype":       "LIMIT",
        "producttype":     product_type,
        "duration":        "DAY",
        "price":           str(price),
        "squareoff":       "0",
        "stoploss":        "0",
        "quantity":        str(int(quantity)),
    }

    if config.PAPER_TRADING:
        # Fix B6: log realistic LTP context alongside the limit so paper-mode
        # diagnostics show "would it have filled?" at a glance.
        paper_ltp = _paper_ltp(api, symbol=symbol, token=token, exchange=exchange)
        log.info(
            "[PAPER] LIMIT %s %dx %s @ ₹%.2f (LTP ~₹%.2f) — order suppressed",
            side, quantity, symbol, price, paper_ltp,
        )
        return f"PAPER-{int(time.time() * 1000)}"

    try:
        order_id = _place_order_raw(api, params)
    except Exception as exc:
        log.critical("LIMIT FAILED | %s %dx %s @ ₹%.2f | %s",
                     side, quantity, symbol, price, exc)
        return None

    log.info(
        "LIMIT PLACED | %s %dx %s @ ₹%.2f %s/%s | oid=%s",
        side, quantity, symbol, price, exchange, product_type, order_id,
    )
    return order_id


def place_stoploss_market(
    api: SmartConnect,
    *,
    symbol: str,
    token: str,
    exchange: str,
    side: str,
    quantity: int,
    trigger_price: float,
    product_type: str = "INTRADAY",
    variety: str = "STOPLOSS",
) -> Optional[str]:
    """STOPLOSS_MARKET — broker-side hard stop.  Returns SL order_id or None."""
    if quantity <= 0:
        log.error("SL refused: quantity must be > 0 (got %s)", quantity)
        return None
    trigger_price = round_to_tick(trigger_price)

    params = {
        "variety":         variety,
        "tradingsymbol":   symbol,
        "symboltoken":     str(token),
        "transactiontype": side.upper(),
        "exchange":        exchange,
        "ordertype":       "STOPLOSS_MARKET",
        "producttype":     product_type,
        "duration":        "DAY",
        "price":           "0",
        "triggerprice":    str(trigger_price),
        "squareoff":       "0",
        "stoploss":        "0",
        "quantity":        str(int(quantity)),
    }

    if config.PAPER_TRADING:
        # Fix B6: log realistic LTP so paper-mode diagnostics show how far
        # the trigger is from the live price.
        paper_ltp = _paper_ltp(api, symbol=symbol, token=token, exchange=exchange)
        log.info(
            "[PAPER] SL %s %dx %s @ trig=₹%.2f (LTP ~₹%.2f) — order suppressed",
            side, quantity, symbol, trigger_price, paper_ltp,
        )
        return f"PAPER-SL-{int(time.time() * 1000)}"

    try:
        order_id = _place_order_raw(api, params)
    except Exception as exc:
        log.critical("SL FAILED | %s %dx %s @ trig=₹%.2f | %s",
                     side, quantity, symbol, trigger_price, exc)
        return None

    log.info(
        "SL PLACED | %s %dx %s @ trig=₹%.2f %s | oid=%s",
        side, quantity, symbol, trigger_price, exchange, order_id,
    )
    return order_id


def modify_stoploss(
    api: SmartConnect,
    *,
    order_id: str,
    symbol: str,
    token: str,
    exchange: str,
    new_trigger: float,
    quantity: int,
    product_type: str = "INTRADAY",
    variety: str = "STOPLOSS",
) -> bool:
    """Raise/lower an existing STOPLOSS_MARKET trigger.  Returns True on ack."""
    new_trigger = round_to_tick(new_trigger)
    params = {
        "variety":      variety,
        "orderid":      str(order_id),
        "tradingsymbol": symbol,
        "symboltoken":  str(token),
        "exchange":     exchange,
        "ordertype":    "STOPLOSS_MARKET",
        "producttype":  product_type,
        "duration":     "DAY",
        "price":        "0",
        "triggerprice": str(new_trigger),
        "quantity":     str(int(quantity)),
    }

    if config.PAPER_TRADING:
        log.info(
            "[PAPER] SL-MODIFY %s → trig=₹%.2f — order suppressed",
            order_id, new_trigger,
        )
        return True

    try:
        resp = _modify_order_raw(api, params)
    except Exception as exc:
        log.warning("SL modify failed oid=%s: %s", order_id, exc)
        return False

    ok = bool(isinstance(resp, dict) and resp.get("status"))
    log.info(
        "SL MODIFIED | oid=%s trig=₹%.2f → %s",
        order_id, new_trigger, "OK" if ok else f"FAIL ({resp})",
    )
    return ok


def cancel_order(
    api: SmartConnect, order_id: str, variety: str = "NORMAL"
) -> bool:
    """Cancel any pending order.  Returns True on ack."""
    if config.PAPER_TRADING:
        log.info("[PAPER] CANCEL %s — suppressed", order_id)
        return True
    try:
        resp = _cancel_order_raw(api, order_id, variety)
    except Exception as exc:
        log.warning("cancel_order failed for %s: %s", order_id, exc)
        return False
    ok = bool(resp and (resp.get("status") if isinstance(resp, dict) else True))
    log.info("CANCEL oid=%s → %s", order_id, "OK" if ok else f"FAIL ({resp})")
    return ok


# ════════════════════════════════════════════════════════════════════════════
#  MARKET DATA
# ════════════════════════════════════════════════════════════════════════════
def fetch_ltp(
    api: SmartConnect, *, symbol: str, token: str, exchange: str
) -> Optional[float]:
    """Last traded price for one instrument, or None if unavailable."""
    try:
        resp = _ltp_raw(api, exchange, symbol, str(token))
    except Exception as exc:
        log.warning("fetch_ltp exception for %s: %s", symbol, exc)
        return None
    if not isinstance(resp, dict) or not resp.get("status"):
        return None
    ltp = float((resp.get("data") or {}).get("ltp") or 0.0)
    return ltp or None


def fetch_candles(
    api: SmartConnect,
    *,
    exchange: str,
    token: str,
    interval: str = "ONE_DAY",
    lookback_days: int = 60,
) -> list[list]:
    """Historical OHLCV from Angel's getCandleData.

    Each row: ``[timestamp, open, high, low, close, volume]``.

    Valid intervals:
        ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, TEN_MINUTE, FIFTEEN_MINUTE,
        THIRTY_MINUTE, ONE_HOUR, ONE_DAY
    """
    end = datetime.now(config.IST)
    start = end - timedelta(days=lookback_days)
    fmt = "%Y-%m-%d %H:%M"
    params = {
        "exchange":    exchange,
        "symboltoken": str(token),
        "interval":    interval,
        "fromdate":    start.strftime(fmt),
        "todate":      end.strftime(fmt),
    }
    try:
        resp = _candle_raw(api, params)
    except Exception as exc:
        log.warning("fetch_candles exception: %s", exc)
        return []
    if not isinstance(resp, dict) or not resp.get("status"):
        log.warning("fetch_candles failed: %s", resp)
        return []
    return resp.get("data") or []
