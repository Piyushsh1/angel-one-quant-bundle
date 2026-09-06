"""algo-barbell  ▸  option_predator.py
================================================================================
THE HIGH-RISK UPSIDE  —  20% of barbell capital  (₹5,000)

⚠ HIGH-RISK WARNING
───────────────────
This bot trades weekly Nifty / Bank Nifty ATM options as 1-lot intraday
momentum bets.  Expected behaviour:

    * 60-70% strike-to-zero rate (ATM weekly options)
    * 8-15%/day theta decay even on flat days
    * Single losing trade can wipe 70-100% of silo
    * Daily-loss kill switch trips at -₹1,500 (30% of silo)

This is the *Talebian asymmetric* leg.  The silo is *expected* to zero out
within 4-8 weeks of live trading.  The bet is that occasional 5x-10x
winners on big trend days more than offset the persistent small losses.

BOT STARTS IN PAPER MODE (``OPTIONS_PAPER_MODE=true``).  Switch to live
ONLY after ≥1 trading day of paper-mode signals look reasonable.

MANDATE
───────
Underlyings: NIFTY 50 spot + BANK NIFTY spot (NSE indices).
Logic, every minute after 09:31 IST:

    1.  Establish opening 15-minute range (09:15–09:30) per underlying.
        OR_high = max(high) over those 15 minutes.
        OR_low  = min(low)  over those 15 minutes.
        OR_avg_vol = mean(volume) over those 15 minutes.

    2.  When the 1-min CLOSE breaks OR_high (long) or OR_low (short)
        AND the breakout candle's volume ≥ 3.0 × OR_avg_vol:

           - Long  → resolve nearest weekly ATM CALL strike, BUY 1 lot @ MARKET
           - Short → resolve nearest weekly ATM PUT  strike, BUY 1 lot @ MARKET

    3.  Per option position:
           - Hard SL  = -10 % of premium  (broker-side STOPLOSS_MARKET)
           - Target   = +20 % of premium  (in-process monitor; SELL @ MARKET on hit)
           - Hard exit = OPTIONS_HARD_EXIT_TIME (15:05 IST) — square-off ALL

    4.  At most ONE active position per underlying.

KILL SWITCHES
─────────────
    * If realised + unrealised P&L ≤ OPTIONS_DAILY_LOSS (-₹1,500)
      → trigger_kill_switch() in DB; refuse new entries; do NOT
      auto-square-off (let SL handle it; we don't compound losses with bad fills).
    * On instance restart, kill_switch state is recovered from DB.
    * After OPTIONS_HARD_EXIT_TIME we exit all and stop.

CRON
────
This bot does NOT run on cron — it runs as a *long-lived* PM2 process from
~09:14 IST to 15:10 IST.  Use ``cron_restart: '14 3 * * 1-5'`` (03:44 UTC =
09:14 IST) plus ``autorestart: false`` so it boots fresh every morning and
naturally exits after square-off.

CLI
───
    python option_predator.py                # paper mode (forced if .env says so)
    python option_predator.py --paper        # force paper mode regardless of .env
    python option_predator.py --force        # bypass NSE trading-day guard

ISOLATION
─────────
* Own rows in the shared database, scoped by ``engine='OPTIONS'``
  (incl. daily_pnl + kill switch)
* Own log file in ``logs/options_<YYYY-MM-DD>.log``
* Capital strictly fenced by OPTIONS_MAX_CAPITAL
* Never imports from macro_engine or smallcap_engine
================================================================================
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import json
import sys
import time
from datetime import datetime, time as dtime, timedelta
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
from retry import is_margin_reject

log = logging.getLogger("options")


# ════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ════════════════════════════════════════════════════════════════════════════
# Spot-index tokens (Angel One — these are the SPOT INDICES, not lot 1 futures).
# Tokens verified against the live Angel scrip master (NSE / AMXIDX segment).
# Lot sizes / strike steps below are for OPTION-side bookkeeping; the actual
# OPTIDX lot size used to compute qty is pulled live from the scrip master by
# instrument_master.resolve_option() at order-build time.
NIFTY_SPOT      = {"name": "NIFTY",      "token": "99926000", "exchange": "NSE", "strike_step":  50}
BANKNIFTY_SPOT  = {"name": "BANKNIFTY",  "token": "99926009", "exchange": "NSE", "strike_step": 100}
FINNIFTY_SPOT   = {"name": "FINNIFTY",   "token": "99926037", "exchange": "NSE", "strike_step":  50}
MIDCPNIFTY_SPOT = {"name": "MIDCPNIFTY", "token": "99926074", "exchange": "NSE", "strike_step":  25}

# ── V3.1 Multi-Index Expansion (22 May 2026) ─────────────────────────
# Adding FINNIFTY + MIDCPNIFTY broadens the breakout scan-surface so the
# Phase B Scaled Runner Exit (Pillar 2) finally gets exercised in paper.
# All four indices share the same per-trade silo, daily kill switch and
# max_lots cap defined in config.  The dynamic lot-sizer in `open_position`
# downsizes automatically if a single OPTIDX lot * premium exceeds the silo,
# so MIDCPNIFTY (lot 120) trades will gracefully fall back to 1-lot mode.
UNDERLYINGS = [NIFTY_SPOT, BANKNIFTY_SPOT, FINNIFTY_SPOT, MIDCPNIFTY_SPOT]

# Weekly-expiry calendar (informational only — pure operator awareness).
# Maps weekday → underlying whose weekly contract expires THAT day.  Used at
# boot to log "today the X weekly expires", which historically produces the
# highest intraday IV and therefore the best Pillar 2 activation odds.
# Source: NSE FO master calendar effective 2024 (BANKNIFTY moved to monthly
#         but the spec still maps it to Wednesday for legacy continuity).
WEEKLY_EXPIRY_CALENDAR = {
    0: "MIDCPNIFTY",   # Monday
    1: "FINNIFTY",     # Tuesday
    2: "BANKNIFTY",    # Wednesday  (monthly post-2024, but kept for awareness)
    3: "NIFTY",        # Thursday
    # Friday / Sat / Sun → no weekly expiry
}

# Trading windows (IST)
SESSION_OPEN = dtime(9, 15)
OR_END = dtime(9, 30)              # 15-min ORB ends here
SCAN_START = dtime(9, 31)          # First minute we may breakout
LOGIN_RETRY_AT = dtime(9, 14)      # Bot starts here per cron
PROCESS_END = dtime(15, 10)        # Bot exits at this point regardless

# Risk constants
# ──────────────────────────────────────────────────────────────────────────
# Free-Ride Trailing Stop (TTP) replaces the old hard +20% target.
#   1. Initial SL  = entry × (1 - SL_PCT)               i.e. -6%
#   2. When ltp ≥ entry × (1 + TRAIL_TRIGGER_PCT) → SL hops to break-even
#      (entry price).  This is the "free ride" — risk neutralised.
#   3. Thereafter SL = max(SL, peak_premium × (1 - TRAIL_DISTANCE_PCT)),
#      capped never to fall below entry.  As premium rallies, SL ratchets
#      upward; if premium reverses 15% from its peak, position exits at MARKET.
#
# REGIME-FILTER CALIBRATION (2026-06-08 backtest, 59d NIFTY 15m, 16-config sweep):
#   * Trading every day is structurally -EV (long premium pays daily theta tax
#     on the ~80% of flat/choppy days where the breakout fails).
#   * Filtering to wide-opening-range days (OR range ≥ MIN_ORB_RANGE_PCT of spot)
#     flipped EV positive: +₹210/trade @ PF 1.68 (≥0.6%), and +₹361 @ PF 2.43
#     with tight SL 6% + runner trail 15%.  Gap-based filters did NOT work; only
#     the opening-range width predicts a tradeable (trending) day.
#   * Lowering the trail trigger backfired (-EV); ITM strike upgrade backfired
#     (doubled loss-per-trade).  Both discarded.
# Knobs are env-driven so they can be reverted without a code change.
# ──────────────────────────────────────────────────────────────────────────
SL_PCT             = float(os.getenv("OPTIONS_SL_PCT", "0.06"))
TRAIL_TRIGGER_PCT  = float(os.getenv("OPTIONS_TRAIL_TRIGGER_PCT", "0.20"))
TRAIL_DISTANCE_PCT = float(os.getenv("OPTIONS_TRAIL_DISTANCE_PCT", "0.15"))
# Regime gate: minimum opening-range width as % of spot to trade the day.
# 0 = disabled (legacy "trade every day" behaviour).  Backtest sweet-spot 0.55-0.6.
MIN_ORB_RANGE_PCT  = float(os.getenv("OPTIONS_MIN_ORB_RANGE_PCT", "0.55"))

# OR-fetch pacing + rate-limit-aware retry.
# 09 Jun 2026 incident: the 09:31 burst of 4×(spot+futures) historical-candle
# calls tripped Angel One's rate limiter ("Access denied because of exceeding
# access rate"); the first call (NIFTY) was dropped and NIFTY sat out the WHOLE
# expiry day.  We now (a) pace the per-underlying OR computations and (b) retry
# a failed/empty OR with a longer backoff that actually clears Angel's cooldown.
# The opening range (09:15–09:30) is a COMPLETED historical window, so retrying
# minutes later returns the same correct candles — no look-ahead risk.
OR_FETCH_PACING_SEC  = float(os.getenv("OPTIONS_OR_FETCH_PACING_SEC", "1.5"))
OR_FETCH_RETRY       = int(os.getenv("OPTIONS_OR_FETCH_RETRY", "3"))
OR_FETCH_BACKOFF_SEC = float(os.getenv("OPTIONS_OR_FETCH_BACKOFF_SEC", "5.0"))

# Scan-loop fetch pacing — spaces the per-underlying price-candle fetches inside
# the 1-min poll so several armed indices don't burst Angel's historical-data
# rate limiter at the top of each minute.  Only paces the 2nd+ fetch in a cycle,
# so a single armed underlying (today's case) is unaffected.  Well within the
# 60s cadence even with all 4 indices armed (≈3×0.8s = 2.4s added).
SCAN_FETCH_PACING_SEC = float(os.getenv("OPTIONS_SCAN_FETCH_PACING_SEC", "0.8"))
# V2 calibration: was hardcoded 3.0× (audit-week, structurally dormant).
# Now sourced from config.OPTIONS_VOL_MULT (.env-driven, default 1.5×).
BREAKOUT_VOLUME_MULT: float = config.OPTIONS_VOL_MULT

# Position sizing
MAX_LOTS           = int(os.getenv("OPTIONS_MAX_LOTS", "2"))
OTM_FALLBACK_STEPS = int(os.getenv("OPTIONS_OTM_FALLBACK_STEPS", "2"))

# Partial-SELL fund-rejection cooldown
# ──────────────────────────────────────────────────────────────────────────
# Angel One's pre-trade margin gate requires full SPAN+ELM cash even on a
# SELL that *closes* an existing long option, until the trade settles.  If
# the account is under-funded, every Phase-B partial scale-out will be
# rejected with "Insufficient Funds".  Without a cooldown the monitor loop
# burns ~1 API call per 30 s on identical guaranteed-reject orders.  When
# we detect that specific reject text on a SELL, we freeze SELL attempts
# for this symbol for N minutes (default 60 — funds rarely appear mid-session).
PARTIAL_SELL_FUND_COOLDOWN_MIN = int(
    os.getenv("OPTIONS_PARTIAL_SELL_FUND_COOLDOWN_MIN", "60")
)
_SELL_FUND_BLOCK: dict[str, datetime] = {}


def _fund_block_remaining_min(symbol: str) -> int:
    """Return minutes remaining on the SELL-fund-block cooldown for `symbol`.

    Returns 0 if no block is active.  Mid-session insufficient-funds rejects
    trigger this guard via :func:`_execute_partial_sell`.
    """
    blocked_at = _SELL_FUND_BLOCK.get(symbol)
    if blocked_at is None:
        return 0
    elapsed_min = (now_ist() - blocked_at).total_seconds() / 60.0
    return max(0, int(round(PARTIAL_SELL_FUND_COOLDOWN_MIN - elapsed_min)))

# ════════════════════════════════════════════════════════════════════════════
#  POSITION TRUTH RECONCILER  (zero-trust DB↔broker sync invariant)
# ════════════════════════════════════════════════════════════════════════════
#  Three guarantees this module provides:
#
#    1. boot_reconcile(api, db)
#         Called once on startup BEFORE the main loop.  Pulls the live
#         position book from the broker and aligns DB rows to reality:
#            - DB row present, broker netqty=0  → record synthetic close
#                                                 (uses sellavgprice) and
#                                                 delete the DB row.
#            - DB row present, broker netqty=DB qty  → in sync, no-op.
#            - DB row absent, broker netqty != 0    → BROKER HOLDS A
#                                                 POSITION WE DON'T KNOW
#                                                 ABOUT.  This is a hard
#                                                 mismatch — we LATCH the
#                                                 position_halt flag and
#                                                 return non-zero so the
#                                                 operator must intervene.
#            - DB row present, broker netqty != DB qty (and !=0)
#                                              → partial mismatch — latch
#                                                 halt, refuse to mutate
#                                                 DB on guesses.
#
#    2. assert_db_matches_broker(api, db, symbol)
#         Called before every entry/exit decision.  If the symbol's DB
#         row and broker netqty disagree (broker says flat, we think we
#         hold; or vice versa) the engine immediately latches the halt
#         flag and aborts the current decision.  Returns True iff the
#         broker confirms the DB state — caller MUST honour the False
#         return as a hard refusal.
#
#    3. is_position_halt_active(db)
#         Cheap DB-only check used by the entry-scan gate every minute.
#         When True, every new-entry path returns immediately.  Exits
#         (close_position, SL modify) are STILL allowed — we must be
#         able to flatten an unsynced position once the operator
#         clears the halt.
#
#  Why halt-on-mismatch instead of auto-fix?  Because auto-fixing in the
#  "broker holds something the DB doesn't know about" direction means
#  writing synthetic BUY rows with a price we can only guess at (the
#  broker's buyavgprice is a weighted average and may include trades
#  from before the bot was running).  That can corrupt P&L attribution.
#  Operator decides: either flatten manually + clear halt, or run
#  reconcile_after_manual_exit.py and clear halt.
# ════════════════════════════════════════════════════════════════════════════
def is_position_halt_active(db: "BotDB") -> bool:
    """O(1) DB check used by the entry-scan gate every minute."""
    halted, _ = db.is_halted()
    return halted


def boot_reconcile(api, db: "BotDB") -> int:
    """Align DB with broker reality at startup.  MUST be called once,
    after authentication, before the main poll loop.

    Returns
    -------
    int
        Number of actions taken (synthetic closes + halts).  Caller can
        log this for observability but should NOT abort startup based
        on the value alone — the halt flag is the authoritative signal.
    """

    log.info("=" * 72)
    log.info("BOOT RECONCILE — verifying DB rows match broker position book")
    log.info("=" * 72)

    db_open = db.open_positions()
    broker_book = broker.fetch_position_book(api)
    if not broker_book and db_open:
        # Probe failed AND we have DB rows — refuse to assume "broker is flat".
        # Halt is the only safe move; operator can clear once they confirm.
        db.halt_trading(
            "boot_reconcile: fetch_position_book returned empty while DB shows open positions"
        )
        log.critical(
            "BOOT RECONCILE: broker probe failed with %d open DB row(s) — "
            "HALT latched; cannot distinguish 'broker is flat' from 'API hiccup'",
            len(db_open),
        )
        return 1

    # DB ∪ Broker — iterate both directions.
    db_symbols     = {p["symbol"] for p in db_open}
    broker_symbols = {s for s, r in broker_book.items() if r["netqty"] != 0}

    actions = 0
    today = now_ist().strftime("%Y-%m-%d")

    # Direction 1: DB has rows the broker disagrees with.
    for p in db_open:
        sym = p["symbol"]
        db_qty = int(p["quantity"])
        entry_px = float(p["entry_price"])
        bpos = broker_book.get(sym)

        if bpos is None or bpos["netqty"] == 0:
            sellavg  = (bpos or {}).get("sellavgprice") or 0.0
            sellqty  = (bpos or {}).get("sellqty") or 0
            if sellavg > 0 and sellqty >= db_qty:
                rt = costs.round_trip(
                    segment="OPTIONS", entry=entry_px, exit_=sellavg, qty=db_qty,
                )
                realised = rt.net_pnl
                log.critical(
                    "BOOT RECONCILE [%s] broker FLAT, DB qty=%d — "
                    "recording synthetic close @ ₹%.2f (net P&L=%+.2f, "
                    "charges=₹%.2f)",
                    sym, db_qty, sellavg, realised, rt.total_charges,
                )
                # P&L booked atomically with the close so a crash here cannot
                # double-count it on the next reconcile.
                db.close_position(
                    symbol=sym, side="SELL", quantity=db_qty,
                    price=sellavg, order_type="MARKET",
                    order_id="boot_reconcile",
                    pnl=realised,
                    meta={"reason": "boot_reconcile_synthetic_close",
                          "broker_sellqty": sellqty,
                          "broker_sellavg": sellavg,
                          "gross_pnl": rt.gross_pnl,
                          "charges": rt.total_charges},
                    book_pnl_date=today,
                )
                actions += 1
            else:
                # Broker shows flat but we can't trust sellavgprice.  Halt.
                db.halt_trading(
                    f"boot_reconcile: [{sym}] broker netqty=0 but sellavg={sellavg} "
                    f"sellqty={sellqty} — cannot synthesise close"
                )
                actions += 1
            continue

        if bpos["netqty"] != db_qty:
            db.halt_trading(
                f"boot_reconcile: [{sym}] DB qty={db_qty} ≠ broker netqty={bpos['netqty']}"
            )
            log.critical(
                "BOOT RECONCILE [%s] PARTIAL MISMATCH — DB qty=%d broker netqty=%d. "
                "HALT latched; operator MUST reconcile.",
                sym, db_qty, bpos["netqty"],
            )
            actions += 1
            continue

        log.info("BOOT RECONCILE [%s] in sync — DB qty=%d broker netqty=%d",
                 sym, db_qty, bpos["netqty"])

    # Direction 2: broker holds a position we know nothing about.
    orphan_broker = broker_symbols - db_symbols
    for sym in orphan_broker:
        bpos = broker_book[sym]
        db.halt_trading(
            f"boot_reconcile: [{sym}] broker netqty={bpos['netqty']} but no DB row"
        )
        log.critical(
            "BOOT RECONCILE [%s] BROKER HOLDS UNTRACKED POSITION — "
            "netqty=%d buyavg=%.2f.  HALT latched; operator MUST either "
            "flatten via Angel app or run reconcile + manual DB insert.",
            sym, bpos["netqty"], bpos["buyavgprice"],
        )
        actions += 1

    db.set_state("last_broker_sync_ts",
                 now_ist().isoformat(timespec="seconds"))
    halted, halt_reason = db.is_halted()
    if halted:
        log.critical("BOOT RECONCILE: HALT ACTIVE — reason=%r. "
                     "Bot will refuse new entries until cleared.", halt_reason)
    else:
        log.info("BOOT RECONCILE: all positions match broker — %d action(s).",
                 actions)
    log.info("=" * 72)
    return actions


def assert_db_matches_broker(
    api, db: "BotDB", symbol: str, expected_qty: int,
) -> bool:
    """Per-decision invariant.  Returns True iff broker netqty == expected_qty
    for ``symbol``.  On mismatch: latch halt + return False.

    Called BEFORE every entry (expected_qty=0 → must be flat) and BEFORE
    every exit (expected_qty=DB qty → must match).  A False return is a
    hard refusal — the caller MUST NOT proceed with the order.
    """
    book = broker.fetch_position_book(api)
    if not book and expected_qty != 0:
        # Probe failed while we expected a non-zero position.  Don't trust
        # silence — refuse the decision.  (For new entries where expected
        # is 0, we tolerate empty: an empty book is consistent with "flat",
        # and we'd rather not block new entries on every transient.)
        log.warning(
            "[%s] assert_db_matches_broker: probe failed (empty book) for "
            "expected_qty=%d — refusing decision (transient).",
            symbol, expected_qty,
        )
        return False

    broker_qty = book.get(symbol, {}).get("netqty", 0)
    if broker_qty == expected_qty:
        return True

    db.halt_trading(
        f"runtime: [{symbol}] expected qty={expected_qty} but broker netqty={broker_qty}"
    )
    log.critical(
        "[%s] DB↔BROKER MISMATCH — expected qty=%d, broker netqty=%d. "
        "HALT latched; refusing decision.",
        symbol, expected_qty, broker_qty,
    )
    return False


# ── V3 Alpha Layer  ───────────────────────────────────────────────────────
# HTF (Higher-Timeframe) bias: NIFTY daily-SMA trend filter computed at boot
# and applied to every entry attempt.  CE-only when BULLISH, PE-only when
# BEARISH.  Module-level state — set in PHASE-0c of main(), read by the
# per-minute breakout logic.
HTF_BIAS_ENABLED: bool = config.OPTIONS_HTF_BIAS_ENABLED
HTF_SMA_PERIOD:   int  = config.OPTIONS_HTF_SMA_PERIOD
DAILY_BIAS: str        = "NEUTRAL"     # mutated by compute_daily_bias()

# Intraday dead zone: NEW entries blocked inside this IST window; the
# monitor_positions() loop keeps running (trail / SL / hard exit / kill
# switch all live).
DEAD_ZONE_START: dtime = config.OPTIONS_DEAD_ZONE_START
DEAD_ZONE_END:   dtime = config.OPTIONS_DEAD_ZONE_END

# Underlying used for the daily bias (architect spec: Nifty 50 daily chart).
# Token 99926000 is NIFTY spot index on NSE — same series we already scan.
HTF_BIAS_TOKEN    = "99926000"
HTF_BIAS_EXCHANGE = "NSE"
HTF_BIAS_NAME     = "NIFTY"


# ════════════════════════════════════════════════════════════════════════════
#  IST CLOCK HELPERS
# ════════════════════════════════════════════════════════════════════════════
def now_ist() -> datetime:
    return datetime.now(config.IST)


def is_after(t: dtime) -> bool:
    return now_ist().time() >= t


def sleep_until(target: dtime, max_secs: float = 120.0) -> None:
    """Sleep at most ``max_secs`` toward target time; returns when reached."""
    while now_ist().time() < target:
        delta = (datetime.combine(now_ist().date(), target, tzinfo=config.IST)
                 - now_ist()).total_seconds()
        if delta <= 0:
            return
        time.sleep(min(delta, max_secs))


def in_dead_zone(t: Optional[dtime] = None) -> bool:
    """``True`` iff ``t`` (default: now IST) is inside the no-entry dead zone.

    Dead Zone is purely an ENTRY filter: existing positions continue to be
    monitored (trail / SL / hard exit / kill switch all live).  Used by the
    main 1-min loop to skip ``open_position`` calls without touching
    ``monitor_positions``.
    """
    cur = t or now_ist().time()
    return DEAD_ZONE_START <= cur < DEAD_ZONE_END


# ════════════════════════════════════════════════════════════════════════════
#  HTF DAILY BIAS  (V3 Trend Alignment filter)
# ════════════════════════════════════════════════════════════════════════════
def compute_daily_bias(api) -> str:
    """Fetch NIFTY daily candles and label the day BULLISH / BEARISH / NEUTRAL.

    Logic (architect spec):
        prior_day_close > SMA(N)   → BULLISH  (CE-only intraday)
        prior_day_close < SMA(N)   → BEARISH  (PE-only intraday)
        prior_day_close == SMA(N)  → NEUTRAL  (no filter — fall back to V2)

    The "prior day" is yesterday's close, NOT today's running candle (because
    today's bar is still in-progress at 09:14).  Any failure (API error,
    insufficient history) returns "NEUTRAL" so the bot never refuses to
    trade purely because the bias couldn't be fetched.
    """
    # Pull ~3× the SMA period of daily bars so we always have a full window
    # plus weekends/holidays cushion.
    lookback_days = max(HTF_SMA_PERIOD * 2 + 20, 120)
    end = now_ist()
    start = end - timedelta(days=lookback_days)
    fmt_day = "%Y-%m-%d 00:00"
    params = {
        "exchange":    HTF_BIAS_EXCHANGE,
        "symboltoken": HTF_BIAS_TOKEN,
        "interval":    "ONE_DAY",
        "fromdate":    start.strftime(fmt_day),
        "todate":      end.strftime(fmt_day),
    }
    try:
        resp = broker._candle_raw(api, params)  # noqa: SLF001
    except Exception as exc:
        log.warning("HTF bias: daily candle fetch failed (%s) — NEUTRAL", exc)
        return "NEUTRAL"
    if not resp or not resp.get("status"):
        log.warning("HTF bias: empty daily-candle response — NEUTRAL")
        return "NEUTRAL"
    rows = resp.get("data", []) or []
    if len(rows) < HTF_SMA_PERIOD + 1:
        log.warning(
            "HTF bias: only %d daily bars (need ≥ %d) — NEUTRAL",
            len(rows), HTF_SMA_PERIOD + 1,
        )
        return "NEUTRAL"
    df = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("timestamp").reset_index(drop=True)

    # The most recent row may be TODAY's running bar (Angel sometimes returns
    # a partial bar dated for today).  Drop any row whose date == today_IST
    # so we always reason off the *prior* completed daily close.
    today_d = now_ist().date()
    df = df[df["timestamp"].dt.date < today_d].reset_index(drop=True)
    if len(df) < HTF_SMA_PERIOD + 1:
        log.warning(
            "HTF bias: after dropping today's partial bar, only %d completed "
            "daily bars (need ≥ %d) — NEUTRAL",
            len(df), HTF_SMA_PERIOD + 1,
        )
        return "NEUTRAL"
    prior_close = float(df.iloc[-1]["close"])
    sma_window = df["close"].iloc[-HTF_SMA_PERIOD:]
    sma = float(sma_window.mean())

    if prior_close > sma:
        bias = "BULLISH"
    elif prior_close < sma:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    log.info(
        "HTF bias: NIFTY prior_close=%.2f vs SMA(%d)=%.2f → %s",
        prior_close, HTF_SMA_PERIOD, sma, bias,
    )
    return bias


def htf_bias_allows(side: str) -> bool:
    """Return True iff a breakout of ``side`` ('LONG'/'SHORT') aligns with the
    current ``DAILY_BIAS``.

    Filter disabled → always True.  NEUTRAL bias → always True.  Otherwise:
        BULLISH bias  → only LONG  allowed (CE entries)
        BEARISH bias  → only SHORT allowed (PE entries)
    """
    if not HTF_BIAS_ENABLED:
        return True
    if DAILY_BIAS == "BULLISH":
        return side == "LONG"
    if DAILY_BIAS == "BEARISH":
        return side == "SHORT"
    return True  # NEUTRAL or any unknown state — fall back to V2


# ════════════════════════════════════════════════════════════════════════════
#  CANDLES → DataFrame
# ════════════════════════════════════════════════════════════════════════════
def _fetch_candles(
    api, *, token: str, exchange: str, name: str,
    start: datetime, end: datetime, interval: str = "ONE_MINUTE",
) -> pd.DataFrame:
    """Low-level candle fetch.  Internal helper used by both spot and futures."""
    fmt = "%Y-%m-%d %H:%M"
    params = {
        "exchange":    exchange,
        "symboltoken": token,
        "interval":    interval,
        "fromdate":    start.strftime(fmt),
        "todate":      end.strftime(fmt),
    }
    try:
        resp = broker._candle_raw(api, params)  # noqa: SLF001
    except Exception as exc:
        log.warning("[%s] candle fetch failed: %s", name, exc)
        return pd.DataFrame()
    if not resp or not resp.get("status"):
        return pd.DataFrame()
    rows = resp.get("data", []) or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"]).reset_index(drop=True)


def fetch_intraday(api, underlying: dict, lookback_minutes: int = 60) -> pd.DataFrame:
    """Fetch 1-minute intraday candles for a spot index.

    NOTE: Spot indices (NIFTY, BANKNIFTY) return ``volume=0`` from Angel's
    historical-candle endpoint — they are price-only indices.  The caller
    must overlay volume from :func:`fetch_futures_volume` for any
    conviction-based filtering (breakout vol gate, OR-vol average).
    """
    end = now_ist()
    start = end - timedelta(minutes=lookback_minutes + 5)
    return _fetch_candles(
        api,
        token=underlying["token"],
        exchange=underlying["exchange"],
        name=underlying["name"],
        start=start,
        end=end,
    )


def fetch_futures_volume(
    api, underlying: dict, *, lookback_minutes: int, end: Optional[datetime] = None,
) -> pd.DataFrame:
    """Fetch 1-minute volume series from the underlying's index-futures contract.

    Returns a 2-column DataFrame ``[timestamp, volume]`` (or empty) that can
    be joined onto the spot DataFrame by ``timestamp``.  The futures contract
    is resolved once at startup and cached on the underlying dict under
    ``futures_token``/``futures_exchange``; if absent, returns empty.
    """
    if not underlying.get("futures_token"):
        return pd.DataFrame()
    end = end or now_ist()
    start = end - timedelta(minutes=lookback_minutes + 5)
    df = _fetch_candles(
        api,
        token=underlying["futures_token"],
        exchange=underlying.get("futures_exchange", "NFO"),
        name=underlying["name"] + "_FUT",
        start=start,
        end=end,
    )
    if df.empty:
        return df
    return df[["timestamp", "volume"]].rename(columns={"volume": "fut_volume"})


# ════════════════════════════════════════════════════════════════════════════
#  OPENING-RANGE COMPUTATION
# ════════════════════════════════════════════════════════════════════════════
def compute_opening_range(api, underlying: dict) -> Optional[dict]:
    """Compute (OR_high, OR_low, OR_avg_vol) for the first 15 minutes.

    Fetches candles from today's 09:15 IST regardless of current time, so
    the engine can recover its OR even if it boots up mid-session.

    PRICE comes from the spot index (the breakout reference plane).
    VOLUME comes from the underlying's index-futures contract (NIFTY spot
    has no traded volume — only its constituent stocks and futures do).
    Falls back to spot volume only if futures-resolution is unavailable.
    """
    today = now_ist().date()
    start = datetime.combine(today, SESSION_OPEN, tzinfo=config.IST)
    end = datetime.combine(today, OR_END, tzinfo=config.IST) + timedelta(minutes=2)
    df = _fetch_candles(
        api,
        token=underlying["token"],
        exchange=underlying["exchange"],
        name=underlying["name"],
        start=start,
        end=end,
    )
    if df.empty:
        log.warning("[%s] OR candles empty", underlying["name"])
        return None

    or_window = df[
        (df["timestamp"].dt.time >= SESSION_OPEN)
        & (df["timestamp"].dt.time < OR_END)
    ]
    if len(or_window) < 5:
        log.warning("[%s] OR window has only %d candles — skipping today",
                    underlying["name"], len(or_window))
        return None

    # Volume comes from futures contract (spot indices have no real volume)
    fut_avg_vol = 0.0
    vol_source = "spot"
    if underlying.get("futures_token"):
        fut_df = _fetch_candles(
            api,
            token=underlying["futures_token"],
            exchange=underlying.get("futures_exchange", "NFO"),
            name=underlying["name"] + "_FUT",
            start=start,
            end=end,
        )
        if not fut_df.empty:
            fut_window = fut_df[
                (fut_df["timestamp"].dt.time >= SESSION_OPEN)
                & (fut_df["timestamp"].dt.time < OR_END)
            ]
            if len(fut_window) >= 5:
                fut_avg_vol = float(fut_window["volume"].mean())
                vol_source = "futures"
            else:
                log.warning(
                    "[%s] futures OR window only %d candles — falling back to spot vol",
                    underlying["name"], len(fut_window),
                )
    if vol_source == "spot":
        fut_avg_vol = float(or_window["volume"].mean())

    return {
        "high": float(or_window["high"].max()),
        "low":  float(or_window["low"].min()),
        "avg_vol": fut_avg_vol,
        "vol_source": vol_source,
        "candles": len(or_window),
    }


# ════════════════════════════════════════════════════════════════════════════
#  ATM STRIKE RESOLUTION
# ════════════════════════════════════════════════════════════════════════════
def nearest_atm_strike(spot: float, step: int) -> int:
    return int(round(spot / step) * step)


def resolve_atm_option(
    underlying: dict, spot: float, opt_type: str, expiry: str
) -> Optional[dict]:
    """Return option contract dict ready for ordering."""
    strike = nearest_atm_strike(spot, underlying["strike_step"])
    try:
        sym, token, lot_size = instrument_master.resolve_option(
            underlying["name"], strike=strike, opt_type=opt_type, expiry=expiry
        )
    except (LookupError, ValueError) as exc:
        log.error(
            "[%s] ATM %s %d %s lookup failed: %s",
            underlying["name"], opt_type, strike, expiry, exc,
        )
        return None
    return {
        "trading_symbol": sym,
        "token": token,
        "exchange": "NFO",
        "strike": strike,
        "opt_type": opt_type,
        "expiry": expiry,
        "lot_size": lot_size,
    }


# ════════════════════════════════════════════════════════════════════════════
#  PAPER-MODE SHIM
# ════════════════════════════════════════════════════════════════════════════
# The simulator now lives in execution.py so that macro and smallcap share the
# exact same paper-order implementation instead of each re-inventing one.
PaperBroker = execution.PaperBroker


# ════════════════════════════════════════════════════════════════════════════
#  ATM-FIRST DELTA-PRIORITISED STRIKE SELECTOR
# ════════════════════════════════════════════════════════════════════════════
def select_strike(
    underlying: dict,
    spot: float,
    opt_type: str,
    expiry: str,
    silo_remaining: float,
    api,
    *,
    max_lots: int = MAX_LOTS,
    otm_fallback_steps: int = OTM_FALLBACK_STEPS,
) -> Optional[dict]:
    """Pick the highest-delta contract that the silo can afford and decide
    how many lots (1 or 2) to buy.

    Algorithm (per the institutional F&O upgrade):

        1.  Resolve ATM contract; fetch its premium.
        2.  Compute ``max_lots_affordable = floor(silo_remaining /
            (premium × lot_size))``, then cap at ``max_lots`` (default 2).
        3.  If ATM affordable for ≥1 lot → return ATM with the chosen lot
            count.  Delta is preserved; lot size scales with available capital.
        4.  If ATM is unaffordable (cost-per-lot exceeds the silo), step
            exactly 1 strike OTM and re-evaluate.  Then 2 strikes OTM.
        5.  After ``otm_fallback_steps`` exhausted with still no affordable
            contract → return ``None``.  Caller treats this as a deterministic
            abort and engages the 15-min cooldown.

    Returns a dict carrying the selected contract metadata plus the chosen
    ``lots`` and total ``qty`` (lots × lot_size).
    """
    step = int(underlying.get("strike_step") or 50)
    atm = nearest_atm_strike(spot, step)
    direction = -1 if opt_type == "PE" else +1

    log.info(
        "[%s] Strike selector — spot=%.2f ATM=%d step=%d "
        "silo_remaining=₹%.0f max_lots=%d otm_fallback=%d",
        underlying["name"], spot, atm, step,
        silo_remaining, max_lots, otm_fallback_steps,
    )

    # i=0 is ATM, i>=1 are sequential OTM steps (away-from-spot).
    for i in range(otm_fallback_steps + 1):
        strike = atm + (i * direction * step)
        if strike <= 0:
            break
        label = "ATM" if i == 0 else f"{i} step OTM"

        try:
            sym, token, lot_size = instrument_master.resolve_option(
                underlying["name"], strike=strike,
                opt_type=opt_type, expiry=expiry,
            )
        except (LookupError, ValueError) as exc:
            log.warning(
                "[%s] %s strike=%d %s: not in chain (%s) — skip",
                underlying["name"], label, strike, opt_type, exc,
            )
            continue
        if lot_size <= 0:
            continue

        premium = broker.fetch_ltp(
            api, symbol=sym, token=token, exchange="NFO"
        )
        if not premium or premium <= 0:
            log.warning(
                "[%s] %s strike=%d %s: LTP unavailable — skip",
                underlying["name"], label, strike, opt_type,
            )
            continue

        cost_per_lot = premium * lot_size
        affordable_lots = int(silo_remaining // cost_per_lot)
        chosen_lots = min(affordable_lots, max_lots)

        if chosen_lots >= 1:
            qty = chosen_lots * lot_size
            cost = chosen_lots * cost_per_lot
            log.info(
                "[%s] ✓ Selected %s strike=%d %s premium=₹%.2f "
                "cost/lot=₹%.0f → buying %d lot(s) qty=%d cost=₹%.0f "
                "(silo=₹%.0f, lot_cap=%d)",
                underlying["name"], label, strike, opt_type,
                premium, cost_per_lot, chosen_lots, qty, cost,
                silo_remaining, max_lots,
            )
            return {
                "trading_symbol": sym,
                "token":          token,
                "exchange":       "NFO",
                "strike":         strike,
                "opt_type":       opt_type,
                "expiry":         expiry,
                "lot_size":       lot_size,
                "lots":           chosen_lots,
                "qty":            qty,
                "premium":        premium,
                "otm_steps":      i,
            }

        log.info(
            "[%s] %s strike=%d %s premium=₹%.2f cost/lot=₹%.0f > silo ₹%.0f "
            "— stepping further OTM",
            underlying["name"], label, strike, opt_type,
            premium, cost_per_lot, silo_remaining,
        )

    log.error(
        "[%s] No affordable %s strike at ATM or within %d OTM step(s) "
        "(silo_remaining=₹%.0f) — abort",
        underlying["name"], opt_type, otm_fallback_steps, silo_remaining,
    )
    return None


# ════════════════════════════════════════════════════════════════════════════
#  ENTRY EXECUTION
# ════════════════════════════════════════════════════════════════════════════
def open_position(
    api, paper: PaperBroker | None, underlying: dict, side: str,
    spot_at_breakout: float, expiry: str, db: BotDB,
) -> tuple[Optional[dict], bool]:
    """Hunt OTM strike within budget, place 1 lot at MARKET, place SL.

    Returns
    -------
    tuple[Optional[dict], bool]
        ``(position_dict, cooldown_eligible)``.

        ``position_dict`` is None on any failure.

        ``cooldown_eligible`` is True iff the failure was deterministic
        (capital exhausted, no affordable strikes, no chain at all) so the
        caller should set a cooldown to suppress immediate re-evaluation.
        Transient failures (rate limit, missing LTP at ORDER time) return
        False so the next minute can retry naturally.
    """
    opt_type = "CE" if side == "LONG" else "PE"

    # Compute silo headroom BEFORE strike selection so the hunter knows
    # the exact budget it has to work within.
    silo_used_today = sum(
        p["entry_price"] * p["quantity"] for p in db.open_positions()
    )
    silo_remaining = config.OPTIONS_MAX_CAPITAL - silo_used_today
    if silo_remaining <= 0:
        log.error(
            "[%s] options silo fully deployed (used=₹%.0f cap=₹%.0f) — abort",
            underlying["name"], silo_used_today, config.OPTIONS_MAX_CAPITAL,
        )
        return None, True   # cooldown: can't trade until something exits

    # Delta-first ATM strike selection with dynamic lot sizing
    opt = select_strike(
        underlying, spot_at_breakout, opt_type, expiry, silo_remaining, api,
    )
    if opt is None:
        # ATM and OTM fallbacks all unaffordable — deterministic, cooldown.
        return None, True

    qty = opt["qty"]
    lots = opt["lots"]
    premium = opt["premium"]
    cost = premium * qty
    log.info(
        "[%s] BREAKOUT %s — buying %s strike=%d lots=%d qty=%d "
        "premium=₹%.2f cost=₹%.0f",
        underlying["name"], side, opt_type, opt["strike"],
        lots, qty, premium, cost,
    )

    # ----- ORDER (paper or live) ----------------------------------------
    if paper is not None:
        order_id, fill_px = paper.buy_market(opt["trading_symbol"], premium, qty)
    else:
        # ─── PRE-ENTRY BROKER-TRUTH GATE ────────────────────────────────
        # Refuse to BUY a symbol the broker says we already hold.  Catches
        # the "DB-shows-flat-but-broker-holds-position" direction that
        # boot_reconcile may have missed if it raced against the bot
        # crashing immediately after the previous BUY filled.
        if not assert_db_matches_broker(
            api, db, symbol=opt["trading_symbol"], expected_qty=0
        ):
            log.critical(
                "[%s] BUY ABORTED — broker-truth gate refused (HALT now latched).",
                underlying["name"],
            )
            return None, True  # deterministic refusal — cooldown the underlying

        result = broker.place_market(
            api,
            symbol=opt["trading_symbol"],
            token=opt["token"],
            exchange="NFO",
            side="BUY",
            quantity=qty,
            product_type="CARRYFORWARD",
            wait_for_fill_s=15.0,
        )
        if not result:
            log.critical("[%s] LIVE BUY FAILED", underlying["name"])
            return None, False     # transient — let next minute retry
        order_id, fill_px = result
        fill_px = fill_px or premium

    # Initial SL: -10% from fill.  This is also the broker-side SL order
    # in live mode — a safety net in case the bot crashes mid-position.
    # The trailing SL (BE @ +20%, then peak×0.90) lives ENTIRELY in the
    # bot's monitor loop and uses MARKET sells to exit.
    sl_price = round(fill_px * (1 - SL_PCT), 2)

    if paper is not None:
        sl_oid = paper.stoploss(opt["trading_symbol"], sl_price, qty)
    else:
        sl_oid = broker.place_stoploss_market(
            api,
            symbol=opt["trading_symbol"],
            token=opt["token"],
            exchange="NFO",
            side="SELL",
            quantity=qty,
            trigger_price=sl_price,
            product_type="CARRYFORWARD",
        )

    db.add_position(
        symbol=opt["trading_symbol"],
        token=opt["token"],
        exchange="NFO",
        product_type="CARRYFORWARD",
        quantity=qty,
        entry_price=fill_px,
        stop_loss=sl_price,
        order_id=str(order_id),
        meta={
            "underlying":       underlying["name"],
            "side":             side,
            "opt_type":         opt_type,
            "strike":           opt["strike"],
            "expiry":           expiry,
            "spot_at_breakout": spot_at_breakout,
            "otm_steps":        opt["otm_steps"],
            "lot_size":         opt["lot_size"],
            "lots":             lots,           # original lots bought
            "lots_sold":        0,              # V3: cumulative scale-outs
            "lots_remaining":   lots,           # V3: open lots after partials
            "original_qty":     qty,            # V3: original total qty
            "htf_bias_at_entry": DAILY_BIAS,    # V3: for post-trade audit
            "sl_order_id":      sl_oid,
            "paper":            paper is not None,
            # Trailing-stop state (mutated by monitor_positions every minute)
            "peak_premium":     fill_px,
            "trail_armed":      False,
            "trail_trigger_pct":  TRAIL_TRIGGER_PCT,
            "trail_distance_pct": TRAIL_DISTANCE_PCT,
        },
    )
    db.record_trade(
        side="BUY",
        symbol=opt["trading_symbol"],
        quantity=qty,
        price=fill_px,
        order_type="MARKET",
        order_id=str(order_id),
        meta={
            "breakout_side": side,
            "underlying":    underlying["name"],
            "lots":          lots,
        },
    )
    log.info(
        "[%s] ✅ OPENED %s strike=%d @ ₹%.2f lots=%d qty=%d sl=₹%.2f "
        "(BE-arm @ ₹%.2f, %d steps OTM)",
        underlying["name"], opt_type, opt["strike"],
        fill_px, lots, qty, sl_price,
        fill_px * (1 + TRAIL_TRIGGER_PCT), opt["otm_steps"],
    )
    return {
        "symbol":          opt["trading_symbol"],
        "token":           opt["token"],
        "qty":             qty,
        "lots":            lots,
        "entry_price":     fill_px,
        "sl_price":        sl_price,
        "underlying_name": underlying["name"],
    }, False


# ════════════════════════════════════════════════════════════════════════════
#  IN-PROCESS POSITION MONITOR  (Free-Ride Trailing Stop)
# ════════════════════════════════════════════════════════════════════════════
def _execute_partial_sell(
    api, paper: PaperBroker | None, pos: dict, qty_to_sell: int, ltp_hint: float,
) -> Optional[tuple[str, float]]:
    """Place a MARKET SELL for ``qty_to_sell`` of an open position.

    Returns ``(order_id, fill_price)`` on success, ``None`` on failure
    (paper mode never fails; live mode may return None on broker rejection).

    Live mode also enforces a SELL-fund-block cooldown: if a prior attempt
    was rejected by Angel for "Insufficient Funds" (a structural failure
    that will not self-heal mid-session), the symbol is frozen from new
    SELL submissions for :data:`PARTIAL_SELL_FUND_COOLDOWN_MIN` minutes so
    the monitor loop stops burning the API rate-limit budget on guaranteed
    rejections.  See class docstring at top-of-file for the why.
    """
    symbol = pos["symbol"]

    if paper is not None:
        oid, fill_px = paper.sell_market(symbol, ltp_hint, qty_to_sell)
        return oid, fill_px

    # Live mode — gate on the fund-block cooldown BEFORE hitting the broker.
    cooldown_left = _fund_block_remaining_min(symbol)
    if cooldown_left > 0:
        log.warning(
            "[%s] SELL %d×%s SUPPRESSED — fund-block cooldown active "
            "(%d min remaining; clear by funding account ≥ SPAN+ELM)",
            symbol, qty_to_sell, symbol, cooldown_left,
        )
        return None

    result = broker.place_market(
        api,
        symbol=symbol,
        token=pos["token"],
        exchange="NFO",
        side="SELL",
        quantity=qty_to_sell,
        product_type="CARRYFORWARD",
        wait_for_fill_s=15.0,
    )

    if not result:
        # Inspect the broker's last reject text to classify the failure.
        # An Angel-One margin / insufficient-funds message means the
        # pre-trade SPAN+ELM gate blocked us — there is no point retrying
        # until the operator adds working capital.  Engage the cooldown
        # and emit the [MARGIN GUARDRAIL] line so EngineerOps tooling and
        # webhook alerts can detect the structural failure mode.
        reject_text = broker.get_last_reject(symbol, "SELL") or ""
        if is_margin_reject(reject_text):
            _SELL_FUND_BLOCK[symbol] = now_ist()
            log.critical(
                "[MARGIN GUARDRAIL] Broker API evaluating SELL as naked "
                "short. Insufficient funds. Aborting retry storm to "
                "protect rate limits."
            )
            log.critical(
                "[MARGIN GUARDRAIL] context: symbol=%s qty=%d cooldown=%dmin "
                "reject_text=%r",
                symbol, qty_to_sell,
                PARTIAL_SELL_FUND_COOLDOWN_MIN, reject_text,
            )
        return None

    # Success → drop any prior fund-block on this symbol.
    _SELL_FUND_BLOCK.pop(symbol, None)
    oid, fill_px = result
    return str(oid), (fill_px or ltp_hint)


def monitor_positions(
    api, paper: PaperBroker | None, db: BotDB, today_str: str,
) -> tuple[float, bool, list[tuple[str, str]]]:
    """Walk every open position; advance trailing stops; close on breach.

    V3 SCALED RUNNER EXIT
    =====================

    Each open position carries a meta blob with at minimum::

        {"peak_premium": float,
         "trail_armed":  bool,
         "lot_size":     int,        # contract lot size (e.g. 65 for NIFTY)
         "lots":         int,        # original lots bought
         "lots_sold":    int,        # cumulative lots scaled-out (default 0)
         "lots_remaining": int,      # current open lots (default = lots)
         "underlying":   "NIFTY" | "BANKNIFTY" | ...}

    Phase A  (initial risk)
        ``trail_armed=False`` → SL stays at the original entry × (1 − SL_PCT).

    Phase B  (scale-out + BE-arm)
        On the FIRST tick where ``ltp ≥ entry × (1 + TRAIL_TRIGGER_PCT)``:

            * If ``lots_remaining ≥ 2``: sell ``max(1, lots_remaining // 2)``
              lots at MARKET, book the partial P&L, move the SL for the
              remainder to break-even, set ``trail_armed=True``.  The partial
              sell is persisted atomically via :meth:`BotDB.partial_exit` so
              a crash between the broker fill and the DB write can never
              double-count the lots.
            * If ``lots_remaining == 1``: graceful degradation — no scale-out
              is possible, so we just hop SL to break-even (V2 behaviour).

    Phase C  (trail the runner)
        Once armed, ``SL = max(SL, peak × (1 − TRAIL_DISTANCE_PCT))`` capped
        never to fall below break-even.  As premium rallies SL ratchets up;
        as premium reverses ``TRAIL_DISTANCE_PCT`` from peak the runner exits.

    Always:
        * ``peak_premium`` is updated and persisted on every tick.
        * If ``ltp ≤ SL`` the position closes at MARKET (full remaining qty).
        * The daily-loss kill switch is evaluated against
          ``realised + unrealised`` at the end of the pass.

    Returns ``(unreal, kill_switch_now, closed_on_sl)`` where
    ``closed_on_sl`` is a list of ``(underlying_name, reason)`` tuples for
    every position closed by ``hard_sl`` or ``trailing_sl`` in this pass
    (the caller uses these to arm the post-SL whipsaw firewall).  Scale-out
    partial exits are NOT included in ``closed_on_sl`` — the position is
    still alive on a runner.
    """
    import json

    open_pos = db.open_positions()
    unreal = 0.0
    closed_on_sl: list[tuple[str, str]] = []

    # ─── GHOST DETECTION ──────────────────────────────────────────────────
    # Pull the broker's position book once per monitor pass so we can
    # short-circuit any DB row the broker no longer agrees with.  A "ghost"
    # row is one where DB says we hold qty>0 but broker shows netqty=0 —
    # the typical aftermath of a broker-side SL firing between cron ticks.
    # Without this check, every monitor pass would attempt a MARKET close,
    # hit the margin gate, and burn API budget on a position that doesn't
    # exist anymore.  We also halt-and-skip on partial mismatches.
    broker_book: dict[str, dict] = {}
    if api is not None and paper is None and open_pos:
        broker_book = broker.fetch_position_book(api)

    for pos in open_pos:
        # Cross-check this row against the broker before doing ANYTHING.
        if broker_book:
            bpos = broker_book.get(pos["symbol"])
            broker_qty = (bpos or {}).get("netqty", 0)
            db_qty = int(pos["quantity"])
            if broker_qty == 0 and db_qty > 0:
                log.critical(
                    "[%s] GHOST DETECTED — DB qty=%d but broker netqty=0. "
                    "Skipping monitor pass; reconcile_after_manual_exit.py "
                    "will sync this on its next run (or run it now).",
                    pos["symbol"], db_qty,
                )
                continue
            if broker_qty != db_qty and broker_qty != 0:
                log.critical(
                    "[%s] PARTIAL MISMATCH — DB qty=%d broker netqty=%d. "
                    "HALT latching; refusing to mutate this position.",
                    pos["symbol"], db_qty, broker_qty,
                )
                db.halt_trading(
                    f"monitor_positions: [{pos['symbol']}] DB qty={db_qty} "
                    f"≠ broker netqty={broker_qty}"
                )
                continue

        ltp = broker.fetch_ltp(
            api, symbol=pos["symbol"], token=pos["token"], exchange="NFO"
        )
        if not ltp or ltp <= 0:
            continue

        entry = float(pos["entry_price"])
        qty = int(pos["quantity"])           # current REMAINING qty
        current_sl = float(pos["stop_loss"])

        meta_raw = pos.get("meta_json") or "{}"
        try:
            meta = json.loads(meta_raw)
        except Exception:
            meta = {}

        peak = float(meta.get("peak_premium") or entry)
        trail_armed = bool(meta.get("trail_armed") or False)

        # ── Lot bookkeeping (with safe defaults for pre-V3 positions) ──
        lot_size = int(meta.get("lot_size") or 0)
        if lot_size <= 0:
            # Legacy position w/o lot_size: derive from qty and assume 1 lot
            lot_size = qty
        original_lots = int(meta.get("lots") or max(qty // lot_size, 1))
        lots_sold = int(meta.get("lots_sold") or 0)
        # lots_remaining defaults to qty/lot_size for forward compat
        lots_remaining = int(meta.get("lots_remaining") or max(qty // lot_size, 1))

        unreal += (ltp - entry) * qty

        # ─── 1. high-water mark ────────────────────────────────────────
        new_peak = max(peak, ltp)
        peak_changed = new_peak > peak

        new_sl = current_sl
        sl_changed = False
        meta_changed = peak_changed
        scaled_out_now = False

        # ─── 2. Phase B: scale-out + BE-arm (first time only) ──────────
        # Single-lot policy:
        #   When OPTIONS_MAX_LOTS=1 (post-Jun-2026 margin-trap migration)
        #   we ALWAYS take the no-scale-out branch.  This is the runner
        #   model — the lot is held intact, SL hops to break-even on the
        #   first +TRAIL_TRIGGER_PCT touch, and the trail ratchets upward
        #   from there.  Bypassing the scale-out branch makes us immune
        #   to Angel's pre-trade SPAN+ELM margin gate on partial SELLs.
        if not trail_armed and ltp >= entry * (1 + TRAIL_TRIGGER_PCT):
            if lots_remaining >= 2 and MAX_LOTS >= 2:
                # Sell half (rounded down, minimum 1 lot)
                lots_to_sell = max(1, lots_remaining // 2)
                qty_to_sell = lots_to_sell * lot_size
                qty_remaining_after = qty - qty_to_sell
                lots_remaining_after = lots_remaining - lots_to_sell

                sell_result = _execute_partial_sell(
                    api, paper, pos, qty_to_sell, ltp,
                )
                if sell_result is None:
                    # Graceful-failure contract (margin guardrail compatible):
                    #   • lots_remaining / lots_sold / trail_armed left UNCHANGED
                    #   • DB state (db.partial_exit) NOT called
                    #   • monitor_positions() continues for other positions
                    # If the underlying failure was a margin/funds reject,
                    # _execute_partial_sell has already engaged the per-symbol
                    # SELL cooldown and emitted the [MARGIN GUARDRAIL] line;
                    # subsequent monitor passes will short-circuit this branch
                    # at the broker boundary until the operator funds the
                    # account or the cooldown elapses.  No retry storm.
                    log.error(
                        "[%s] Phase-B partial SELL FAILED (qty=%d) — "
                        "state unchanged; next monitor pass will re-check",
                        pos["symbol"], qty_to_sell,
                    )
                else:
                    oid, fill_px = sell_result
                    # Net of the exit leg's real charges. The entry charge was
                    # already booked when the position opened, so only the
                    # closing leg is deducted here.
                    gross_partial = (fill_px - entry) * qty_to_sell
                    partial_charges = costs.exit_charges(
                        segment="OPTIONS", price=fill_px, qty=qty_to_sell,
                    )
                    partial_pnl = round(gross_partial - partial_charges, 2)
                    # NOTE: P&L is booked atomically inside partial_exit() below
                    # (via book_pnl_date), never as a separate transaction.

                    # New SL for remainder: hop to break-even
                    new_sl = round(entry, 2)
                    trail_armed = True

                    # Mutate meta for the persisted snapshot
                    meta["lots_sold"] = lots_sold + lots_to_sell
                    meta["lots_remaining"] = lots_remaining_after
                    meta["trail_armed"] = True
                    meta["peak_premium"] = new_peak
                    if "lot_size" not in meta:
                        meta["lot_size"] = lot_size
                    if "lots" not in meta:
                        meta["lots"] = original_lots

                    db.partial_exit(
                        symbol=pos["symbol"],
                        qty_sold=qty_to_sell,
                        qty_remaining=qty_remaining_after,
                        price=fill_px,
                        order_type="MARKET",
                        order_id=oid,
                        pnl=partial_pnl,
                        trade_meta={
                            "reason": "scale_out_phase_b",
                            "lots_sold_this_tx": lots_to_sell,
                            "lots_remaining": lots_remaining_after,
                            "underlying": meta.get("underlying"),
                        },
                        position_meta=meta,
                        new_stop_loss=new_sl,
                        book_pnl_date=today_str,
                    )
                    log.info(
                        "[%s] Lot %d secured at +%.0f%% (sold %d×%s @ ₹%.2f, "
                        "pnl=₹%+.2f net). Runner armed at Breakeven — "
                        "%d lot(s) / %d qty left.",
                        pos["symbol"],
                        lots_sold + lots_to_sell,
                        TRAIL_TRIGGER_PCT * 100,
                        qty_to_sell, pos["symbol"], fill_px,
                        partial_pnl,
                        lots_remaining_after, qty_remaining_after,
                    )
                    # Already booked partial_pnl as realised; remove it
                    # from the in-loop unrealised tally so monitor totals
                    # reflect only the surviving runner.
                    unreal -= (fill_px - entry) * qty_to_sell

                    # Refresh local view of the position for the rest of
                    # this iteration (trail computation below uses these).
                    qty = qty_remaining_after
                    current_sl = new_sl
                    lots_remaining = lots_remaining_after
                    scaled_out_now = True
                    meta_changed = False   # partial_exit already persisted
                    sl_changed = False     # partial_exit already persisted
            else:
                # Single-lot position — no scale-out possible.  V2 behaviour:
                # arm trail at break-even and let the runner ride solo.
                trail_armed = True
                new_sl = max(new_sl, round(entry, 2))
                sl_changed = new_sl != current_sl
                meta_changed = True
                log.info(
                    "[%s] TRAIL ARMED (single-lot, no scale-out) — "
                    "ltp=₹%.2f ≥ entry×(1+%.0f%%)=₹%.2f → SL → BE ₹%.2f",
                    pos["symbol"], ltp, TRAIL_TRIGGER_PCT * 100,
                    entry * (1 + TRAIL_TRIGGER_PCT), new_sl,
                )

        # ─── 3. Phase C: trail the runner (or already-armed single-lot) ──
        if trail_armed and not scaled_out_now and qty > 0:
            trail_floor = round(new_peak * (1 - TRAIL_DISTANCE_PCT), 2)
            candidate = max(trail_floor, round(entry, 2))
            if candidate > new_sl:
                log.info(
                    "[%s] TRAIL ADVANCE — peak=₹%.2f×%.2f=₹%.2f, "
                    "SL %.2f → %.2f",
                    pos["symbol"], new_peak, 1 - TRAIL_DISTANCE_PCT,
                    trail_floor, new_sl, candidate,
                )
                new_sl = candidate
                sl_changed = True

        # ─── 4. persist any state changes not already saved by partial_exit ──
        if meta_changed and not scaled_out_now:
            meta["peak_premium"] = new_peak
            meta["trail_armed"] = trail_armed
            if "lot_size" not in meta:
                meta["lot_size"] = lot_size
            if "lots" not in meta:
                meta["lots"] = original_lots
            if "lots_sold" not in meta:
                meta["lots_sold"] = lots_sold
            if "lots_remaining" not in meta:
                meta["lots_remaining"] = lots_remaining
            db.update_position_meta(pos["symbol"], meta)
        if sl_changed and not scaled_out_now:
            db.update_stop_loss(pos["symbol"], new_sl)
            current_sl = new_sl

            # ─── EXCHANGE SYNC ───────────────────────────────────────────
            # Mirror every trailing-SL ratchet (Phase-B BE-arm AND
            # Phase-C peak-trail) to the resting STOPLOSS_MARKET order at
            # the exchange.  Without this, the trail lives only in our
            # DB; when LTP touches it the engine fires a fresh MARKET
            # SELL which Angel's pre-trade margin gate rejects.  The
            # resting SL is exit-side and bypasses that gate cleanly.
            # ──────────────────────────────────────────────────────────────
            if paper is None:
                sl_oid = str(meta.get("sl_order_id") or "")
                if not sl_oid:
                    log.warning(
                        "[EXCHANGE SYNC] [%s] no sl_order_id in meta — "
                        "trail SL (₹%.2f) is unmirrored at broker. "
                        "Legacy position; engine will use bot-side close on breach.",
                        pos["symbol"], new_sl,
                    )
                else:
                    ok, detail = broker.modify_exchange_stop_loss(
                        api,
                        order_id=sl_oid,
                        new_trigger_price=new_sl,
                        quantity=qty,
                        symbol=pos["symbol"],
                        token=pos["token"],
                        exchange="NFO",
                        product_type="CARRYFORWARD",
                    )
                    if ok:
                        log.info(
                            "[EXCHANGE SYNC] [%s] surgically moved resting SL "
                            "order %s to ₹%.2f at the broker (qty=%d).",
                            pos["symbol"], sl_oid, new_sl, qty,
                        )
                    elif broker.is_sl_no_longer_pending(detail):
                        # Resting order has lapsed (triggered / cancelled /
                        # unknown).  Cancel any residual + re-arm a fresh SL
                        # clamped just below LTP so it fires next tick.
                        log.critical(
                            "[EXCHANGE SYNC] [%s] resting SL %s NO LONGER "
                            "PENDING (%s) — emergency resync engaging.",
                            pos["symbol"], sl_oid, detail,
                        )
                        new_oid = broker.emergency_sl_resync(
                            api,
                            old_order_id=sl_oid,
                            symbol=pos["symbol"],
                            token=pos["token"],
                            exchange="NFO",
                            side="SELL",
                            quantity=qty,
                            desired_trigger=new_sl,
                            current_ltp=ltp,
                            product_type="CARRYFORWARD",
                        )
                        if new_oid:
                            meta["sl_order_id"] = new_oid
                            db.update_position_meta(pos["symbol"], meta)
                        else:
                            log.critical(
                                "[EXCHANGE SYNC] [%s] emergency resync FAILED "
                                "— position is UNPROTECTED at the broker. "
                                "Operator: manual flatten via Angel app NOW.",
                                pos["symbol"],
                            )
                    else:
                        # Generic modify failure (e.g. transient rate-limit,
                        # trigger-out-of-range).  Old SL is still resting at
                        # the previous trigger — capital is still protected,
                        # we just couldn't ratchet this round.  Will retry
                        # on the next ratchet event.
                        log.warning(
                            "[EXCHANGE SYNC] [%s] modify failed (%s) — "
                            "resting SL stays at previous trigger; will "
                            "retry on next ratchet.",
                            pos["symbol"], detail,
                        )

        # ─── 5. SL breach → close FULL REMAINING qty at MARKET ─────────
        if qty > 0 and ltp <= current_sl:
            reason = "trailing_sl" if trail_armed else "hard_sl"
            log.warning(
                "[%s] SL HIT — ltp=₹%.2f ≤ sl=₹%.2f (entry=₹%.2f peak=₹%.2f "
                "armed=%s lots_rem=%d) — selling MARKET (reason=%s)",
                pos["symbol"], ltp, current_sl, entry, new_peak,
                trail_armed, lots_remaining, reason,
            )
            # Refresh pos dict with current qty before close_position uses it
            pos["quantity"] = qty
            close_position(api, paper, pos, db, reason=reason)
            unreal -= (ltp - entry) * qty
            underlying_name = str(meta.get("underlying") or "")
            if underlying_name:
                closed_on_sl.append((underlying_name, reason))

    # ─── 6. daily-loss kill switch ────────────────────────────────────
    realised, ks = db.get_daily_pnl(today_str)
    total = realised + unreal
    if not ks and total <= config.OPTIONS_DAILY_LOSS:
        log.critical(
            "★ KILL SWITCH ★ realised=%+.2f unreal=%+.2f total=%+.2f "
            "≤ limit %+.2f",
            realised, unreal, total, config.OPTIONS_DAILY_LOSS,
        )
        db.trigger_kill_switch(today_str)
        return unreal, True, closed_on_sl
    return unreal, False, closed_on_sl


def close_position(
    api, paper: PaperBroker | None, pos: dict, db: BotDB, reason: str,
) -> None:
    """SELL MARKET to close a position; record trade; remove from DB.

    Live mode honours the same fund-block cooldown used by partial-sells
    (:data:`_SELL_FUND_BLOCK`).  A close that would be guaranteed-rejected
    by Angel's margin gate is suppressed early with a CRITICAL log so the
    operator sees one loud message per minute instead of a retry storm.
    """
    if paper is not None:
        ltp = broker.fetch_ltp(
            api, symbol=pos["symbol"], token=pos["token"], exchange="NFO"
        ) or pos["entry_price"]
        oid, fill_px = paper.sell_market(pos["symbol"], ltp, pos["quantity"])
    else:
        # Live mode — gate on fund-block cooldown before hitting the broker.
        cooldown_left = _fund_block_remaining_min(pos["symbol"])
        if cooldown_left > 0:
            log.critical(
                "[%s] CLOSE (%s) SUPPRESSED — fund-block cooldown active "
                "(%d min remaining). Position remains open. "
                "Add funds ≥ SPAN+ELM and run flatten_all.py manually.",
                pos["symbol"], reason, cooldown_left,
            )
            return

        result = broker.place_market(
            api,
            symbol=pos["symbol"],
            token=pos["token"],
            exchange="NFO",
            side="SELL",
            quantity=pos["quantity"],
            product_type="CARRYFORWARD",
            wait_for_fill_s=15.0,
        )
        if not result:
            reject_text = broker.get_last_reject(pos["symbol"], "SELL") or ""
            if is_margin_reject(reject_text):
                _SELL_FUND_BLOCK[pos["symbol"]] = now_ist()
                log.critical(
                    "[MARGIN GUARDRAIL] Broker API evaluating SELL as naked "
                    "short. Insufficient funds. Aborting retry storm to "
                    "protect rate limits."
                )
                log.critical(
                    "[MARGIN GUARDRAIL] context: symbol=%s reason=%s qty=%d "
                    "cooldown=%dmin reject_text=%r — POSITION REMAINS OPEN, "
                    "manual flatten required after funding account",
                    pos["symbol"], reason, pos["quantity"],
                    PARTIAL_SELL_FUND_COOLDOWN_MIN, reject_text,
                )
            else:
                log.critical("[%s] CLOSE FAILED — manual intervention required",
                             pos["symbol"])
            return
        _SELL_FUND_BLOCK.pop(pos["symbol"], None)
        oid, fill_px = result

    # Net P&L after the real round-trip charges (brokerage, STT, exchange,
    # GST, stamp duty). Paper and live use the same model so a paper record
    # is directly comparable to a broker statement.
    entry_px = float(pos["entry_price"])
    qty = int(pos["quantity"])
    rt = costs.round_trip(
        segment="OPTIONS", entry=entry_px, exit_=fill_px, qty=qty,
    )
    pnl = rt.net_pnl
    today_str = now_ist().strftime("%Y-%m-%d")
    # P&L is booked INSIDE close_position's transaction — see its docstring.
    db.close_position(
        side="SELL",
        symbol=pos["symbol"],
        quantity=qty,
        price=fill_px,
        order_type="MARKET",
        order_id=str(oid),
        pnl=pnl,
        meta={
            "reason": reason,
            "gross_pnl": rt.gross_pnl,
            "charges": rt.total_charges,
        },
        book_pnl_date=today_str,
    )
    log.info(
        "[%s] CLOSED qty=%d @ ₹%.2f gross=₹%+.2f charges=₹%.2f "
        "net=₹%+.2f (reason=%s)",
        pos["symbol"], qty, fill_px, rt.gross_pnl, rt.total_charges,
        pnl, reason,
    )


def square_off_all(
    api, paper: PaperBroker | None, db: BotDB, reason: str = "hard_exit"
) -> None:
    """Force-close every open position."""
    for pos in db.open_positions():
        close_position(api, paper, pos, db, reason)


# ════════════════════════════════════════════════════════════════════════════
#  LOGGING SETUP
# ════════════════════════════════════════════════════════════════════════════
def setup_logging(level: str) -> None:
    today = now_ist().strftime("%Y-%m-%d")
    log_path = config.LOG_DIR / f"options_{today}.log"
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
#  MAIN LOOP
# ════════════════════════════════════════════════════════════════════════════
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper", action="store_true",
                        help="Force paper mode regardless of .env")
    parser.add_argument("--force", action="store_true",
                        help="Bypass NSE trading-day guard")
    parser.add_argument("--scan-only", action="store_true",
                        help="Run one OR + 1-min scan and exit (for smoke testing)")
    args = parser.parse_args()

    setup_logging(config.LOG_LEVEL)
    log.info(config.banner())
    log.info("=" * 72)

    paper_mode = config.PAPER_TRADING or args.paper
    log.info("option_predator starting | env=%s mode=%s",
             config.APP_ENV, "PAPER" if paper_mode else "LIVE")
    log.info(execution.mode_banner())
    # Environment-level isolation: dev / preprod / prod each own a separate
    # database, so paper state can never leak into production state.
    log.info("DB: %s", config._redacted_dsn())
    log.info(
        "Silo: ₹%.0f | daily-loss kill: ₹%+.0f | hard exit: %s | "
        "SL=-%.0f%% / trail-arm=+%.0f%% (BE) / trail-dist=-%.0f%% peak | "
        "regime-gate: OR width ≥ %.2f%% | "
        "max_lots=%d | vol=%.1f×OR_avg (FUTIDX proxy) | "
        "post-SL cooldown=%d min | entry cooldown=%d min",
        config.OPTIONS_MAX_CAPITAL,
        config.OPTIONS_DAILY_LOSS,
        config.OPTIONS_HARD_EXIT_TIME.strftime("%H:%M"),
        SL_PCT * 100,
        TRAIL_TRIGGER_PCT * 100,
        TRAIL_DISTANCE_PCT * 100,
        MIN_ORB_RANGE_PCT,
        MAX_LOTS,
        BREAKOUT_VOLUME_MULT,
        int(os.getenv("OPTIONS_POST_SL_COOLDOWN_MINS", "30")),
        int(os.getenv("OPTIONS_ENTRY_COOLDOWN_MINS", "15")),
    )
    log.info(
        "V3 Alpha: HTF bias=%s (SMA-%d) | dead zone=%s–%s IST | "
        "scaled runner exit=ENABLED (sell ½ at +%.0f%%, runner trails)",
        "ENABLED" if HTF_BIAS_ENABLED else "DISABLED",
        HTF_SMA_PERIOD,
        DEAD_ZONE_START.strftime("%H:%M"),
        DEAD_ZONE_END.strftime("%H:%M"),
        TRAIL_TRIGGER_PCT * 100,
    )
    # V3.1: Universe + expiry-of-the-day awareness banner
    log.info(
        "V3.1 Universe: %s  (%d indices in scan loop)",
        " · ".join(u["name"] for u in UNDERLYINGS),
        len(UNDERLYINGS),
    )
    _today_weekday = now_ist().weekday()
    _expiring_today = WEEKLY_EXPIRY_CALENDAR.get(_today_weekday)
    if _expiring_today:
        log.info(
            "Weekly expiry today (%s): %s — expect elevated IV and breakout density",
            now_ist().strftime("%A"), _expiring_today,
        )
    else:
        log.info(
            "Weekly expiry today (%s): NONE — Mon/Tue/Wed/Thu scan with no near-zero-DTE bias",
            now_ist().strftime("%A"),
        )
    log.info("=" * 72)

    if not args.force and not nse_calendar.is_nse_trading_day():
        log.warning("Trading-day guard: SKIP (%s)",
                    nse_calendar.reason_market_closed())
        return 0

    wait_for_database()
    db = BotDB("OPTIONS")
    today_str = now_ist().strftime("%Y-%m-%d")

    realised, ks_hit = db.get_daily_pnl(today_str)
    log.info("Today's realised P&L: ₹%+.2f | kill_switch=%s", realised, ks_hit)
    if ks_hit:
        log.critical("KILL SWITCH already tripped today — refusing new entries.")
        return 0

    log.info("Authenticating to Angel One…")
    try:
        api = login()
    except Exception as exc:
        log.critical("Login failed: %s", exc)
        return 1
    log.info("Authenticated as %s", config.CLIENT_ID)

    paper_broker = PaperBroker() if paper_mode else None

    # ─── BOOT RECONCILE — make broker reality the source of truth ─────
    # Live mode only.  Paper mode has no broker book to reconcile against,
    # and its DB is a private playground that must NEVER consult Angel.
    if paper_broker is None:
        try:
            boot_reconcile(api, db)
        except Exception as exc:
            log.critical("boot_reconcile crashed: %s — HALT latched for safety", exc)
            db.halt_trading(f"boot_reconcile crashed: {exc}")
        halted, halt_reason = db.is_halted()
        if halted:
            log.critical(
                "Bot will run in HALT mode (monitor + exits only, no new entries) "
                "until operator clears 'position_halt' flag.  Reason: %r",
                halt_reason,
            )

    try:
        # ─── PHASE 0: resolve nearest weekly expiry for both underlyings ─
        expiries = {}
        for u in UNDERLYINGS:
            try:
                expiries[u["name"]] = instrument_master.nearest_weekly_expiry(u["name"])
            except LookupError as e:
                log.error("[%s] no expiry found: %s", u["name"], e)
                expiries[u["name"]] = None
        log.info("Weekly expiries: %s", expiries)

        # ─── PHASE 0b: resolve volume-proxy futures for each spot index ──
        # Spot NIFTY/BANKNIFTY return volume=0 from Angel.  The breakout
        # vol gate ( ≥ 3× OR_avg ) therefore needs the underlying futures
        # contract's 1-min volume series.  We resolve once at startup and
        # cache symbol/token on each underlying dict.
        for u in UNDERLYINGS:
            try:
                fut_sym, fut_tok, fut_lot = instrument_master.resolve_index_future(
                    u["name"]
                )
                u["futures_symbol"]   = fut_sym
                u["futures_token"]    = fut_tok
                u["futures_exchange"] = "NFO"
                log.info(
                    "[%s] Volume proxy: %s (token=%s, lot=%d)",
                    u["name"], fut_sym, fut_tok, fut_lot,
                )
            except LookupError as exc:
                log.error(
                    "[%s] FUTIDX resolution failed (%s) — breakout vol gate "
                    "will fall back to spot vol (≈0) and pass every breakout",
                    u["name"], exc,
                )
                u["futures_symbol"]   = None
                u["futures_token"]    = None
                u["futures_exchange"] = None

        # ─── PHASE 0c: HTF Daily Bias (V3 Trend Alignment) ──────────────
        # Fetch NIFTY daily candles, compute SMA(N), label BULLISH/BEARISH.
        # Failures fall back to NEUTRAL so the bot still trades (matching V2).
        global DAILY_BIAS
        if HTF_BIAS_ENABLED:
            DAILY_BIAS = compute_daily_bias(api)
            log.info(
                "HTF bias armed: %s → %s entries only this session",
                DAILY_BIAS,
                "CE (LONG)"  if DAILY_BIAS == "BULLISH"
                else "PE (SHORT)" if DAILY_BIAS == "BEARISH"
                else "both (no filter)",
            )
        else:
            DAILY_BIAS = "NEUTRAL"
            log.info("HTF bias DISABLED via .env — V2 behaviour")

        # ─── PHASE 1: wait until 09:31 IST to compute OR ────────────────
        if now_ist().time() < SCAN_START and not args.scan_only:
            log.info("Waiting until %s IST to compute opening range…",
                     SCAN_START.strftime("%H:%M"))
            sleep_until(SCAN_START)

        # ─── PHASE 2: compute opening range per underlying ──────────────
        ors: dict[str, dict] = {}
        for _u_idx, u in enumerate(UNDERLYINGS):
            # Pace successive underlyings so the burst doesn't trip Angel's
            # historical-candle rate limiter (09 Jun 2026 NIFTY drop-out).
            if _u_idx > 0 and OR_FETCH_PACING_SEC > 0:
                time.sleep(OR_FETCH_PACING_SEC)

            or_data = None
            for _attempt in range(1, OR_FETCH_RETRY + 1):
                or_data = compute_opening_range(api, u)
                if or_data:
                    break
                if _attempt < OR_FETCH_RETRY:
                    log.warning(
                        "[%s] OR fetch attempt %d/%d empty (rate-limit?) — "
                        "backing off %.1fs and retrying (OR window is historical, "
                        "safe to refetch)",
                        u["name"], _attempt, OR_FETCH_RETRY, OR_FETCH_BACKOFF_SEC,
                    )
                    time.sleep(OR_FETCH_BACKOFF_SEC)
            if not or_data:
                log.warning("[%s] OR not available after %d attempts — "
                            "skipping for today", u["name"], OR_FETCH_RETRY)
                continue

            log.info(
                "[%s] OR high=%.2f low=%.2f avg_vol=%.0f candles=%d",
                u["name"], or_data["high"], or_data["low"],
                or_data["avg_vol"], or_data["candles"],
            )

            # ── V3 defensive vol-gate floor (data-artefact protection) ─
            # 21 May 2026 incident: BANKNIFTY's OR fut_avg_vol came back as
            # 0 from a broker fetch failure inside 09:15–09:30, making the
            # 1.5× threshold trivially passable.  We now refuse to trade
            # any underlying whose futures OR avg-vol falls below the
            # configured floor for the WHOLE DAY (other underlyings keep
            # trading; this is a per-instrument refusal, not a kill switch).
            if or_data["avg_vol"] < config.OPTIONS_MIN_FUTURES_OR_VOL:
                log.warning(
                    "[VOL-GATE-FLOOR] %s OR_avg_vol=%.0f < floor=%d "
                    "(vol_src=%s) → refusing this underlying for entire day "
                    "(data artefact protection)",
                    u["name"], or_data["avg_vol"],
                    config.OPTIONS_MIN_FUTURES_OR_VOL,
                    or_data.get("vol_source", "?"),
                )
                continue   # do NOT add to `ors` ⇒ main-loop scan skips it

            # ── REGIME GATE: stand down on flat/narrow-range days ──────
            # Long-premium ORB is only +EV when real intraday volatility is
            # present.  A narrow opening range = chop day = guaranteed theta
            # bleed.  We measure OR width vs spot (midpoint of OR) and refuse
            # the underlying for the whole day if it's below threshold.
            # Backtest: this single gate flips aggregate EV from -₹205 to +₹210.
            if MIN_ORB_RANGE_PCT > 0:
                or_mid = (or_data["high"] + or_data["low"]) / 2.0
                or_range_pct = (or_data["high"] - or_data["low"]) / or_mid * 100 \
                    if or_mid else 0.0
                if or_range_pct < MIN_ORB_RANGE_PCT:
                    log.info(
                        "[REGIME-SKIP] %s OR width=%.2f%% < %.2f%% threshold "
                        "→ flat/chop day, standing down for entire session "
                        "(long-premium ORB is -EV on narrow-range days)",
                        u["name"], or_range_pct, MIN_ORB_RANGE_PCT,
                    )
                    # Idle-day evidence ledger — NEVER allowed to crash the loop.
                    try:
                        db.log_regime(
                            date_str=now_ist().date().isoformat(),
                            underlying=u["name"], or_high=or_data["high"],
                            or_low=or_data["low"],
                            or_width_pct=round(or_range_pct, 3),
                            threshold_pct=MIN_ORB_RANGE_PCT, decision="SKIP",
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.debug("regime_log SKIP write failed (non-fatal): %s", exc)
                    continue   # do NOT add to `ors`
                log.info(
                    "[REGIME-OK] %s OR width=%.2f%% ≥ %.2f%% → volatile day, "
                    "armed for breakout scan",
                    u["name"], or_range_pct, MIN_ORB_RANGE_PCT,
                )
                try:
                    db.log_regime(
                        date_str=now_ist().date().isoformat(),
                        underlying=u["name"], or_high=or_data["high"],
                        or_low=or_data["low"],
                        or_width_pct=round(or_range_pct, 3),
                        threshold_pct=MIN_ORB_RANGE_PCT, decision="ARMED",
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("regime_log ARMED write failed (non-fatal): %s", exc)

            ors[u["name"]] = or_data

        if not ors:
            log.error("No opening ranges computable — aborting day")
            return 0

        if args.scan_only:
            log.info("--scan-only flag set — skipping main loop")
            return 0

        # ─── PHASE 3: main 1-min poll loop ──────────────────────────────
        log.info("Entering main poll loop (1-min cadence)…")
        last_minute_seen: dict[str, str] = {u["name"]: "" for u in UNDERLYINGS}
        stop_for_day = False
        # Per-underlying entry cooldown.  Set after a deterministic abort
        # (no affordable strikes / silo full) to suppress 60×/min log spam
        # and quota-bombing the broker.  Default 15 min (overridable via
        # OPTIONS_ENTRY_COOLDOWN_MINS env var).
        cooldown_until: dict[str, datetime] = {}
        cooldown_minutes = int(os.getenv("OPTIONS_ENTRY_COOLDOWN_MINS", "15"))
        cooldown_logged: dict[str, bool] = {}
        # V3.1: per-underlying "currently-holding" tracker so we log a
        # one-line state-transition note when an index becomes locked-out
        # or unlocked, instead of silently swallowing the breakout-scan.
        # The value is the held option-symbol (or None if no position).
        in_trade_held: dict[str, Optional[str]] = {u["name"]: None for u in UNDERLYINGS}

        # Whipsaw firewall — post-SL cooldown.  When a position closes via
        # hard_sl OR trailing_sl, the underlying enters a 30-min freeze.
        # This prevents the bot from immediately re-entering the same play
        # while the breakout level is still being whipsawed.
        post_sl_cooldown: dict[str, datetime] = {}
        post_sl_minutes = int(os.getenv("OPTIONS_POST_SL_COOLDOWN_MINS", "30"))
        post_sl_logged: dict[str, bool] = {}

        # Dead-zone "logged once per session entry" flags
        dead_zone_entered_logged = False
        dead_zone_exited_logged = False

        while not stop_for_day:
            now = now_ist()
            # Count of broker candle-fetches issued so far in THIS poll cycle —
            # used by the scan-loop pacer to space the 2nd+ fetch and avoid
            # bursting Angel's rate limiter when multiple indices are armed.
            _scan_fetches = 0

            # Hard exit at OPTIONS_HARD_EXIT_TIME
            if now.time() >= config.OPTIONS_HARD_EXIT_TIME:
                log.info("Hard exit time (%s) reached — squaring off all",
                         config.OPTIONS_HARD_EXIT_TIME.strftime("%H:%M"))
                square_off_all(api, paper_broker, db, reason="hard_exit")
                stop_for_day = True
                break

            # Process-end safety
            if now.time() >= PROCESS_END:
                log.info("PROCESS_END (%s) reached — exiting",
                         PROCESS_END.strftime("%H:%M"))
                stop_for_day = True
                break

            # Kill switch?
            _, ks = db.get_daily_pnl(today_str)
            if ks:
                log.warning("Kill switch tripped — squaring off all then exiting")
                square_off_all(api, paper_broker, db, reason="kill_switch")
                stop_for_day = True
                break

            # ── V3 Dead Zone gate: skip ENTRY evaluation for ALL underlyings
            # inside the configured window.  monitor_positions() (below) keeps
            # running normally so trails / SL / hard exit / kill switch all
            # remain live for existing positions.
            in_dz = in_dead_zone(now.time())
            if in_dz:
                if not dead_zone_entered_logged:
                    log.info(
                        "Dead Zone active (%s–%s IST) — skipping new entries. "
                        "monitor_positions remains live.",
                        DEAD_ZONE_START.strftime("%H:%M"),
                        DEAD_ZONE_END.strftime("%H:%M"),
                    )
                    dead_zone_entered_logged = True
                    dead_zone_exited_logged = False
            else:
                if dead_zone_entered_logged and not dead_zone_exited_logged:
                    log.info(
                        "Dead Zone exited at %s IST — resuming entry evaluation",
                        now.strftime("%H:%M:%S"),
                    )
                    dead_zone_exited_logged = True
                    dead_zone_entered_logged = False

            # Per-underlying scan (entries only — monitor runs unconditionally)
            for u in UNDERLYINGS:
                if in_dz:
                    break       # skip the entry-scan loop entirely
                name = u["name"]
                if name not in ors:
                    continue
                if expiries.get(name) is None:
                    continue

                # Post-SL cooldown gate (whipsaw firewall, 30 min)
                psl = post_sl_cooldown.get(name)
                if psl is not None and now < psl:
                    if not post_sl_logged.get(name):
                        secs_left = int((psl - now).total_seconds())
                        log.info(
                            "[%s] Post-SL cooldown active for %d:%02d more — "
                            "skipping breakout evaluation",
                            name, secs_left // 60, secs_left % 60,
                        )
                        post_sl_logged[name] = True
                    continue
                if psl is not None and now >= psl:
                    log.info("[%s] Post-SL cooldown expired — re-arming", name)
                    post_sl_cooldown.pop(name, None)
                    post_sl_logged.pop(name, None)

                # Entry cooldown gate (after a deterministic abort)
                cd = cooldown_until.get(name)
                if cd is not None and now < cd:
                    if not cooldown_logged.get(name):
                        secs_left = int((cd - now).total_seconds())
                        log.info(
                            "[%s] Entry cooldown active for %d:%02d more — "
                            "skipping breakout evaluation",
                            name, secs_left // 60, secs_left % 60,
                        )
                        cooldown_logged[name] = True
                    continue
                if cd is not None and now >= cd:
                    log.info("[%s] Entry cooldown expired — re-arming", name)
                    cooldown_until.pop(name, None)
                    cooldown_logged.pop(name, None)

                # ── V3.1 CRITICAL FIX (Sun 24 May 2026, pre-Monday audit) ──
                # The legacy check `name in str(meta_json)` was a SUBSTRING
                # match against the raw meta_json text.  That was silently
                # broken even in V3 (BANKNIFTY's meta_json contains the
                # symbol "BANKNIFTY26MAY..." which IS_NOT a superstring of
                # "NIFTY" — but the new `"underlying": "BANKNIFTY"` field
                # contains "NIFTY" as substring, blocking NIFTY entries
                # whenever BANKNIFTY was open).
                #
                # V3.1 made this far worse — "NIFTY" is also a substring of
                # FINNIFTY and MIDCPNIFTY → ANY non-NIFTY open position
                # would silently block ALL NIFTY entries for the rest of
                # the session.  Devastating for Pillar 2 trap-firing
                # because NIFTY is our best 2-lot candidate.
                #
                # Fix: parse meta_json as JSON and exact-match the
                # structured `underlying` field that open_position writes.
                held_symbol: Optional[str] = None
                for _p in db.open_positions():
                    try:
                        _m = json.loads(_p.get("meta_json") or "{}")
                    except (ValueError, TypeError):
                        continue
                    if _m.get("underlying") == name:
                        held_symbol = _p.get("symbol")
                        break

                # State-transition logging (no per-minute spam)
                _prev = in_trade_held.get(name)
                if held_symbol and held_symbol != _prev:
                    log.info(
                        "[%s] entry-scan locked — already holding %s "
                        "(will re-arm on exit)",
                        name, held_symbol,
                    )
                    in_trade_held[name] = held_symbol
                elif not held_symbol and _prev is not None:
                    log.info(
                        "[%s] position exited — entry-scan re-armed", name,
                    )
                    in_trade_held[name] = None

                if held_symbol:
                    continue

                # ─── HALT GATE ────────────────────────────────────────────
                # Latched by boot_reconcile() or runtime mismatch detection.
                # Exits are still allowed (monitor_positions runs below);
                # new entries are NOT.  Operator clears via db.clear_halt().
                if is_position_halt_active(db):
                    continue

                # Pace the 2nd+ price-candle fetch this cycle so multiple armed
                # indices don't burst the broker's rate limiter (immaterial for
                # a single armed underlying — the guard skips the first fetch).
                if SCAN_FETCH_PACING_SEC > 0 and _scan_fetches > 0:
                    time.sleep(SCAN_FETCH_PACING_SEC)
                _scan_fetches += 1

                df = fetch_intraday(api, u, lookback_minutes=5)
                if df.empty:
                    continue

                last = df.iloc[-1]
                ts_key = str(last["timestamp"])
                if ts_key == last_minute_seen[name]:
                    continue  # already evaluated this candle
                last_minute_seen[name] = ts_key

                last_close = float(last["close"])
                or_h = ors[name]["high"]
                or_l = ors[name]["low"]
                or_v = ors[name]["avg_vol"]
                vol_source = ors[name].get("vol_source", "spot")

                # Price breakout passes — now confirm with FUTURES volume.
                # Spot-index volume is always 0; we delegate the conviction
                # filter to the futures contract's 1-min volume.
                price_break_long  = last_close > or_h
                price_break_short = last_close < or_l
                if not (price_break_long or price_break_short):
                    continue

                last_vol = float(last["volume"])  # spot vol (≈0 for indices)
                if u.get("futures_token"):
                    if SCAN_FETCH_PACING_SEC > 0 and _scan_fetches > 0:
                        time.sleep(SCAN_FETCH_PACING_SEC)
                    _scan_fetches += 1
                    fut_df = fetch_futures_volume(api, u, lookback_minutes=5)
                    if not fut_df.empty:
                        try:
                            ts_target = pd.to_datetime(last["timestamp"])
                            match = fut_df[fut_df["timestamp"] == ts_target]
                            if not match.empty:
                                last_vol = float(match.iloc[-1]["fut_volume"])
                            else:
                                # Closest preceding minute, if exact match missing
                                preceding = fut_df[fut_df["timestamp"] <= ts_target]
                                if not preceding.empty:
                                    last_vol = float(preceding.iloc[-1]["fut_volume"])
                        except Exception as exc:
                            log.warning(
                                "[%s] futures vol lookup error: %s — using spot vol=%.0f",
                                name, exc, last_vol,
                            )

                vol_threshold = BREAKOUT_VOLUME_MULT * or_v
                breakout_side: Optional[str] = None
                if price_break_long and last_vol >= vol_threshold:
                    log.info(
                        "[%s] ★ LONG breakout: close=%.2f > OR_high=%.2f "
                        "fut_vol=%.0f ≥ %.1f×OR_avg=%.0f (vol_src=%s)",
                        name, last_close, or_h,
                        last_vol, BREAKOUT_VOLUME_MULT, vol_threshold, vol_source,
                    )
                    breakout_side = "LONG"
                elif price_break_short and last_vol >= vol_threshold:
                    log.info(
                        "[%s] ★ SHORT breakout: close=%.2f < OR_low=%.2f "
                        "fut_vol=%.0f ≥ %.1f×OR_avg=%.0f (vol_src=%s)",
                        name, last_close, or_l,
                        last_vol, BREAKOUT_VOLUME_MULT, vol_threshold, vol_source,
                    )
                    breakout_side = "SHORT"
                elif price_break_long or price_break_short:
                    log.info(
                        "[%s] price breakout (%s) but vol gate FAILED: "
                        "fut_vol=%.0f < %.1f×OR_avg=%.0f (vol_src=%s) — skip",
                        name,
                        "LONG" if price_break_long else "SHORT",
                        last_vol, BREAKOUT_VOLUME_MULT, vol_threshold, vol_source,
                    )

                if breakout_side is None:
                    continue

                # ── V3 HTF Bias guard: block counter-trend breakouts ─────
                if not htf_bias_allows(breakout_side):
                    log.info(
                        "[%s] Rejected by HTF Bias — breakout=%s but "
                        "DAILY_BIAS=%s (only %s entries allowed today)",
                        name, breakout_side, DAILY_BIAS,
                        "CE/LONG" if DAILY_BIAS == "BULLISH" else "PE/SHORT",
                    )
                    continue

                pos, cooldown_eligible = open_position(
                    api, paper_broker, u, breakout_side,
                    spot_at_breakout=last_close,
                    expiry=expiries[name],
                    db=db,
                )
                if pos is None and cooldown_eligible:
                    cooldown_until[name] = now + timedelta(minutes=cooldown_minutes)
                    cooldown_logged[name] = False
                    log.warning(
                        "[%s] Entry deterministically blocked — cooldown "
                        "until %s IST (%d min)",
                        name,
                        cooldown_until[name].strftime("%H:%M:%S"),
                        cooldown_minutes,
                    )

            # Monitor open positions for target hits + kill switch
            unreal, ks_now, sl_closes = monitor_positions(
                api, paper_broker, db, today_str
            )

            # Whipsaw firewall — arm 30-min post-SL cooldown for each underlying
            # whose position just closed via hard_sl or trailing_sl.
            for under_name, reason in sl_closes:
                post_sl_cooldown[under_name] = now + timedelta(minutes=post_sl_minutes)
                post_sl_logged[under_name] = False
                log.warning(
                    "[%s] Post-SL whipsaw firewall armed for %d min "
                    "(reason=%s) — entries blocked until %s IST",
                    under_name, post_sl_minutes, reason,
                    post_sl_cooldown[under_name].strftime("%H:%M:%S"),
                )

            if ks_now:
                log.critical("Kill switch JUST tripped — squaring off all then exiting")
                square_off_all(api, paper_broker, db, reason="kill_switch")
                stop_for_day = True
                break

            # Sleep until next minute boundary
            now2 = now_ist()
            secs_to_next_min = 60 - now2.second
            time.sleep(min(secs_to_next_min, 30))

        # Final pnl summary
        realised_final, _ = db.get_daily_pnl(today_str)
        log.info("=" * 72)
        log.info("DAY DONE | realised=₹%+.2f open=%d",
                 realised_final, len(db.open_positions()))
        log.info("=" * 72)

    finally:
        terminate(api)

    return 0


if __name__ == "__main__":
    sys.exit(main())
