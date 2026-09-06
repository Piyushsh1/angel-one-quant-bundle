"""algo-barbell  ▸  macro_engine.py
================================================================================
THE SAFE ANCHOR  —  40% of barbell capital  (₹10,000)

MANDATE
───────
Macro-trend follower on broad-market ETFs.  Wakes up once a day at 15:20 IST,
scans NIFTYBEES-EQ + BANKBEES-EQ, and BUYS DELIVERY (CNC) when:

    Close > highest-high(10 days) AND RSI(14) > 55

Hard SL = entry - (2 × ATR(14)).  No trailing — held until SL hit.

UNIVERSE
────────
    NIFTYBEES-EQ    NSE Cash, Nifty 50 ETF        product=DELIVERY
    BANKBEES-EQ     NSE Cash, Bank Nifty ETF      product=DELIVERY

(SILVERMIC was evaluated and removed — see "Tuning" in README.md.  Silver
mini's ATR consistently exceeds the ₹300 risk budget per trade, so the
sizing formula returns qty=0 every day.  Re-add only after carving a
separate commodity silo with looser sizing rules.)

CRON
────
PM2 cron_restart: '50 9 * * 1-5'   (09:50 UTC = 15:20 IST, Mon–Fri)

CLI
───
    python macro_engine.py                # live cron run
    python macro_engine.py --dry-run      # scan + log only, no orders
    python macro_engine.py --force        # bypass NSE trading-day guard
================================================================================
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
from datetime import datetime
from typing import Optional

import pandas as pd

import broker
import config
import costs
import execution
import instrument_master
import nse_calendar
from auth import login, terminate
from database import BotDB, wait_for_database

log = logging.getLogger("macro")

# ── GTT sweep buffer (25 May 2026 V2 migration) ─────────────────────────────
# GTT SELL rules need both a trigger AND a limit price.  We set the limit
# 0.5% below the trigger so a gap-down opens BELOW the limit and the order
# still fills — effectively behaving like a market-on-trigger.  If we passed
# `limit == trigger`, a hard gap-down would leave the rule resting un-fillable.
GTT_LIMIT_BUFFER_PCT = 0.005


# ════════════════════════════════════════════════════════════════════════════
#  UNIVERSE CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════
# kind ∈ {'equity', 'commodity'}  →  drives instrument_master lookup
# product_type follows Angel One's order schema
MACRO_UNIVERSE: list[dict] = [
    {"name": "NIFTYBEES-EQ", "kind": "equity", "product_type": "DELIVERY"},
    {"name": "BANKBEES-EQ",  "kind": "equity", "product_type": "DELIVERY"},
]


# ════════════════════════════════════════════════════════════════════════════
#  TECHNICAL INDICATORS  (computed inline — no pandas-ta dependency)
# ════════════════════════════════════════════════════════════════════════════
def donchian_high(df: pd.DataFrame, lookback: int) -> float:
    """Highest high of the LAST `lookback` PRIOR days (excludes today)."""
    if len(df) < lookback + 1:
        return float("nan")
    return float(df["high"].iloc[-(lookback + 1):-1].max())


def rsi(df: pd.DataFrame, period: int = 14) -> float:
    """Wilder's RSI on the close series.  Returns the latest value."""
    if len(df) < period + 1:
        return float("nan")
    close = df["close"].astype(float)
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    # Wilder's smoothing = EMA with alpha = 1/period
    avg_gain = up.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    rsi_series = 100 - (100 / (1 + rs))
    return float(rsi_series.iloc[-1])


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """Wilder's ATR on OHLC.  Returns the latest value."""
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
    atr_series = tr.ewm(alpha=1 / period, adjust=False).mean()
    return float(atr_series.iloc[-1])


