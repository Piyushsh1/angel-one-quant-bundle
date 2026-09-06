"""
regime_eod.py  ▸  Post-close "idle-day evidence" generator
================================================================================
Runs once after market close (cron ~15:35 IST).  For every underlying the live
bot STOOD DOWN on today (regime_log.decision == 'SKIP'), this script replays
what an Opening-Range-Breakout trade *would* have done — using the SAME risk
logic the live bot uses (6% SL, +20% trail-arm to break-even, 15% peak trail,
hard-exit) — and back-fills the hypothetical P&L into regime_log.

It then emits a single human-readable summary line:

    [REGIME-EOD] 2026-06-08 | skipped=3 armed=1 traded=0 |
                 hypothetical P&L of skipped breakouts: -₹1,840 (losses avoided)

This is the operator's daily "the filter is working" dopamine hit — proof that
sitting idle on flat days SAVED money rather than missing it.

ISOLATION & SAFETY
──────────────────
* Runs entirely AFTER the close — zero interaction with the live execution loop.
* Read/replay only; the ONLY write is update_regime_hypo() into regime_log.
* All broker calls are historical-candle reads (no orders, ever).
* Hypothetical pricing is a transparent Black-Scholes approximation (fixed IV,
  ATM strike, first-breakout entry, no HTF/vol filter) — it is an *indicative*
  counterfactual, not a tick-accurate fill simulation.  This is clearly labelled
  in the dashboard.
================================================================================
"""
from __future__ import annotations

import logging
import math
import os
import sys
from datetime import datetime, time as dtime, timedelta

# Reuse the live bot's plumbing (import is side-effect-free; main() is guarded).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config                                   # noqa: E402
import option_predator as op                    # noqa: E402
from auth import login, terminate               # noqa: E402
from database import BotDB, wait_for_database    # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("regime_eod")

# ── Pricing / replay knobs (env-overridable, indicative only) ───────────────
HYPO_IV       = float(os.getenv("OPTIONS_HYPO_IV", "0.14"))    # annualised σ
RISK_FREE     = 0.065
SL_PCT        = op.SL_PCT
TRAIL_TRIGGER = op.TRAIL_TRIGGER_PCT
TRAIL_DIST    = op.TRAIL_DISTANCE_PCT
HARD_EXIT     = config.OPTIONS_HARD_EXIT_TIME
DEAD_START    = config.OPTIONS_DEAD_ZONE_START
DEAD_END      = config.OPTIONS_DEAD_ZONE_END
SCAN_START    = op.SCAN_START
SESSION_OPEN  = op.SESSION_OPEN

# Approximate option lot sizes (env-overridable). Used only to scale the
# indicative P&L into rupees; exact values drift over time but don't change
# the sign or rough magnitude of the counterfactual.
LOT_SIZES = {
    "NIFTY":      int(os.getenv("HYPO_LOT_NIFTY", "75")),
    "BANKNIFTY":  int(os.getenv("HYPO_LOT_BANKNIFTY", "35")),
    "FINNIFTY":   int(os.getenv("HYPO_LOT_FINNIFTY", "65")),
    "MIDCPNIFTY": int(os.getenv("HYPO_LOT_MIDCPNIFTY", "120")),
}

# Weekday → underlying whose weekly contract expires (mirrors the bot's map).
EXPIRY_WEEKDAY = {  # underlying -> weekday int (Mon=0)
    "MIDCPNIFTY": 0, "FINNIFTY": 1, "BANKNIFTY": 2, "NIFTY": 1,
}
# NIFTY weekly expiry is Tuesday (post-SEBI consolidation).


# ── Black-Scholes (self-contained, no scipy dependency) ─────────────────────
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, r: float, sigma: float, side: str) -> float:
    if T <= 1e-6 or sigma <= 0:
        return max(S - K, 0.0) if side == "CE" else max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if side == "CE":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _expiry_T(now: datetime, underlying: str) -> float:
    """Years to the underlying's next weekly expiry at 15:30 IST."""
    target_wd = EXPIRY_WEEKDAY.get(underlying, 1)
    days_ahead = (target_wd - now.weekday()) % 7
    if days_ahead == 0 and now.time() > dtime(15, 30):
        days_ahead = 7
    expiry_dt = (now + timedelta(days=days_ahead)).replace(
        hour=15, minute=30, second=0, microsecond=0)
    secs = (expiry_dt - now).total_seconds()
    return max(secs / (365.0 * 24 * 3600), 1e-6)


def _nearest_atm(spot: float, step: int) -> int:
    return int(round(spot / step) * step)


