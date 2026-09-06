"""algo-barbell  ▸  backtest_engine.py
================================================================================
V3 ALPHA — Standalone Backtest Harness for the Options Engine
--------------------------------------------------------------------------------
Replays the ORB strategy across the last N trading days using cached 1-minute
NIFTY spot and NIFTY current-month-future data, sweeping the breakout volume
multiplier so the operator can pick the optimal value before flipping live.

What this script does (and does NOT do)
---------------------------------------

DOES
    * Fetch 1-min NIFTY spot + futures candles for the last ``LOOKBACK_DAYS``
      and cache them in the ``candles`` table (re-runs can then be offline).
    * Fetch NIFTY daily candles for HTF (50-SMA) bias replay.
    * For each day in scope and each multiplier in ``MULTIPLIER_SWEEP``:
        - compute the 09:15–09:30 Opening Range (spot price + FUTIDX volume)
        - compute the daily HTF bias as-of yesterday's close
        - walk every minute 09:31 → 15:05 IST simulating:
              · the futures-volume gate at this multiplier
              · the HTF bias entry guard
              · the dead-zone (11:30–13:00 IST) entry skip
              · the position-per-underlying "one trade at a time" rule
              · entries, scale-out at +20% (half lots), trail at -10% peak,
                hard SL at -10%, hard exit at 15:05
        - book hypothetical premium-% P&L for every trade
    * Aggregate per multiplier: total trades, win rate, avg P&L %, expectancy.

DOES NOT
    * Touch trading state.  It only ever reads/writes the ``candles`` table —
      never open_positions, trade_history, daily_pnl or bot_state.
    * Place any broker order, in any mode.
    * Use real option premium data.  Premium is modelled as a function of the
      spot move (ATM Δ ≈ 0.50) over a notional entry premium of ₹100, so the
      backtest measures DIRECTIONAL EDGE not exact ₹ P&L.  This is sufficient
      for ranking multipliers — which is the only question the sweep asks.

Usage
-----

    cd /home/ubuntu/algo-barbell
    ./venv/bin/python backtest_engine.py
    ./venv/bin/python backtest_engine.py --lookback 30   # custom window
    ./venv/bin/python backtest_engine.py --no-htf        # disable HTF filter
    ./venv/bin/python backtest_engine.py --no-deadzone   # disable dead zone
    ./venv/bin/python backtest_engine.py --offline       # skip the API fetch
================================================================================
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, time as dtime, date
from typing import Iterator, Optional

import pandas as pd

import broker
import config
import instrument_master
from auth import login, terminate
from database import CandleCache, wait_for_database

log = logging.getLogger("backtest")


# ════════════════════════════════════════════════════════════════════════════
#  CONSTANTS  (mirror option_predator.py for fidelity)
# ════════════════════════════════════════════════════════════════════════════
# Candle history now lives in the shared Postgres database (table: candles),
# not in a local SQLite file.

NIFTY_SPOT_TOKEN = "99926000"          # NSE NIFTY 50 spot index
NIFTY_SPOT_NAME  = "NIFTY"
NIFTY_SPOT_EXCH  = "NSE"

LOOKBACK_DAYS_DEFAULT = 60
CHUNK_DAYS = 10                        # Angel's 1-min endpoint limit per call

# Trading windows (IST)
SESSION_OPEN = dtime(9, 15)
OR_START = dtime(9, 15)
OR_END   = dtime(9, 30)
SCAN_START = dtime(9, 31)
HARD_EXIT  = dtime(15, 5)
PROCESS_END = dtime(15, 10)

# Strategy constants (locked to live engine)
SL_PCT             = 0.10
TRAIL_TRIGGER_PCT  = 0.20
TRAIL_DISTANCE_PCT = 0.10
MAX_LOTS           = 2

# HTF bias & dead zone defaults
HTF_SMA_PERIOD = 50
DEAD_ZONE_START = dtime(11, 30)
DEAD_ZONE_END   = dtime(13, 0)

# Multiplier sweep (architect spec: 1.1 → 1.5 step 0.1)
MULTIPLIER_SWEEP = [1.1, 1.2, 1.3, 1.4, 1.5]

# Opening-Range-Width filter sweep (V3 backtester-only — architect spec
# 21 May 2026: "test OR Width filter ONLY in the backtester, not in live
# production yet").  Expressed in PERCENT.  `or_range_pct = (OR_high -
# OR_low) / spot_close_at_OR_end × 100`.  Sweep 0.20 % → 0.50 % step 0.05.
# Reported with `0.0` as a control row (filter disabled).
MIN_OR_RANGE_PCT_SWEEP = [0.0, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]

# Synthetic option model
ENTRY_PREMIUM   = 100.0      # notional ₹; reports are % so absolute value is moot
ATM_DELTA       = 0.50       # ≈ ATM call delta at the moment of entry
TRADE_HORIZON_MIN = 60       # max minutes a trade may live (15:05 hard cap also applies)


# ════════════════════════════════════════════════════════════════════════════
#  CACHE LAYER  (candles table in the shared application database)
# ════════════════════════════════════════════════════════════════════════════
# The candle cache used to be a side-car SQLite file. It now lives in the same
# Postgres database as everything else: one database technology for the whole
# application, and this data (expensive to refetch — rate-limited, 30-day
# chunks) gets backed up along with trading state.
_cache: CandleCache | None = None


def _cache_handle() -> CandleCache:
    global _cache
    if _cache is None:
        wait_for_database()
        _cache = CandleCache()
    return _cache


def ensure_cache_schema() -> None:
    """Create the schema if needed. Idempotent."""
    _cache_handle()


def cache_insert(symbol: str, token: str, interval: str, df: pd.DataFrame) -> int:
    """Insert candles, ignoring duplicates.  Returns rows actually written."""
    if df.empty:
        return 0
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    rows = [
        (
            r.timestamp,
            None if pd.isna(r.open) else float(r.open),
            None if pd.isna(r.high) else float(r.high),
            None if pd.isna(r.low) else float(r.low),
            None if pd.isna(r.close) else float(r.close),
            None if pd.isna(r.volume) else int(r.volume),
        )
        for r in df[["timestamp", "open", "high", "low", "close", "volume"]]
        .itertuples(index=False)
    ]
    return _cache_handle().insert(symbol, token, interval, rows)


def cache_load(symbol: str, interval: str,
               start: Optional[datetime] = None,
               end: Optional[datetime] = None) -> pd.DataFrame:
    rows = _cache_handle().load(
        symbol,
        interval,
        start=start.strftime("%Y-%m-%dT%H:%M:%S") if start else None,
        end=end.strftime("%Y-%m-%dT%H:%M:%S") if end else None,
    )
    df = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"]).reset_index(drop=True)


# ════════════════════════════════════════════════════════════════════════════
#  API FETCH  (chunked to dodge Angel's 30-day-per-call cap on 1-min data)
# ════════════════════════════════════════════════════════════════════════════
def _chunk_range(start: datetime, end: datetime, chunk_days: int) -> Iterator[tuple[datetime, datetime]]:
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=chunk_days), end)
        yield cur, nxt
        cur = nxt


def fetch_to_cache(api, *, symbol: str, token: str, exchange: str, interval: str,
                   start: datetime, end: datetime) -> int:
    """Fetch ``[start, end]`` in chunks and upsert into the cache.

    Returns the number of NEW rows added (existing rows are ignored via the
    PRIMARY KEY).  Sleeps ``API_PACING_SECONDS`` between chunks to respect
    Angel's throttle (currently 1.2 s).
    """
    fmt = "%Y-%m-%d %H:%M"
    chunk_days = CHUNK_DAYS if interval == "ONE_MINUTE" else 90
    total_new = 0
    for a, b in _chunk_range(start, end, chunk_days):
        params = {
            "exchange":    exchange,
            "symboltoken": token,
            "interval":    interval,
            "fromdate":    a.strftime(fmt),
            "todate":      b.strftime(fmt),
        }
        try:
            resp = broker._candle_raw(api, params)  # noqa: SLF001
        except Exception as exc:
            log.warning("fetch %s %s [%s..%s] failed: %s",
                        symbol, interval, a, b, exc)
            time.sleep(config.API_PACING_SECONDS)
            continue
        rows = (resp or {}).get("data", []) or []
        if rows:
            df = pd.DataFrame(rows,
                              columns=["timestamp", "open", "high", "low", "close", "volume"])
            added = cache_insert(symbol, token, interval, df)
            total_new += added
            log.info("  + %5d new rows  %s %s  %s → %s",
                     added, symbol, interval, a.date(), b.date())
        time.sleep(config.API_PACING_SECONDS)
    return total_new


# ════════════════════════════════════════════════════════════════════════════
#  HTF BIAS  (replay using cached daily candles)
# ════════════════════════════════════════════════════════════════════════════
def htf_bias_for(daily_df: pd.DataFrame, trading_day: date,
                 sma_period: int = HTF_SMA_PERIOD) -> str:
    """Compute HTF bias as it would have been computed at 09:14 on ``trading_day``.

    Uses the close of the most recent COMPLETED daily bar strictly before
    ``trading_day`` against the SMA of the prior ``sma_period`` bars.
    """
    if daily_df is None or daily_df.empty:
        return "NEUTRAL"
    prior = daily_df[daily_df["timestamp"].dt.date < trading_day]
    if len(prior) < sma_period + 1:
        return "NEUTRAL"
    prior_close = float(prior.iloc[-1]["close"])
    sma = float(prior["close"].iloc[-sma_period:].mean())
    if prior_close > sma:
        return "BULLISH"
    if prior_close < sma:
        return "BEARISH"
    return "NEUTRAL"


def htf_allows(bias: str, side: str, enabled: bool) -> bool:
    if not enabled:
        return True
    if bias == "BULLISH":
        return side == "LONG"
    if bias == "BEARISH":
        return side == "SHORT"
    return True


def in_dead_zone(t: dtime) -> bool:
    return DEAD_ZONE_START <= t < DEAD_ZONE_END


# ════════════════════════════════════════════════════════════════════════════
#  SYNTHETIC TRADE SIMULATION
# ════════════════════════════════════════════════════════════════════════════
def simulate_trade(
    *, side: str, entry_spot: float, future_bars: pd.DataFrame,
) -> dict:
    """Walk forward minute-by-minute from entry until SL / scale-out / 15:05.

    ``future_bars`` must be a DataFrame of 1-min spot candles ordered by time,
    starting from the minute AFTER entry and bounded by 15:05 IST.  Columns:
    ``timestamp, open, high, low, close``.

    Returns a dict with the trade outcome:
        {
          "outcome":         "scale_then_trail" | "hard_sl" | "trailing_sl" |
                             "hard_exit_1505" | "no_data",
          "exit_premium_pct": float,   # final %P&L after scale-out + runner
          "lots_sold":       0|1|2,
          "phase_b_triggered": bool,
          "exit_timestamp":  pd.Timestamp,
          "minutes_held":    int,
        }
    Premium is modelled as::

        prem(spot) = ENTRY_PREMIUM ± ATM_DELTA × (spot - entry_spot)

    where ``+`` is used for LONG (CE buys) and ``-`` is used for SHORT (PE buys).
    """
    sign = 1.0 if side == "LONG" else -1.0
    entry_prem = ENTRY_PREMIUM
    sl_prem = entry_prem * (1 - SL_PCT)              # initial -10%
    trigger_prem = entry_prem * (1 + TRAIL_TRIGGER_PCT)  # +20% (BE-arm / scale)
    peak_prem = entry_prem

    trail_armed = False
    phase_b_triggered = False
    lots_sold = 0
    lots_remaining = MAX_LOTS
    # Premium P&L is per-lot; we compose total across lots that were sold and
    # the runner that remains, weighted by lot count.
    realised_pct = 0.0
    # ratio: how much of the original position is "alive"
    alive_lots = MAX_LOTS

    if future_bars.empty:
        return {
            "outcome": "no_data", "exit_premium_pct": 0.0,
            "lots_sold": 0, "phase_b_triggered": False,
            "exit_timestamp": pd.NaT, "minutes_held": 0,
        }

    last_close = entry_prem
    last_ts = future_bars.iloc[0]["timestamp"]
    for i, row in future_bars.iterrows():
        last_ts = row["timestamp"]
        # Use HIGH/LOW for SL/trigger checks (intra-minute extremes); use
        # close for peak/SL ratcheting (conservative reasonable approximation).
        high_prem = entry_prem + sign * ATM_DELTA * (float(row["high"]) - entry_spot)
        low_prem  = entry_prem + sign * ATM_DELTA * (float(row["low"])  - entry_spot)
        close_prem = entry_prem + sign * ATM_DELTA * (float(row["close"]) - entry_spot)

        # For LONG (sign=+1): high_prem > low_prem; vice versa for SHORT.
        # Define intra-bar best & worst premium based on side.
        if side == "LONG":
            best_prem  = high_prem
            worst_prem = low_prem
        else:
            best_prem  = low_prem   # ← in SHORT, low spot means high premium for PE
            worst_prem = high_prem
            # Recompute: for PE buys, prem moves +0.5×(entry_spot - spot).  If
            # spot LOW < entry_spot → prem rises (best); spot HIGH > entry_spot
            # → prem falls (worst).  Verify the sign convention:
            #   prem(spot)   = ENTRY_PREMIUM - 0.50 × (spot - entry_spot)
            #   prem(LOW)    = entry_prem  - 0.50 × (LOW  - entry_spot)   ← MAX
            #   prem(HIGH)   = entry_prem  - 0.50 × (HIGH - entry_spot)   ← MIN
            # The formula above with sign=-1 already produces:
            #   high_prem = entry_prem + (-1) × 0.50 × (HIGH - entry_spot)
            #             = entry_prem - 0.50 × (HIGH - entry_spot)   ← MIN  ✓
            #   low_prem  = entry_prem - 0.50 × (LOW  - entry_spot)   ← MAX  ✓
            # so for SHORT: best = low_prem (=MAX), worst = high_prem (=MIN).

        # Update peak using best
        if best_prem > peak_prem:
            peak_prem = best_prem

        # ── Pessimistic order-of-events: SL check first ────────────────
        if not trail_armed:
            # Phase A — hard SL at -10%
            if worst_prem <= sl_prem:
                # SL hit; runner-loss = -10% × alive_lots
                pnl_per_lot_pct = (sl_prem - entry_prem) / entry_prem * 100.0
                realised_pct += pnl_per_lot_pct * alive_lots / MAX_LOTS
                return {
                    "outcome": "hard_sl", "exit_premium_pct": realised_pct,
                    "lots_sold": lots_sold, "phase_b_triggered": phase_b_triggered,
                    "exit_timestamp": last_ts, "minutes_held": i + 1,
                }
            # Phase B trigger: did the bar reach +20%?
            if best_prem >= trigger_prem:
                phase_b_triggered = True
                if lots_remaining >= 2:
                    lots_to_sell = max(1, lots_remaining // 2)
                    # Realise the +20% on the sold lots (assume fill at the trigger,
                    # which is the conservative midpoint).
                    pnl_sold_pct = (trigger_prem - entry_prem) / entry_prem * 100.0
                    realised_pct += pnl_sold_pct * lots_to_sell / MAX_LOTS
                    lots_sold += lots_to_sell
                    lots_remaining -= lots_to_sell
                    alive_lots = lots_remaining
                    # SL hops to break-even for the runner
                    sl_prem = entry_prem
                    trail_armed = True
                else:
                    # Single-lot — graceful degrade (no scale-out)
                    sl_prem = entry_prem
                    trail_armed = True
                # Continue this bar: a violent reversal could still hit SL=BE same minute
                # Skip secondary check for simplicity (one transition per bar)
                continue
        else:
            # Already armed — Phase C trail
            trail_floor = peak_prem * (1 - TRAIL_DISTANCE_PCT)
            sl_prem = max(sl_prem, trail_floor, entry_prem)   # never below BE
            if worst_prem <= sl_prem:
                pnl_runner_pct = (sl_prem - entry_prem) / entry_prem * 100.0
                realised_pct += pnl_runner_pct * alive_lots / MAX_LOTS
                return {
                    "outcome": "trailing_sl", "exit_premium_pct": realised_pct,
                    "lots_sold": lots_sold, "phase_b_triggered": phase_b_triggered,
                    "exit_timestamp": last_ts, "minutes_held": i + 1,
                }
        last_close = close_prem

    # Loop ended without SL — hard exit at 15:05 (or out of horizon)
    pnl_close_pct = (last_close - entry_prem) / entry_prem * 100.0
    realised_pct += pnl_close_pct * alive_lots / MAX_LOTS
    return {
        "outcome": "hard_exit_1505", "exit_premium_pct": realised_pct,
        "lots_sold": lots_sold, "phase_b_triggered": phase_b_triggered,
        "exit_timestamp": last_ts, "minutes_held": len(future_bars),
    }


# ════════════════════════════════════════════════════════════════════════════
#  PER-DAY SIMULATION
# ════════════════════════════════════════════════════════════════════════════
def simulate_day(
    *, trading_day: date, spot_1m: pd.DataFrame, futures_1m: pd.DataFrame,
    daily_df: pd.DataFrame, vol_mult: float,
    htf_on: bool = True, dead_zone_on: bool = True,
    min_or_range_pct: float = 0.0,
) -> list[dict]:
    """Simulate one trading day; return list of trade dicts.

    ``min_or_range_pct`` (V3 backtester-only filter): if the day's OR-Width
    percentage ``(or_high − or_low) / spot_close_at_OR_end × 100`` is below
    this threshold, the entire day is skipped (no entries simulated).
    ``0.0`` disables the filter (default — control row in the sweep).
    """
    # Filter to this day
    spot_day = spot_1m[spot_1m["timestamp"].dt.date == trading_day].copy()
    if spot_day.empty:
        return []
    fut_day = futures_1m[futures_1m["timestamp"].dt.date == trading_day].copy()

    # OR bars: 09:15–09:29 inclusive (15 bars).  We use 09:15 ≤ t < 09:30.
    or_mask = (spot_day["timestamp"].dt.time >= OR_START) & \
              (spot_day["timestamp"].dt.time < OR_END)
    or_spot = spot_day[or_mask]
    if or_spot.empty:
        return []
    or_high = float(or_spot["high"].max())
    or_low  = float(or_spot["low"].min())
    # The "spot_close" reference for the OR-Width filter is the close of
    # the LAST bar inside the OR window (09:29).  Robust against missing
    # rows by falling back to (high+low)/2 of the OR.
    spot_close_at_or_end = float(or_spot.iloc[-1]["close"]) \
        if not or_spot.empty else (or_high + or_low) / 2.0
    or_range_pct = (or_high - or_low) / max(spot_close_at_or_end, 1e-9) * 100.0

    # ── V3 backtester-only OR-Width filter ─────────────────────────────
    if min_or_range_pct > 0.0 and or_range_pct < min_or_range_pct:
        return []   # day skipped — OR too tight to trade

    fut_or_mask = (fut_day["timestamp"].dt.time >= OR_START) & \
                  (fut_day["timestamp"].dt.time < OR_END)
    or_fut = fut_day[fut_or_mask]
    if or_fut.empty or or_fut["volume"].sum() == 0:
        return []
    or_avg_vol = float(or_fut["volume"].mean())

    threshold = vol_mult * or_avg_vol
    bias = htf_bias_for(daily_df, trading_day) if htf_on else "NEUTRAL"

    # Scan bars from 09:31 to 15:04
    scan_mask = (spot_day["timestamp"].dt.time >= SCAN_START) & \
                (spot_day["timestamp"].dt.time < HARD_EXIT)
    scan = spot_day[scan_mask].reset_index(drop=True)

    fut_idx = fut_day.set_index("timestamp")

    trades: list[dict] = []
    in_trade_until: Optional[pd.Timestamp] = None

    for i, row in scan.iterrows():
        ts = row["timestamp"]
        ts_t = ts.time()
        if in_trade_until is not None and ts <= in_trade_until:
            continue
        in_trade_until = None

        if dead_zone_on and in_dead_zone(ts_t):
            continue

        last_close = float(row["close"])
        price_long  = last_close > or_high
        price_short = last_close < or_low
        if not (price_long or price_short):
            continue

        # Lookup futures volume for THIS minute (or nearest preceding)
        try:
            v = fut_idx.loc[ts, "volume"]
            if isinstance(v, pd.Series):
                v = v.iloc[-1]
            last_vol = float(v)
        except KeyError:
            preceding = fut_idx[fut_idx.index <= ts]
            if preceding.empty:
                continue
            last_vol = float(preceding.iloc[-1]["volume"])

        if last_vol < threshold:
            continue

        side = "LONG" if price_long else "SHORT"
        if not htf_allows(bias, side, htf_on):
            continue

        # Walk forward for up to TRADE_HORIZON_MIN minutes, bounded by 15:05
        future = scan.iloc[i + 1:].copy()
        horizon_cutoff = ts + pd.Timedelta(minutes=TRADE_HORIZON_MIN)
        hard_exit_cutoff = pd.Timestamp.combine(
            ts.date(), HARD_EXIT
        ).tz_localize(ts.tzinfo) if ts.tzinfo else pd.Timestamp.combine(
            ts.date(), HARD_EXIT
        )
        cutoff = min(horizon_cutoff, hard_exit_cutoff)
        future = future[future["timestamp"] <= cutoff].reset_index(drop=True)

        result = simulate_trade(side=side, entry_spot=last_close, future_bars=future)
        trades.append({
            "date":            trading_day.isoformat(),
            "entry_ts":        ts,
            "side":            side,
            "entry_spot":      last_close,
            "or_high":         or_high,
            "or_low":          or_low,
            "or_range_pct":    or_range_pct,
            "or_avg_vol":      or_avg_vol,
            "fut_vol":         last_vol,
            "vol_mult":        vol_mult,
            "bias":            bias,
            **result,
        })
        # Block re-entry on this underlying until current trade exits
        in_trade_until = result.get("exit_timestamp") or cutoff

    return trades


# ════════════════════════════════════════════════════════════════════════════
#  SWEEP + REPORT
# ════════════════════════════════════════════════════════════════════════════
def aggregate(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0,
                "win_rate": 0.0, "avg_pnl": 0.0,
                "expectancy": 0.0, "best": 0.0, "worst": 0.0}
    pnls = [t["exit_premium_pct"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    return {
        "n":          len(pnls),
        "wins":       wins,
        "losses":     losses,
        "win_rate":   wins / len(pnls) * 100.0,
        "avg_pnl":    sum(pnls) / len(pnls),
        "expectancy": sum(pnls) / len(pnls),     # = avg_pnl per trade (premium-%)
        "best":       max(pnls),
        "worst":      min(pnls),
    }


def run_sweep(
    *, spot_1m: pd.DataFrame, futures_1m: pd.DataFrame, daily_df: pd.DataFrame,
    multipliers: list[float], min_or_range_pcts: list[float],
    htf_on: bool, dead_zone_on: bool,
) -> dict[tuple[float, float], dict]:
    """Run a 2-D sweep over (vol_mult × min_or_range_pct).

    Returns a dict keyed by ``(vol_mult, min_or_range_pct)`` whose values
    are the standard `aggregate()` blobs.
    """
    days = sorted(set(spot_1m["timestamp"].dt.date.unique()))
    log.info(
        "Replaying %d trading days  ·  multipliers=%s  ·  min_or_range_pct=%s  "
        "·  HTF=%s  DZ=%s",
        len(days), multipliers, min_or_range_pcts, htf_on, dead_zone_on,
    )
    out: dict[tuple[float, float], dict] = {}
    for m in multipliers:
        for r in min_or_range_pcts:
            trades: list[dict] = []
            days_traded = 0
            for d in days:
                day_trades = simulate_day(
                    trading_day=d, spot_1m=spot_1m, futures_1m=futures_1m,
                    daily_df=daily_df, vol_mult=m,
                    htf_on=htf_on, dead_zone_on=dead_zone_on,
                    min_or_range_pct=r,
                )
                if day_trades:
                    days_traded += 1
                trades.extend(day_trades)
            agg = aggregate(trades)
            agg["trades"] = trades
            agg["per_day"] = (agg["n"] / max(len(days), 1))
            agg["days_traded"] = days_traded
            agg["days_total"] = len(days)
            out[(m, r)] = agg
            log.info(
                "  mult=%.1f  min_or_range=%.2f%%  n=%4d  trades/day=%.2f  "
                "days_traded=%d/%d  win=%5.1f%%  avgP&L=%+6.2f%%  "
                "best=%+6.1f%%  worst=%+6.1f%%",
                m, r, agg["n"], agg["per_day"], days_traded, len(days),
                agg["win_rate"], agg["avg_pnl"], agg["best"], agg["worst"],
            )
    return out


def print_report(results: dict[tuple[float, float], dict]) -> None:
    """2-D heat-table: rows = multipliers, cols = min_or_range_pct.

    Emitted through the logger so a sweep that runs unattended (cron, CI)
    still leaves the result table in the log file rather than only on a
    terminal nobody was watching.
    """
    mults = sorted({m for (m, _) in results.keys()})
    ranges = sorted({r for (_, r) in results.keys()})

    log.info("")
    log.info("╔" + "═" * 96 + "╗")
    log.info("║  V3 ALPHA — Multiplier × OR-Width Sweep"
             "                                                        ║")
    log.info("║  Synthetic-premium model: Δ=0.50 × spot move on ₹%.0f base prem."
             "  Cells = expectancy %%       ║", ENTRY_PREMIUM)
    log.info("╠" + "═" * 96 + "╣")

    # Header row (range thresholds across columns)
    hdr = "║  Mult \\ ORW% │ " + " │ ".join(
        f"{('disabled' if r == 0.0 else f'{r:>5.2f}%'):>8}" for r in ranges
    ) + "  ║"
    log.info(hdr)
    log.info("╠" + "─" * 96 + "╣")

    # Body — Expectancy %
    for m in mults:
        cells = []
        for r in ranges:
            a = results[(m, r)]
            cell = f"{a['expectancy']:+7.2f}" if a["n"] > 0 else "    n/a"
            cells.append(f"{cell:>8}")
        log.info("║  vol=%-4.1f      │ %s  ║", m, " │ ".join(cells))

    log.info("╠" + "─" * 96 + "╣")
    # Footer row — Trades/Day for context (helps spot the 3–5/day band)
    log.info("║  TRADES/DAY  │ %s  ║", " │ ".join(f"{'':>8}" for _ in ranges))
    for m in mults:
        cells = []
        for r in ranges:
            a = results[(m, r)]
            cell = f"{a['per_day']:>6.2f}/d"
            cells.append(f"{cell:>8}")
        log.info("║  vol=%-4.1f      │ %s  ║", m, " │ ".join(cells))
    log.info("╚" + "═" * 96 + "╝")
    log.info("")

    # Sweet-spot identification — same logic, but now across the joint param space
    in_band = [k for k, a in results.items() if 3.0 <= a["per_day"] <= 5.0]
    if in_band:
        best_key = max(in_band, key=lambda k: results[k]["expectancy"])
        m, r = best_key
        a = results[best_key]
        log.info("  ★ Recommended joint setting:  OPTIONS_VOL_MULT=%.1f  "
                 "OPTIONS_MIN_OR_RANGE_PCT=%.2f%%", m, r)
        log.info("     within 3–5 trades/day band  ·  n=%d  trades/day=%.2f  "
                 "win=%.1f%%  expectancy=%+.2f%%",
                 a["n"], a["per_day"], a["win_rate"], a["expectancy"])
    else:
        best_key = max(results.keys(), key=lambda k: results[k]["expectancy"])
        m, r = best_key
        a = results[best_key]
        log.warning("  ⚠ No (mult, OR-width) combination produced 3–5 trades/day.")
        log.warning("     Best raw expectancy: vol_mult=%.1f  "
                    "min_or_range_pct=%.2f%%  (%.2f trades/day, EV=%+.2f%%)",
                    m, r, a["per_day"], a["expectancy"])

    # Honourable mention: same expectancy filter but at "vol-only" (range=0)
    vol_only = [(m, results[(m, 0.0)]) for m in mults if (m, 0.0) in results]
    if vol_only:
        best_vol = max(vol_only, key=lambda x: x[1]["expectancy"])
        log.info("  · Best vol-only baseline (no OR-Width filter): "
                 "OPTIONS_VOL_MULT=%.1f  EV=%+.2f%%  (%.2f trades/day)",
                 best_vol[0], best_vol[1]["expectancy"], best_vol[1]["per_day"])
    log.info("")


# ════════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DAYS_DEFAULT,
                    help="calendar days of history to fetch (default 60)")
    ap.add_argument("--offline", action="store_true",
                    help="skip API fetch; use whatever is already cached")
    ap.add_argument("--no-htf", action="store_true",
                    help="disable HTF Daily Bias filter in the simulation")
    ap.add_argument("--no-deadzone", action="store_true",
                    help="disable the 11:30–13:00 dead zone in the simulation")
    ap.add_argument("--multipliers", default="1.1,1.2,1.3,1.4,1.5",
                    help="comma-separated list of multipliers to sweep")
    ap.add_argument("--min-or-range",
                    default="0.0,0.20,0.25,0.30,0.35,0.40,0.45,0.50",
                    help="comma-separated list of OR-Width%% thresholds to sweep "
                         "(0.0 = filter disabled, baseline)")
    ap.add_argument("--log-level", default="INFO",
                    help="DEBUG / INFO / WARNING")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    mults = [float(x.strip()) for x in args.multipliers.split(",") if x.strip()]
    or_ranges = [float(x.strip()) for x in args.min_or_range.split(",") if x.strip()]
    end_dt = datetime.now(config.IST).replace(hour=15, minute=30, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=args.lookback)

    ensure_cache_schema()
    log.info("Cache DB : %s (table: candles)", config._redacted_dsn())
    log.info("Window   : %s → %s (%d days)",
             start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"),
             args.lookback)

    api = None
    if not args.offline:
        log.info("Authenticating to Angel One…")
        try:
            api = login()
        except Exception as exc:
            log.critical("Login failed: %s — falling back to --offline mode", exc)
            api = None

        if api is not None:
            try:
                log.info("Resolving NIFTY current-month future…")
                fut_sym, fut_tok, fut_lot = instrument_master.resolve_index_future("NIFTY")
                log.info("  futures contract: %s (token=%s, lot=%d)",
                         fut_sym, fut_tok, fut_lot)
            except Exception as exc:
                log.critical("Failed to resolve NIFTY future: %s", exc)
                terminate(api)
                return 2

            log.info("Fetching 1-min NIFTY SPOT…")
            fetch_to_cache(api, symbol=NIFTY_SPOT_NAME, token=NIFTY_SPOT_TOKEN,
                           exchange=NIFTY_SPOT_EXCH, interval="ONE_MINUTE",
                           start=start_dt, end=end_dt)

            log.info("Fetching 1-min %s (NFO futures)…", fut_sym)
            fetch_to_cache(api, symbol=fut_sym, token=fut_tok,
                           exchange="NFO", interval="ONE_MINUTE",
                           start=start_dt, end=end_dt)

            log.info("Fetching NIFTY DAILY (for HTF bias)…")
            daily_start = start_dt - timedelta(days=HTF_SMA_PERIOD * 2 + 30)
            fetch_to_cache(api, symbol=NIFTY_SPOT_NAME, token=NIFTY_SPOT_TOKEN,
                           exchange=NIFTY_SPOT_EXCH, interval="ONE_DAY",
                           start=daily_start, end=end_dt)
            terminate(api)

    # Load cached data
    log.info("Loading cached data…")
    spot_1m = cache_load(NIFTY_SPOT_NAME, "ONE_MINUTE", start_dt, end_dt)
    log.info("  NIFTY spot 1-min : %d bars", len(spot_1m))

    # Find any futures symbol in the cache (most recent month)
    fut_symbols = _cache_handle().symbols(like="%FUT%")
    if not fut_symbols:
        log.critical("No futures symbol cached; aborting.  "
                     "Run without --offline first to populate the cache.")
        return 3
    fut_symbol = sorted(fut_symbols, reverse=True)[0]
    log.info("  Futures symbol   : %s", fut_symbol)
    futures_1m = cache_load(fut_symbol, "ONE_MINUTE", start_dt, end_dt)
    log.info("  Futures 1-min    : %d bars", len(futures_1m))

    daily_df = cache_load(NIFTY_SPOT_NAME, "ONE_DAY",
                          start_dt - timedelta(days=HTF_SMA_PERIOD * 2 + 30), end_dt)
    log.info("  NIFTY daily      : %d bars", len(daily_df))

    if spot_1m.empty or futures_1m.empty:
        log.critical("Cache lacks data for the requested window.  Re-run without --offline.")
        return 4

    # Run the sweep (vol_mult × min_or_range_pct)
    results = run_sweep(
        spot_1m=spot_1m, futures_1m=futures_1m, daily_df=daily_df,
        multipliers=mults, min_or_range_pcts=or_ranges,
        htf_on=not args.no_htf, dead_zone_on=not args.no_deadzone,
    )

    print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
