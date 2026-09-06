"""algo-barbell  ▸  smallcap_engine.py
================================================================================
THE GROWTH ENGINE  —  40% of barbell capital  (₹10,000)

MANDATE
───────
Minervini Volatility Contraction Pattern (VCP) + volume breakout on the most
liquid Nifty Smallcap-250 names.  Wakes up daily at 15:15 IST and BUYS
DELIVERY (CNC) at MARKET when ALL of the following hold:

    Filter 0  Liquidity firewall:  20-day avg turnover ≥ ₹50 lakhs
    Filter 1  Trend filter:        Close > SMA(50) AND Close > SMA(200)
    Filter 2  VCP entry trigger:   Close > Upper Bollinger Band (20, 2)
    Filter 3  Volume confirmation: Volume > 2.5 × 20-day avg volume

Sizing: 1.5% of SMALLCAP_MAX_CAPITAL risked per trade, sized by
2 × ATR(14) stop-loss distance.  Hard SL placed broker-side as
STOPLOSS_MARKET.  Trail to SMA(20) once trade is in profit.

CRON
────
PM2 cron_restart: '45 9 * * 1-5'   (09:45 UTC = 15:15 IST, Mon–Fri)
                                    (5 minutes earlier than macro to allow
                                     ~100-stock candle scan to complete)

CLI
───
    python smallcap_engine.py                # live cron run
    python smallcap_engine.py --dry-run      # scan + log only, no orders
    python smallcap_engine.py --force        # bypass NSE trading-day guard
    python smallcap_engine.py --top N        # override universe size for this run

ISOLATION
─────────
* Own rows in the shared database, scoped by ``engine='SMALLCAP'``
* Own log file in ``logs/smallcap_<YYYY-MM-DD>.log``
* Capital strictly fenced by ``SMALLCAP_MAX_CAPITAL`` × cushion
* Never imports from macro_engine or option_predator
================================================================================
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
from datetime import datetime
from typing import Optional

import pandas as pd

import broker
import config
import costs
import execution
import instrument_master
import nse_calendar
import smallcap_universe
from auth import login, terminate
from database import BotDB, wait_for_database

log = logging.getLogger("smallcap")


# ════════════════════════════════════════════════════════════════════════════
#  TECHNICAL INDICATORS
# ════════════════════════════════════════════════════════════════════════════
def sma(series: pd.Series, period: int) -> float:
    if len(series) < period:
        return float("nan")
    return float(series.iloc[-period:].mean())


def bbands(close: pd.Series, period: int = 20, std: float = 2.0
           ) -> tuple[float, float, float]:
    """Return (lower, mid, upper) Bollinger Bands."""
    if len(close) < period:
        return float("nan"), float("nan"), float("nan")
    window = close.iloc[-period:]
    mid = float(window.mean())
    sd = float(window.std(ddof=0))
    return mid - std * sd, mid, mid + std * sd


def atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return float("nan")
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


def candles_to_df(rows: list[list]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
    df = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"]).reset_index(drop=True)


# ════════════════════════════════════════════════════════════════════════════
#  POSITION SIZING  (strict silo enforcement)
# ════════════════════════════════════════════════════════════════════════════
def size_position(
    *, entry_price: float, atr_value: float
) -> tuple[int, float, str]:
    """Return ``(quantity, stop_loss, reason_if_zero)`` for a smallcap entry."""
    if atr_value <= 0 or entry_price <= 0:
        return 0, 0.0, f"bad inputs atr={atr_value} entry={entry_price}"

    sl_distance = config.SMALLCAP_ATR_SL_MULT * atr_value
    risk_budget = config.risk_budget(
        config.SMALLCAP_MAX_CAPITAL, config.SMALLCAP_RISK_PCT
    )
    usable = config.usable_silo(config.SMALLCAP_MAX_CAPITAL)

    raw_qty = int(risk_budget / sl_distance)
    if raw_qty <= 0:
        return 0, 0.0, (
            f"risk_budget {risk_budget:.0f} < sl_distance {sl_distance:.2f}"
        )

    cost = raw_qty * entry_price
    if cost > usable:
        raw_qty = int(usable / entry_price)

    if raw_qty <= 0:
        return 0, 0.0, (
            f"silo too small for entry={entry_price:.2f}: usable={usable:.0f}"
        )

    stop_loss = round(entry_price - sl_distance, 2)
    return raw_qty, stop_loss, ""


# ════════════════════════════════════════════════════════════════════════════
#  PER-SYMBOL SCAN
# ════════════════════════════════════════════════════════════════════════════
def scan_symbol(api, symbol: str) -> Optional[dict]:
    """Scan ONE smallcap symbol; return setup dict if all filters pass.

    Returns
    -------
    None
        Symbol failed a filter (or unresolvable, or insufficient data).
    dict
        ``{symbol, token, exchange, last_close, atr, sma20, sma50, sma200,
           upper_bb, vol_today, avg_vol, turnover, qty, sl}``
    """
    try:
        token, exchange = instrument_master.resolve_equity(symbol)
    except LookupError:
        log.debug("[%s] not in scrip master — skip", symbol)
        return None

    rows = broker.fetch_candles(
        api, exchange=exchange, token=token,
        interval="ONE_DAY", lookback_days=300,
    )
    df = candles_to_df(rows)
    # Need enough history for SMA(200)
    if len(df) < 210:
        log.debug("[%s] insufficient candles (%d) — skip", symbol, len(df))
        return None

    last_close = float(df["close"].iloc[-1])
    last_volume = float(df["volume"].iloc[-1])

    # Filter 0: liquidity firewall  (20-day avg turnover)
    avg_vol_20 = float(df["volume"].iloc[-21:-1].mean())
    avg_close_20 = float(df["close"].iloc[-21:-1].mean())
    turnover = avg_vol_20 * avg_close_20
    if turnover < config.SMALLCAP_MIN_TURNOVER:
        log.debug("[%s] turnover ₹%.0f < min ₹%.0f — skip",
                  symbol, turnover, config.SMALLCAP_MIN_TURNOVER)
        return None

    # Filter 1: trend filter
    sma20 = sma(df["close"], 20)
    sma50 = sma(df["close"], 50)
    sma200 = sma(df["close"], 200)
    if not (last_close > sma50 and last_close > sma200):
        log.debug(
            "[%s] trend filter fail close=%.2f sma50=%.2f sma200=%.2f",
            symbol, last_close, sma50, sma200,
        )
        return None

    # Filter 2: BB upper-band breakout
    _, _, upper_bb = bbands(df["close"], config.SMALLCAP_BB_PERIOD,
                            config.SMALLCAP_BB_STD)
    if not (last_close > upper_bb):
        log.debug("[%s] no BB breakout close=%.2f upper=%.2f",
                  symbol, last_close, upper_bb)
        return None

    # Filter 3: volume confirmation
    if not (last_volume > config.SMALLCAP_VOL_MULT * avg_vol_20):
        log.debug(
            "[%s] volume %.0f < %.1f×avg_vol %.0f",
            symbol, last_volume, config.SMALLCAP_VOL_MULT, avg_vol_20,
        )
        return None

    # All filters passed — size it
    atr_val = atr(df, 14)
    qty, sl, reason = size_position(entry_price=last_close, atr_value=atr_val)
    if qty <= 0:
        log.warning("[%s] passed filters but sizing rejected: %s",
                    symbol, reason)
        return None

    setup = {
        "symbol": symbol,
        "token": token,
        "exchange": exchange,
        "last_close": last_close,
        "atr": atr_val,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "upper_bb": upper_bb,
        "vol_today": last_volume,
        "avg_vol": avg_vol_20,
        "turnover": turnover,
        "qty": qty,
        "sl": sl,
    }
    log.info(
        "[%s] ★ SETUP ★ close=%.2f upperBB=%.2f vol=%.0f(×%.1f) "
        "atr=%.2f qty=%d sl=%.2f cost=₹%.0f",
        symbol, last_close, upper_bb, last_volume,
        last_volume / max(avg_vol_20, 1), atr_val, qty, sl,
        qty * last_close,
    )
    return setup


# ════════════════════════════════════════════════════════════════════════════
#  ORDER EXECUTION  (entries)
# ════════════════════════════════════════════════════════════════════════════
def execute_entry(api, setup: dict, dry_run: bool, db: BotDB) -> bool:
    sym = setup["symbol"]
    if dry_run:
        log.info("[%s] DRY-RUN — entry suppressed", sym)
        return True

    # Refuse new entries while a halt is latched or the kill switch has tripped.
    allowed, gate_reason = execution.entry_gate(
        db, engine_label="SMALLCAP",
        date_str=datetime.now(config.IST).strftime("%Y-%m-%d"),
    )
    if not allowed:
        log.critical("[%s] ENTRY REFUSED — %s", sym, gate_reason)
        return False

    result = execution.place_market(
        api,
        symbol=sym,
        token=setup["token"],
        exchange=setup["exchange"],
        side="BUY",
        quantity=setup["qty"],
        product_type="DELIVERY",
        wait_for_fill_s=15.0,
    )
    if not result:
        log.critical("[%s] BUY FAILED — no SL placed", sym)
        return False
    order_id, fill_px = result
    fill_px = fill_px or setup["last_close"]

    # Recompute SL relative to actual fill (in case of slippage)
    sl = round(fill_px - config.SMALLCAP_ATR_SL_MULT * setup["atr"], 2)

    sl_oid = execution.place_stoploss_market(
        api,
        symbol=sym,
        token=setup["token"],
        exchange=setup["exchange"],
        side="SELL",
        quantity=setup["qty"],
        trigger_price=sl,
        product_type="DELIVERY",
    )

    db.add_position(
        symbol=sym,
        token=setup["token"],
        exchange=setup["exchange"],
        product_type="DELIVERY",
        quantity=setup["qty"],
        entry_price=fill_px,
        stop_loss=sl,
        order_id=order_id,
        meta={
            "atr": setup["atr"],
            "sma20": setup["sma20"],
            "upper_bb_at_entry": setup["upper_bb"],
            "sl_order_id": sl_oid,
        },
    )
    db.record_trade(
        side="BUY",
        symbol=sym,
        quantity=setup["qty"],
        price=fill_px,
        order_type="MARKET",
        order_id=order_id,
        meta={"vcp_breakout": True},
    )
    log.info(
        "[%s] ✅ POSITION OPENED qty=%d @ ₹%.2f sl=₹%.2f oid=%s sl_oid=%s",
        sym, setup["qty"], fill_px, sl, order_id, sl_oid,
    )
    return True


# ════════════════════════════════════════════════════════════════════════════
#  EXIT EVALUATION  (trail SL up to SMA20 once in profit)
# ════════════════════════════════════════════════════════════════════════════
def evaluate_exits(api, dry_run: bool, db: BotDB) -> int:
    open_pos = db.open_positions()
    if not open_pos:
        log.info("PHASE 1: No open positions.")
        return 0

    log.info("PHASE 1: Evaluating %d open position(s)…", len(open_pos))
    exits = 0
    for pos in open_pos:
        sym = pos["symbol"]

        # Refresh candles for SMA20 trail
        rows = broker.fetch_candles(
            api, exchange=pos["exchange"], token=pos["token"],
            interval="ONE_DAY", lookback_days=60,
        )
        df = candles_to_df(rows)
        if df.empty:
            log.warning("[%s] candle fetch failed — skip exit eval", sym)
            continue

        ltp = float(df["close"].iloc[-1])
        sma20 = sma(df["close"], 20)
        current_sl = pos.get("stop_loss") or 0.0
        entry = pos["entry_price"]

        log.info(
            "[%s] ltp=%.2f entry=%.2f sl=%.2f sma20=%.2f qty=%d",
            sym, ltp, entry, current_sl, sma20, pos["quantity"],
        )

        # ----- safety-net exit if LTP below SL -----
        if current_sl > 0 and ltp < current_sl:
            log.warning("[%s] LTP %.2f < SL %.2f — broker SL should have fired",
                        sym, ltp, current_sl)
            if dry_run:
                log.info("[%s] DRY-RUN — exit suppressed", sym)
                continue
            result = execution.place_market(
                api, symbol=sym, token=pos["token"], exchange=pos["exchange"],
                side="SELL", quantity=pos["quantity"],
                product_type="DELIVERY", wait_for_fill_s=15.0,
            )
            if result:
                oid, fpx = result
                # Net of real charges, booked atomically into daily_pnl so this
                # engine has a realised-P&L ledger and a working kill switch.
                rt = costs.round_trip(
                    segment=costs.segment_for(pos["exchange"]),
                    entry=float(entry),
                    exit_=fpx,
                    qty=int(pos["quantity"]),
                )
                pnl = rt.net_pnl
                db.close_position(
                    side="SELL", symbol=sym, quantity=pos["quantity"],
                    price=fpx, order_type="MARKET", order_id=oid, pnl=pnl,
                    meta={
                        "reason": "sl_safety_net",
                        "gross_pnl": rt.gross_pnl,
                        "charges": rt.total_charges,
                    },
                    book_pnl_date=datetime.now(config.IST).strftime("%Y-%m-%d"),
                )
                log.info(
                    "[%s] CLOSED qty=%d @ ₹%.2f gross=₹%+.2f charges=₹%.2f "
                    "net=₹%+.2f",
                    sym, pos["quantity"], fpx, rt.gross_pnl,
                    rt.total_charges, pnl,
                )
                exits += 1
            continue

        # ----- trail SL up to SMA20 once in profit -----
        if ltp > entry and sma20 > current_sl and sma20 < ltp:
            new_sl = round(sma20, 2)
            log.info(
                "[%s] trailing SL %.2f → %.2f (SMA20)",
                sym, current_sl, new_sl,
            )
            if not dry_run:
                # Cancel the old broker-side SL and place a fresh one.
                meta = pos.get("meta_json") or "{}"
                try:
                    import json
                    old_sl_oid = (json.loads(meta) or {}).get("sl_order_id")
                except Exception:
                    old_sl_oid = None
                if old_sl_oid:
                    execution.cancel_order(api, old_sl_oid, variety="STOPLOSS")
                new_sl_oid = execution.place_stoploss_market(
                    api, symbol=sym, token=pos["token"], exchange=pos["exchange"],
                    side="SELL", quantity=pos["quantity"],
                    trigger_price=new_sl, product_type="DELIVERY",
                )
                db.update_stop_loss(sym, new_sl)
                log.info("[%s] new SL order id=%s", sym, new_sl_oid)
    return exits


# ════════════════════════════════════════════════════════════════════════════
#  LOGGING SETUP
# ════════════════════════════════════════════════════════════════════════════
def setup_logging(level: str) -> None:
    today = datetime.now(config.IST).strftime("%Y-%m-%d")
    log_path = config.LOG_DIR / f"smallcap_{today}.log"
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10_000_000, backupCount=3, encoding="utf-8"
    )
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers = [fh, sh]
    root.setLevel(level)


# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--top", type=int, default=None,
                        help="Override SMALLCAP_UNIVERSE_SIZE for this run (100 or 250)")
    parser.add_argument("--max-entries", type=int, default=3,
                        help="Cap on new entries per run (default 3)")
    args = parser.parse_args()

    setup_logging(config.LOG_LEVEL)

    universe_size = args.top or config.SMALLCAP_UNIVERSE_SIZE
    universe = smallcap_universe.get_universe(universe_size)

    log.info(config.banner())
    log.info("=" * 72)
    log.info("smallcap_engine starting | mode=%s",
             "DRY-RUN" if args.dry_run else "LIVE")
    log.info("DB: %s (env=%s)", config._redacted_dsn(), config.APP_ENV)
    log.info("Universe: top-%d (%d symbols loaded)", universe_size, len(universe))
    log.info("Capital silo: ₹%.0f | risk %.1f%%/trade (₹%.0f) | max_entries=%d",
             config.SMALLCAP_MAX_CAPITAL,
             config.SMALLCAP_RISK_PCT * 100,
             config.risk_budget(config.SMALLCAP_MAX_CAPITAL,
                                config.SMALLCAP_RISK_PCT),
             args.max_entries)
    log.info("=" * 72)

    if not args.force and not nse_calendar.is_nse_trading_day():
        log.warning("Trading-day guard: SKIP (%s)",
                    nse_calendar.reason_market_closed())
        return 0

    log.info(execution.mode_banner())
    wait_for_database()
    db = BotDB("SMALLCAP")

    log.info("Authenticating to Angel One…")
    try:
        api = login()
    except Exception as exc:
        log.critical("Login failed: %s", exc)
        return 1
    log.info("Authenticated as %s", config.CLIENT_ID)

    try:
        evaluate_exits(api, args.dry_run, db)

        log.info("PHASE 2: Scanning %d symbols (%.1f s budget at %.1fs/req)…",
                 len(universe), len(universe) * config.API_PACING_SECONDS,
                 config.API_PACING_SECONDS)
        scan_start = time.time()
        setups: list[dict] = []
        rejected = 0
        for i, sym in enumerate(universe, start=1):
            try:
                setup = scan_symbol(api, sym)
                if setup:
                    setups.append(setup)
                else:
                    rejected += 1
            except Exception as exc:
                log.exception("[%s] scan crashed: %s", sym, exc)
                rejected += 1
            time.sleep(config.API_PACING_SECONDS)
            if i % 25 == 0:
                log.info("  …scanned %d/%d (%d setups, %d rejected)",
                         i, len(universe), len(setups), rejected)
        scan_secs = time.time() - scan_start
        log.info("PHASE 2 complete in %.1fs | setups=%d rejected=%d",
                 scan_secs, len(setups), rejected)

        if not setups:
            log.info("No setups today.")
            return 0

        # Rank setups by volume-multiple (highest first) then take top N
        setups.sort(key=lambda s: s["vol_today"] / max(s["avg_vol"], 1),
                    reverse=True)
        chosen = setups[:args.max_entries]
        log.info("PHASE 3: %d setup(s) selected for execution:", len(chosen))
        for s in chosen:
            log.info("  → %s (vol×%.1f)", s["symbol"],
                     s["vol_today"] / max(s["avg_vol"], 1))

        executed = 0
        for setup in chosen:
            try:
                if execute_entry(api, setup, args.dry_run, db):
                    executed += 1
            except Exception as exc:
                log.exception("[%s] entry crashed: %s", setup["symbol"], exc)

        log.info("=" * 72)
        log.info("Smallcap pass complete | scanned=%d setups=%d executed=%d",
                 len(universe), len(setups), executed)
        log.info("=" * 72)
    finally:
        terminate(api)

    return 0


if __name__ == "__main__":
    sys.exit(main())