# ── Hypothetical replay for one skipped underlying ──────────────────────────
def replay_skip(api, underlying: dict, or_high: float, or_low: float,
                date: datetime.date) -> dict:
    """Return the indicative counterfactual for a stood-down underlying."""
    name = underlying["name"]
    step = underlying["strike_step"]
    lot = LOT_SIZES.get(name, 50)

    start = datetime.combine(date, SESSION_OPEN, tzinfo=config.IST)
    end = datetime.combine(date, HARD_EXIT, tzinfo=config.IST) + timedelta(minutes=5)
    df = op._fetch_candles(  # noqa: SLF001
        api, token=underlying["token"], exchange=underlying["exchange"],
        name=name, start=start, end=end, interval="ONE_MINUTE",
    )
    if df.empty:
        return _no_trade("NO_DATA")

    df = df[(df["timestamp"].dt.time >= SCAN_START)
            & (df["timestamp"].dt.time <= HARD_EXIT)].reset_index(drop=True)
    if df.empty:
        return _no_trade("NO_DATA")

    # 1) find the first breakout (either direction), outside dead-zone
    side = None
    entry_row = None
    for _, row in df.iterrows():
        t = row["timestamp"].time()
        if DEAD_START <= t < DEAD_END:
            continue
        if row["close"] > or_high:
            side, entry_row = "CE", row
            break
        if row["close"] < or_low:
            side, entry_row = "PE", row
            break
    if side is None:
        return _no_trade("NO_BREAKOUT")

    entry_ts = entry_row["timestamp"]
    spot_entry = float(entry_row["close"])
    strike = _nearest_atm(spot_entry, step)
    T0 = _expiry_T(entry_ts.to_pydatetime(), name)
    entry_prem = bs_price(spot_entry, strike, T0, RISK_FREE, HYPO_IV, side)
    if entry_prem < 1.0:
        return _no_trade("DUST_PREMIUM")

    sl = entry_prem * (1 - SL_PCT)
    peak = entry_prem
    armed = False

    post = df[df["timestamp"] > entry_ts]
    exit_prem, reason = None, None
    for _, row in post.iterrows():
        ts = row["timestamp"]
        T = _expiry_T(ts.to_pydatetime(), name)
        prem = bs_price(float(row["close"]), strike, T, RISK_FREE, HYPO_IV, side)
        if prem > peak:
            peak = prem
        if not armed and prem >= entry_prem * (1 + TRAIL_TRIGGER):
            armed = True
            sl = max(sl, entry_prem)              # hop to break-even
        if armed:
            trail_floor = peak * (1 - TRAIL_DIST)
            if trail_floor > sl:
                sl = trail_floor
        # adverse extreme within the bar
        adverse_spot = float(row["low"]) if side == "CE" else float(row["high"])
        adverse_prem = bs_price(adverse_spot, strike, T, RISK_FREE, HYPO_IV, side)
        if adverse_prem <= sl:
            exit_prem, reason = sl, ("TRAIL" if armed else "SL")
            break
        if ts.time() >= HARD_EXIT:
            exit_prem, reason = prem, "EOD"
            break
    if exit_prem is None:
        last = post.iloc[-1] if len(post) else entry_row
        T = _expiry_T(last["timestamp"].to_pydatetime(), name)
        exit_prem = bs_price(float(last["close"]), strike, T, RISK_FREE, HYPO_IV, side)
        reason = "EOD"

    pnl = round((exit_prem - entry_prem) * lot, 2)
    return {
        "hypo_side": "LONG" if side == "CE" else "SHORT",
        "hypo_breakout_ts": entry_ts.isoformat(),
        "hypo_entry_prem": round(entry_prem, 2),
        "hypo_exit_prem": round(exit_prem, 2),
        "hypo_exit_reason": reason,
        "hypo_pnl": pnl,
    }


def _no_trade(reason: str) -> dict:
    return {
        "hypo_side": "NONE", "hypo_breakout_ts": None,
        "hypo_entry_prem": None, "hypo_exit_prem": None,
        "hypo_exit_reason": reason, "hypo_pnl": 0.0,
    }


# ── Main ────────────────────────────────────────────────────────────────────
def main() -> int:
    wait_for_database()
    db = BotDB("OPTIONS")
    today = op.now_ist().date()
    today_str = today.isoformat()

    rows = db.regime_log_for_date(today_str)
    if not rows:
        log.info("[REGIME-EOD] %s | no regime_log rows (gate disabled or no "
                 "session today) — nothing to do", today_str)
        return 0

    skips = [r for r in rows if r["decision"] == "SKIP"]
    armed = [r for r in rows if r["decision"] == "ARMED"]

    # underlyings that actually traded today (a BUY in trade_history)
    traded_unders = set()
    for t in db.trade_history(limit=200):
        if t["side"] == "BUY" and str(t["timestamp"]).startswith(today_str):
            for u in op.UNDERLYINGS:
                if t["symbol"].startswith(u["name"]):
                    traded_unders.add(u["name"])

    api = None
    if skips:
        try:
            api = login()
        except Exception as exc:  # noqa: BLE001
            log.error("login failed — cannot compute hypotheticals: %s", exc)
            api = None

    by_name = {u["name"]: u for u in op.UNDERLYINGS}
    total_hypo = 0.0
    losses_avoided = 0.0
    detail_lines = []
    for r in skips:
        name = r["underlying"]
        u = by_name.get(name)
        if api is None or u is None or r["or_high"] is None:
            res = _no_trade("NO_REPLAY")
        else:
            try:
                res = replay_skip(api, u, r["or_high"], r["or_low"], today)
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] replay failed: %s", name, exc)
                res = _no_trade("REPLAY_ERROR")
        try:
            db.update_regime_hypo(date_str=today_str, underlying=name, **res)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] hypo write failed: %s", name, exc)

        pnl = res["hypo_pnl"] or 0.0
        total_hypo += pnl
        if pnl < 0:
            losses_avoided += pnl
        detail_lines.append(
            f"    {name:<11} {res['hypo_side']:<5} "
            f"{res['hypo_exit_reason']:<12} hypo P&L=₹{pnl:>+9,.0f}"
        )

    if api is not None:
        try:
            terminate(api)
        except Exception:  # noqa: BLE001
            pass

    log.info("=" * 72)
    log.info(
        "[REGIME-EOD] %s | skipped=%d armed=%d traded=%d | "
        "hypothetical P&L of skipped breakouts: ₹%s",
        today_str, len(skips), len(armed), len(traded_unders),
        f"{total_hypo:+,.0f}",
    )
    if losses_avoided < 0:
        log.info("             → losses AVOIDED by standing down: ₹%s",
                 f"{losses_avoided:+,.0f}")
    for line in detail_lines:
        log.info(line)
    log.info("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