# ════════════════════════════════════════════════════════════════════════════
#  CANDLE → DataFrame
# ════════════════════════════════════════════════════════════════════════════
def candles_to_df(rows: list[list]) -> pd.DataFrame:
    """Convert Angel One's list-of-lists candle response to a tidy DataFrame."""
    if not rows:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
    df = pd.DataFrame(
        rows,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"]).reset_index(drop=True)


# ════════════════════════════════════════════════════════════════════════════
#  POSITION SIZING  (strict silo enforcement)
# ════════════════════════════════════════════════════════════════════════════
def size_position(
    *, entry_price: float, atr_value: float, lot_size: int = 1
) -> tuple[int, float, str]:
    """Return ``(quantity, stop_loss, reason_if_zero)`` for a macro entry.

    Sizing rule
    ───────────
        risk_per_trade  =  MACRO_MAX_CAPITAL × MACRO_RISK_PCT
        sl_distance     =  MACRO_ATR_SL_MULT × ATR(14)
        raw_qty         =  risk_per_trade / sl_distance
        cost            =  raw_qty × entry_price
        if cost > usable_silo:  scale qty down to fit usable silo
        round to lot_size multiple

    Capital is FENCED — broker margin is irrelevant.
    """
    if atr_value <= 0 or entry_price <= 0:
        return 0, 0.0, f"bad inputs: atr={atr_value} entry={entry_price}"

    sl_distance = config.MACRO_ATR_SL_MULT * atr_value
    risk_budget = config.risk_budget(config.MACRO_MAX_CAPITAL, config.MACRO_RISK_PCT)
    usable = config.usable_silo(config.MACRO_MAX_CAPITAL)

    raw_qty = int(risk_budget / sl_distance)
    if raw_qty <= 0:
        return 0, 0.0, f"risk_budget {risk_budget:.0f} < sl_distance {sl_distance:.2f}"

    cost = raw_qty * entry_price
    if cost > usable:
        raw_qty = int(usable / entry_price)

    # Snap to lot size (1 for equity, contract lot for commodity)
    if lot_size > 1:
        raw_qty = (raw_qty // lot_size) * lot_size

    if raw_qty <= 0:
        return 0, 0.0, (
            f"silo too small for entry={entry_price:.2f}: "
            f"usable={usable:.0f} lot={lot_size}"
        )

    stop_loss = round(entry_price - sl_distance, 2)
    return raw_qty, stop_loss, ""


# ════════════════════════════════════════════════════════════════════════════
#  PER-SYMBOL EVALUATION
# ════════════════════════════════════════════════════════════════════════════
def resolve_symbol(api, asset: dict) -> Optional[dict]:
    """Look up token + exchange + lot_size for a universe entry."""
    name = asset["name"]
    kind = asset["kind"]
    try:
        if kind == "equity":
            token, exchange = instrument_master.resolve_equity(name)
            return {
                "trading_symbol": name,
                "token": token,
                "exchange": exchange,
                "lot_size": 1,
            }
        if kind == "commodity":
            sym, token, exchange = instrument_master.resolve_commodity(name)
            # Fetch lot size from cache
            lot_size = 1
            for inst in instrument_master._load_master():  # noqa: SLF001
                if inst.get("symbol") == sym:
                    lot_size = int(inst.get("lotsize") or 1)
                    break
            return {
                "trading_symbol": sym,
                "token": token,
                "exchange": exchange,
                "lot_size": lot_size,
            }
    except Exception as exc:
        log.error("[%s] Symbol resolution failed: %s", name, exc)
        return None
    return None


def evaluate_entry(api, asset: dict, dry_run: bool, db: BotDB) -> bool:
    """Scan ONE asset for a Donchian breakout signal.  Returns True if a
    new position was opened (or would have been, in dry-run mode).
    """
    name = asset["name"]
    inst = resolve_symbol(api, asset)
    if inst is None:
        return False

    # Already long?  Skip.
    if any(p["symbol"] == inst["trading_symbol"] for p in db.open_positions()):
        log.info("[%s] Already in DB — skip entry scan", name)
        return False

    # Fetch 60 daily candles
    rows = broker.fetch_candles(
        api, exchange=inst["exchange"], token=inst["token"],
        interval="ONE_DAY", lookback_days=90,
    )
    df = candles_to_df(rows)
    if len(df) < max(config.MACRO_DONCHIAN_LOOKBACK + 1, config.MACRO_RSI_PERIOD + 1):
        log.warning("[%s] Insufficient candles (%d) — skip", name, len(df))
        return False

    last_close = float(df["close"].iloc[-1])
    dc_high = donchian_high(df, config.MACRO_DONCHIAN_LOOKBACK)
    rsi_val = rsi(df, config.MACRO_RSI_PERIOD)
    atr_val = atr(df, 14)

    log.info(
        "[%s] close=%.2f | donchian-%d-high=%.2f | rsi=%.1f | atr=%.3f",
        name, last_close, config.MACRO_DONCHIAN_LOOKBACK, dc_high, rsi_val, atr_val,
    )

    # Entry condition
    if not (last_close > dc_high):
        log.info("[%s] No breakout (close ≤ donchian high) — skip", name)
        return False
    if not (rsi_val > config.MACRO_RSI_THRESHOLD):
        log.info(
            "[%s] RSI %.1f ≤ threshold %.0f — skip",
            name, rsi_val, config.MACRO_RSI_THRESHOLD,
        )
        return False

    # Sizing
    qty, sl, reason = size_position(
        entry_price=last_close, atr_value=atr_val, lot_size=inst["lot_size"]
    )
    if qty <= 0:
        log.warning("[%s] SIGNAL but sizing rejected: %s", name, reason)
        return False

    log.info(
        "[%s] ★ ENTRY SIGNAL ★ qty=%d entry≈%.2f sl=%.2f cost≈₹%.0f",
        name, qty, last_close, sl, qty * last_close,
    )

    if dry_run:
        log.info("[%s] DRY-RUN — order suppressed", name)
        return True

    # Refuse new entries while a halt is latched or the kill switch has tripped.
    allowed, gate_reason = execution.entry_gate(
        db, engine_label="MACRO",
        date_str=datetime.now(config.IST).strftime("%Y-%m-%d"),
    )
    if not allowed:
        log.critical("[%s] ENTRY REFUSED — %s", name, gate_reason)
        return False

    # Place market entry (simulated automatically in dev / preprod)
    result = execution.place_market(
        api,
        symbol=inst["trading_symbol"],
        token=inst["token"],
        exchange=inst["exchange"],
        side="BUY",
        quantity=qty,
        product_type=asset["product_type"],
        wait_for_fill_s=15.0,
    )
    if not result:
        log.critical("[%s] MARKET BUY FAILED — no SL placed", name)
        return False
    order_id, fill_price = result
    fill_price = fill_price or last_close

    # ── V2 (25 May 2026) GTT MIGRATION ──────────────────────────────────
    # Angel auto-cancels every STOPLOSS_MARKET order at ~15:30 IST EOD,
    # leaving DELIVERY positions naked against overnight gap-down.  We now
    # use a Good-Till-Triggered SELL rule (timeperiod=365) which lives at
    # the exchange and persists across sessions.  Trigger = ATR-based SL
    # (unchanged).  Limit = trigger × (1 - 0.5%) so a gap-down sweeps the
    # book like a market order.
    gtt_limit = round(sl * (1.0 - GTT_LIMIT_BUFFER_PCT), 2)
    gtt_rule_id = execution.create_gtt_sell_rule(
        api,
        tradingsymbol=inst["trading_symbol"],
        symboltoken=inst["token"],
        exchange=inst["exchange"],
        qty=qty,
        trigger_price=sl,
        limit_price=gtt_limit,
        product_type=asset["product_type"],
        timeperiod=365,
    )
    if not gtt_rule_id:
        log.critical(
            "[%s] GTT-SL CREATION FAILED — position is OPEN but UNHEDGED. "
            "Manual GTT placement REQUIRED via Angel One app.",
            name,
        )

    # Persist to DB
    db.add_position(
        symbol=inst["trading_symbol"],
        token=inst["token"],
        exchange=inst["exchange"],
        product_type=asset["product_type"],
        quantity=qty,
        entry_price=fill_price,
        stop_loss=sl,
        order_id=order_id,
        meta={
            "donchian_high":  dc_high,
            "rsi":            rsi_val,
            "atr":            atr_val,
            "gtt_rule_id":    gtt_rule_id,     # V2: replaces sl_order_id
            "gtt_trigger":    sl,
            "gtt_limit":      gtt_limit,
            "sl_mechanism":   "GTT",
        },
    )
    db.record_trade(
        side="BUY",
        symbol=inst["trading_symbol"],
        quantity=qty,
        price=fill_price,
        order_type="MARKET",
        order_id=order_id,
        meta={"donchian_breakout": True, "rsi": rsi_val},
    )
    log.info(
        "[%s] ✅ POSITION OPENED qty=%d @ ₹%.2f sl=₹%.2f (GTT trig=%.2f lim=%.2f) "
        "oid=%s gtt_rule_id=%s",
        name, qty, fill_price, sl, sl, gtt_limit, order_id, gtt_rule_id,
    )
    return True


# ════════════════════════════════════════════════════════════════════════════
#  EXIT EVALUATION  (broker-side SL is primary; this is belt-and-braces)
# ════════════════════════════════════════════════════════════════════════════
def evaluate_exits(api, dry_run: bool, db: BotDB) -> int:
    """Loop existing positions; if LTP < SL and the broker-side SL didn't
    fire, force-exit MARKET as a safety net.  Returns count of exits.
    """
    open_pos = db.open_positions()
    if not open_pos:
        log.info("PHASE 1: No open positions.")
        return 0

    log.info("PHASE 1: Evaluating %d open position(s)…", len(open_pos))
    exits = 0
    for pos in open_pos:
        sym = pos["symbol"]
        ltp = broker.fetch_ltp(
            api, symbol=sym, token=pos["token"], exchange=pos["exchange"]
        )
        if ltp is None:
            log.warning("[%s] LTP unavailable — leaving position untouched", sym)
            continue

        sl = pos.get("stop_loss") or 0.0
        log.info(
            "[%s] ltp=%.2f entry=%.2f sl=%.2f qty=%d",
            sym, ltp, pos["entry_price"], sl, pos["quantity"],
        )

        if sl > 0 and ltp < sl:
            log.warning(
                "[%s] LTP %.2f < SL %.2f — broker SL should have fired; "
                "force-exiting as safety net",
                sym, ltp, sl,
            )
            if dry_run:
                log.info("[%s] DRY-RUN — exit suppressed", sym)
                continue

            # ── V2 GTT migration ──────────────────────────────────────
            # Cancel the resting GTT SELL rule BEFORE firing the market
            # sweep — otherwise the GTT stays armed and could fire later
            # against an empty DEMAT, triggering a phantom short-sell
            # rejection (or worse, a margin call if naked-short is on).
            try:
                _meta = json.loads(pos.get("meta_json") or "{}")
            except (ValueError, TypeError):
                _meta = {}
            _gtt_rid = _meta.get("gtt_rule_id")
            if _gtt_rid:
                ok = execution.cancel_gtt_rule(
                    api,
                    rule_id=str(_gtt_rid),
                    symboltoken=pos["token"],
                    exchange=pos["exchange"],
                )
                if not ok:
                    log.critical(
                        "[%s] GTT cancel FAILED (rule_id=%s) — proceeding "
                        "with MARKET sell, but you MUST manually cancel "
                        "the orphaned GTT in your Angel One app.",
                        sym, _gtt_rid,
                    )
            else:
                log.warning(
                    "[%s] No gtt_rule_id in meta_json (legacy pre-V2 "
                    "position?) — proceeding without GTT cancel.", sym,
                )

            result = execution.place_market(
                api,
                symbol=sym,
                token=pos["token"],
                exchange=pos["exchange"],
                side="SELL",
                quantity=pos["quantity"],
                product_type=pos["product_type"],
                wait_for_fill_s=15.0,
            )
            if result:
                oid, fill_px = result
                # Net of real charges, and booked into daily_pnl atomically so
                # this engine finally has a realised-P&L ledger (and therefore
                # a kill switch that can actually fire).
                rt = costs.round_trip(
                    segment=costs.segment_for(pos["exchange"]),
                    entry=float(pos["entry_price"]),
                    exit_=fill_px,
                    qty=int(pos["quantity"]),
                )
                pnl = rt.net_pnl
                db.close_position(
                    side="SELL",
                    symbol=sym,
                    quantity=pos["quantity"],
                    price=fill_px,
                    order_type="MARKET",
                    order_id=oid,
                    pnl=pnl,
                    meta={
                        "reason": "stop_loss_safety_net",
                        "gross_pnl": rt.gross_pnl,
                        "charges": rt.total_charges,
                    },
                    book_pnl_date=datetime.now(config.IST).strftime("%Y-%m-%d"),
                )
                log.info(
                    "[%s] CLOSED qty=%d @ ₹%.2f gross=₹%+.2f charges=₹%.2f "
                    "net=₹%+.2f",
                    sym, pos["quantity"], fill_px, rt.gross_pnl,
                    rt.total_charges, pnl,
                )
                exits += 1
            else:
                log.critical("[%s] SAFETY-NET EXIT FAILED — manual intervention required",
                             sym)
    return exits


# ════════════════════════════════════════════════════════════════════════════
#  LOGGING SETUP
# ════════════════════════════════════════════════════════════════════════════
def setup_logging(level: str) -> None:
    today = datetime.now(config.IST).strftime("%Y-%m-%d")
    log_path = config.LOG_DIR / f"macro_{today}.log"
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
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan and log signals but place no orders")
    parser.add_argument("--force", action="store_true",
                        help="Bypass NSE trading-day guard")
    args = parser.parse_args()

    setup_logging(config.LOG_LEVEL)

    log.info(config.banner())
    log.info("=" * 72)
    log.info("macro_engine starting | mode=%s",
             "DRY-RUN" if args.dry_run else "LIVE")
    log.info("DB: %s (env=%s)", config._redacted_dsn(), config.APP_ENV)
    log.info("Universe: %s", [a["name"] for a in MACRO_UNIVERSE])
    log.info("Capital silo: ₹%.0f | risk %.1f%%/trade (₹%.0f)",
             config.MACRO_MAX_CAPITAL,
             config.MACRO_RISK_PCT * 100,
             config.risk_budget(config.MACRO_MAX_CAPITAL, config.MACRO_RISK_PCT))
    log.info("=" * 72)

    if not args.force:
        if not nse_calendar.is_nse_trading_day():
            log.warning("Trading-day guard: SKIP (%s)",
                        nse_calendar.reason_market_closed())
            return 0

    log.info(execution.mode_banner())
    wait_for_database()
    db = BotDB("MACRO")

    log.info("Authenticating to Angel One…")
    try:
        api = login()
    except Exception as exc:
        log.critical("Login failed: %s", exc)
        return 1
    log.info("Authenticated as %s", config.CLIENT_ID)

    setups_found = 0
    exits_done = 0
    try:
        exits_done = evaluate_exits(api, args.dry_run, db)
        log.info("PHASE 2: Scanning %d universe assets…", len(MACRO_UNIVERSE))
        for asset in MACRO_UNIVERSE:
            try:
                if evaluate_entry(api, asset, args.dry_run, db):
                    setups_found += 1
            except Exception as exc:
                log.exception("[%s] Evaluation crashed: %s", asset["name"], exc)
    finally:
        terminate(api)

    log.info("=" * 72)
    log.info("Macro pass complete | setups=%d exits=%d", setups_found, exits_done)
    log.info("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
