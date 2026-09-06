"""angel-one-quant-bundle  ▸  dashboard.py
==============================================================================
Complete Automation Bundle — Live Trading Terminal.

Six-pane single-page Streamlit app.  Top to bottom:

    ┌─────────────────────────────────────────────────────────────────────┐
    │  0.  MODE BANNER       LIVE 🔴  or  PAPER 🟡  (full-width hero strip)│
    ├─────────────────────────────────────────────────────────────────────┤
    │  1.  Live Ingestion Terminal                                        │
    │      scrolling tick feed off SmartWebSocketV2                       │
    ├─────────────────────────────────────────────────────────────────────┤
    │  2.  Capital Silo Matrix         (3 cards — same as Offer A)        │
    ├─────────────────────────────────────────────────────────────────────┤
    │  3.  Sizing Engine Indicator     (next-order parameters)            │
    ├─────────────────────────────────────────────────────────────────────┤
    │  4.  Execution — Order Tape      (every broker call, last 20)       │
    │      [HH:MM:SS]  PLACE  MARKET BUY  NIFTY 22400 CE  75q  fill₹152.40│
    │      [HH:MM:SS]  EXIT   MARKET SELL NIFTY 22400 CE  75q  fill₹163.20│
    ├─────────────────────────────────────────────────────────────────────┤
    │  5.  Execution — Open Positions  (live mark-to-market table)        │
    │      Symbol             Side  Qty  Entry    LTP      P&L     SL    │
    │      NIFTY 22400 CE     BUY   75   152.40   158.10   +427.50 145.0│
    ├─────────────────────────────────────────────────────────────────────┤
    │  6.  Recent Webhook Alerts       (last 10 — TG/Discord identical)   │
    └─────────────────────────────────────────────────────────────────────┘

Key difference vs Offer A
─────────────────────────
This dashboard is execution-AWARE.  Strategy code can call:

    execution.place_market(...) → dashboard.push_order(...)
    execution.fetch_ltp(...)    → mark-to-market in pane 5

…and the dashboard reflects every broker call within ~2s.  In Offer A
those panes don't exist because there's no execution engine.

USAGE
─────
    pip install streamlit                 # (already in requirements.txt)
    streamlit run src/dashboard.py

DEMO MODE
─────────
Set the env var ``OFFERA_DEMO=true`` (same flag name as the alerts-only
build to keep deployment ergonomics consistent) and the dashboard
self-feeds synthetic ticks, orders, and alerts so you never have to
record a Loom against an empty UI.
"""
from __future__ import annotations

import os
import random
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# ──────────────────────────────────────────────────────────────────────────
# Make `from src import ...` work whether streamlit runs this file as
# __main__ or as src.dashboard.
# ──────────────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
BUNDLE_ROOT = HERE.parent
if str(BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(BUNDLE_ROOT))

try:
    import streamlit as st
except ImportError as exc:                                            # pragma: no cover
    raise SystemExit(
        "streamlit not installed — `pip install streamlit` first."
    ) from exc

from src import config, alerts, risk

IST = timezone(timedelta(hours=5, minutes=30))


# ════════════════════════════════════════════════════════════════════════════
#  STATE
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class _Position:
    symbol: str
    side: str          # "BUY" or "SELL"
    qty: int
    entry: float
    ltp: float
    sl: float
    order_id: str
    silo: str

    @property
    def pnl(self) -> float:
        sign = 1.0 if self.side == "BUY" else -1.0
        return (self.ltp - self.entry) * self.qty * sign


def _init_state() -> None:
    if "tick_buffer" not in st.session_state:
        st.session_state.tick_buffer = deque(maxlen=200)
    if "order_tape" not in st.session_state:
        st.session_state.order_tape = deque(maxlen=20)
    if "open_positions" not in st.session_state:
        st.session_state.open_positions = {}    # order_id -> _Position
    if "alert_buffer" not in st.session_state:
        st.session_state.alert_buffer = deque(maxlen=20)
    if "risk_mgr" not in st.session_state:
        st.session_state.risk_mgr = risk.RiskManager.from_config()
    if "demo_state" not in st.session_state:
        st.session_state.demo_state = {
            "NIFTY":     22400.00,
            "BANKNIFTY": 48200.00,
            "frame":     0,
            "next_oid":  1,
        }


def _is_demo() -> bool:
    return os.environ.get("OFFERA_DEMO", "").lower() in ("1", "true", "yes")


def _safe_render(label: str):
    """Context-manager that turns a crash inside a render block into a
    contained red `[RENDER ERROR]` card instead of a 500 page.

    Each pane in `render()` is wrapped with ``with _safe_render('Pane'):``
    so a single bad row from the EC2 adapter, a malformed alert, or a
    transient SQLite read can't take down the whole dashboard.  The
    operator sees an inline error chip showing the exception type,
    message, and (collapsible) traceback — still actionable, never fatal.
    """
    import contextlib, traceback

    @contextlib.contextmanager
    def _ctx():
        try:
            yield
        except Exception as exc:                                       # noqa: BLE001
            tb = traceback.format_exc(limit=4)
            st.markdown(
                f"<div style='border:1px solid #dc2626;border-radius:6px;"
                f"padding:12px;background:rgba(127,29,29,0.18);"
                f"color:#fecaca;font-family:ui-monospace,Menlo,monospace;"
                f"font-size:12px;line-height:1.45;margin:6px 0'>"
                f"<b style='color:#f87171'>[RENDER ERROR] {label}</b><br>"
                f"{type(exc).__name__}: {exc}<br>"
                f"<details><summary style='cursor:pointer;color:#fca5a5'>"
                f"traceback</summary><pre style='white-space:pre-wrap;"
                f"margin:6px 0 0;color:#fda4af'>{tb}</pre></details>"
                f"</div>",
                unsafe_allow_html=True,
            )

    return _ctx()


def _silo_pill(silo: risk.CapitalSilo) -> tuple[str, str]:
    if silo.is_locked:
        return "🔴", "#dc2626"
    cap_inr = silo.max_capital * silo.daily_drawdown_pct
    used = abs(min(silo.total_pnl_today, 0.0))
    util = used / cap_inr if cap_inr > 0 else 0.0
    if util >= 0.5:
        return "🟡", "#f59e0b"
    return "🟢", "#10b981"


# ════════════════════════════════════════════════════════════════════════════
#  DEMO INJECTOR  (only runs when OFFERA_DEMO=true)
# ════════════════════════════════════════════════════════════════════════════
def _demo_step() -> None:
    """One frame of synthetic activity.  Pushes ticks every rerun, and on
    specific frame numbers triggers orders / silo commits / alerts / fills
    so the dashboard never sits empty during a sales walkthrough."""
    ds = st.session_state.demo_state
    rng = random.Random(ds["frame"] * 7919 + 13)
    ds["frame"] += 1
    rm = st.session_state.risk_mgr

    # ── 1. Two synthetic ticks per refresh ─────────────────────────────
    for sym in ("NIFTY", "BANKNIFTY"):
        prev = ds[sym]
        delta = rng.gauss(0.0, prev * 0.0008)
        ds[sym] = max(0.05, prev + delta)
        ts = datetime.now(IST).strftime("%H:%M:%S.%f")[:-3]
        push_tick(ts, sym, ds[sym], delta)

    # ── 2. Mark-to-market every open position against the latest tick ──
    for pos in st.session_state.open_positions.values():
        # nudge LTP by ±0.4% of entry to simulate intra-trade drift
        drift = rng.gauss(pos.entry * 0.002, pos.entry * 0.004)
        pos.ltp = max(0.05, pos.ltp + drift)

    # ── 3. Frame-keyed scripted events ─────────────────────────────────
    if ds["frame"] == 3:
        # Strategy fires an alpha signal alert
        a = alerts.build_strategy_match(
            asset="NIFTY 22400 CE",
            indicator="Opening-Range Breakout (15-min)",
            volatility_pct=1.85,
            position_size="75 Qty (1 Lot)",
            direction="LONG",
        )
        alerts.dispatch(a); push_alert(a)
        push_sizing(qty=75, lots=1, vol_pct=1.85, risk_inr=3_500.0)

    if ds["frame"] == 4:
        # Risk engine commits capital
        deriv = rm.silo(config.SILO_C_NAME)
        oid = f"PAPER-{ds['next_oid']:06d}"
        ds["next_oid"] += 1
        try:
            deriv.commit(position_id=oid, cost=11_430.0)
        except risk.RiskRefusedError:
            pass

        # Execution engine "places" a market order
        entry = 152.40
        push_order(
            ts=datetime.now(IST).strftime("%H:%M:%S"),
            verb="PLACE", op="MARKET BUY",
            symbol="NIFTY 22400 CE", qty=75,
            fill=entry, oid=oid,
        )
        # Order-filled webhook
        a = alerts.build_order_filled(
            side="BUY", symbol="NIFTY 22400 CE", quantity=75,
            fill_price=entry, order_id=oid, mode="PAPER",
            silo=deriv.name,
        )
        alerts.dispatch(a); push_alert(a)

        # Track open position
        st.session_state.open_positions[oid] = _Position(
            symbol="NIFTY 22400 CE", side="BUY", qty=75,
            entry=entry, ltp=entry, sl=entry * 0.92,
            order_id=oid, silo=deriv.name,
        )

    if ds["frame"] == 11:
        # Free-ride trailing stop arms after +5% drift → ratchets SL up
        for pos in st.session_state.open_positions.values():
            gain = (pos.ltp - pos.entry) / pos.entry
            if gain >= 0.05:
                new_sl = pos.ltp * (1 - config.TRAIL_STEP_PCT)
                if new_sl > pos.sl:
                    pos.sl = new_sl

    if ds["frame"] == 14:
        # Simulated WS drop alert
        a = alerts.build_engine_status(
            reconnect_attempt=2,
            max_reconnects=config.WS_MAX_RECONNECTS,
            backoff_seconds=config.WS_RECONNECT_BACKOFF * 2,
            reason="Demo: simulated upstream idle timeout",
        )
        alerts.dispatch(a); push_alert(a)

    if ds["frame"] == 18 and st.session_state.open_positions:
        # Take-profit exit on the open position
        oid, pos = next(iter(st.session_state.open_positions.items()))
        exit_px = pos.entry * 1.075
        pos.ltp = exit_px
        push_order(
            ts=datetime.now(IST).strftime("%H:%M:%S"),
            verb="EXIT", op="MARKET SELL",
            symbol=pos.symbol, qty=pos.qty,
            fill=exit_px, oid=oid + "-X",
        )
        # Realise PnL on the silo
        realised = (exit_px - pos.entry) * pos.qty
        deriv = rm.silo(pos.silo)
        try:
            deriv.release(position_id=oid, realised_pnl=realised)
        except Exception:                                              # noqa: BLE001
            pass
        # Order-filled exit alert
        a = alerts.build_order_filled(
            side="SELL", symbol=pos.symbol, quantity=pos.qty,
            fill_price=exit_px, order_id=oid + "-X", mode="PAPER",
            silo=pos.silo,
        )
        alerts.dispatch(a); push_alert(a)
        del st.session_state.open_positions[oid]

    if ds["frame"] == 24:
        # Walk DERIV past its daily DD cap with a synthetic large loss
        deriv = rm.silo(config.SILO_C_NAME)
        cap_inr = deriv.max_capital * deriv.daily_drawdown_pct
        breach = -(cap_inr * 1.05)
        # Inject a faux position just to drive the DD math
        forced_oid = f"FORCE-{ds['next_oid']:06d}"
        ds["next_oid"] += 1
        try:
            deriv.commit(position_id=forced_oid, cost=1.0)
            deriv.update_unrealised(position_id=forced_oid, pnl=breach)
        except Exception:                                              # noqa: BLE001
            pass
        if deriv.is_locked:
            a = alerts.build_critical_risk(
                silo_id=f"{deriv.name}_DEMO",
                realised_drawdown=breach,
                hard_threshold=-cap_inr,
            )
            alerts.dispatch(a); push_alert(a)

    if ds["frame"] == 30:
        locked = [s.name for s in (
            rm.silo(config.SILO_A_NAME),
            rm.silo(config.SILO_B_NAME),
            rm.silo(config.SILO_C_NAME),
        ) if s.is_locked]
        if not locked:
            locked = ["ALL"]
        a = alerts.build_compliance_lock(
            session_pnl=sum(s.realised_pnl_today for s in (
                rm.silo(config.SILO_A_NAME),
                rm.silo(config.SILO_B_NAME),
                rm.silo(config.SILO_C_NAME),
            )),
            locked_silos=locked,
        )
        alerts.dispatch(a); push_alert(a)


# ════════════════════════════════════════════════════════════════════════════
#  LAYOUT
# ════════════════════════════════════════════════════════════════════════════
def _ec2_adapter_enabled() -> bool:
    """Toggle for the live-EC2 polling adapter.  OFF by default so local
    runs and demo mode stay deterministic — flip ``EC2_ADAPTER_ENABLE``
    in the PM2 ecosystem config to bridge the dashboard to live bots."""
    return os.environ.get("EC2_ADAPTER_ENABLE", "").lower() in ("1", "true", "yes")


@st.cache_resource(show_spinner=False)
def _ensure_ec2_adapter() -> Optional[Any]:
    """Spin the EC2 adapter exactly once across all Streamlit reruns.

    `@st.cache_resource` is the official idiom for boot-once singletons
    (DB pools, ML models, background threads).  The result is cached at
    process scope, so even though `render()` is invoked on every script
    rerun the adapter thread is created exactly once.

    Returns ``None`` if the adapter is disabled OR if the import fails
    (e.g. running against a non-EC2 checkout where pm2 / SQLite paths
    don't exist).  The dashboard continues to render — just without the
    live data feed.
    """
    if not _ec2_adapter_enabled():
        return None
    try:
        # Absolute import — works when streamlit runs this file as __main__
        # (it adds BUNDLE_ROOT to sys.path at module load, see top of file).
        from src import ec2_adapter
        adapter = ec2_adapter.EC2Adapter()
        adapter.start()
        return adapter
    except Exception:                                                  # noqa: BLE001
        import logging, traceback
        logging.getLogger("dashboard").warning(
            "EC2 adapter failed to start:\n%s", traceback.format_exc()
        )
        return None


def render() -> None:
    st.set_page_config(
        page_title=f"{config.ALERT_BRAND_NAME} — Complete Automation",
        layout="wide",
    )
    _init_state()

    # Boot the EC2 adapter on first render (cached singleton).  Wrapped in
    # _safe_render so adapter-init errors don't kill the dashboard boot.
    adapter = None
    with _safe_render("EC2 Adapter Boot"):
        adapter = _ensure_ec2_adapter()

    # ── 0. MODE banner (hero strip) ─────────────────────────────────────
    is_paper = config.PAPER_TRADING
    banner_bg = "#fbbf24" if is_paper else "#dc2626"
    banner_fg = "#1f2937" if is_paper else "#ffffff"
    banner_label = "🟡 PAPER MODE — orders simulated, no broker calls" if is_paper \
                   else "🔴 LIVE MODE — REAL ORDERS ARE BEING PLACED"
    st.markdown(
        f"<div style='background:{banner_bg};color:{banner_fg};"
        f"padding:10px 16px;border-radius:6px;font-weight:600;"
        f"text-align:center;font-size:15px;margin-bottom:14px'>"
        f"{banner_label}</div>",
        unsafe_allow_html=True,
    )

    st.markdown(f"### {config.ALERT_BRAND_NAME}  ·  Complete Automation Terminal")
    demo_badge = "  ·  🟣 **DEMO**" if _is_demo() else ""
    ec2_badge = "  ·  🛰️ **EC2 LIVE**" if adapter is not None else ""
    st.caption(
        f"Mode: **{'PAPER' if is_paper else 'LIVE'}**{demo_badge}{ec2_badge}   ·   "
        f"Brand: `{config.ALERT_BRAND_NAME}`   ·   "
        f"Session: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    if _is_demo():
        with _safe_render("Demo Injector"):
            _demo_step()

    # ── 1. Live Ingestion Terminal ──────────────────────────────────────
    st.markdown("#### 1.  Live Ingestion Terminal")
    with _safe_render("Live Ingestion Terminal"):
        if st.session_state.tick_buffer:
            buf = list(st.session_state.tick_buffer)[-15:][::-1]
            st.code("\n".join(buf), language=None)
        else:
            st.info("Waiting for ticks — wire `src.ingestion.TickFeed` (or the EC2 adapter) to push into state.")

    # ── 2. Capital Silo Matrix ──────────────────────────────────────────
    st.markdown("#### 2.  Capital Silo Matrix")
    with _safe_render("Capital Silo Matrix"):
        cols = st.columns(3)
        rm = st.session_state.risk_mgr
        for col, silo_name in zip(cols, [config.SILO_A_NAME, config.SILO_B_NAME, config.SILO_C_NAME]):
            silo = rm.silo(silo_name)
            emoji, hex_colour = _silo_pill(silo)
            with col:
                st.markdown(
                    f"<div style='border:1px solid {hex_colour};border-radius:6px;"
                    f"padding:14px;background:rgba(0,0,0,0.02)'>"
                    f"<div style='font-size:13px;color:#666'>{silo.name}</div>"
                    f"<div style='font-size:24px;font-weight:600'>{emoji} "
                    f"{'LOCKED' if silo.is_locked else 'ACTIVE'}</div>"
                    f"<hr style='margin:8px 0;border:none;border-top:1px solid #eee'/>"
                    f"<div>Max Capital:  <b>₹{silo.max_capital:,.0f}</b></div>"
                    f"<div>Deployed:     <b>₹{silo.deployed_capital:,.0f}</b></div>"
                    f"<div>Available:    <b>₹{silo.available_capital:,.0f}</b></div>"
                    f"<div>Daily P&amp;L: <b>₹{silo.total_pnl_today:,.2f}</b></div>"
                    f"<div>DD Cap:       <b>₹{-silo.max_capital * silo.daily_drawdown_pct:,.0f}</b></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    # ── 3. Sizing Engine Indicator ──────────────────────────────────────
    st.markdown("#### 3.  Sizing Engine Indicator")
    with _safe_render("Sizing Engine Indicator"):
        s = st.session_state.get("sizing")
        if s:
            st.markdown(
                f"<div style='border:1px solid #3b82f6;border-radius:6px;padding:14px;"
                f"background:rgba(59,130,246,0.05)'>"
                f"<b>Target Deployment:</b> {s['qty']} Qty "
                f"({s['lots']} Lot{'s' if s['lots']!=1 else ''})<br>"
                f"<b>Volatility 5-min:</b>  {s['vol_pct']:.2f}%<br>"
                f"<b>Risk Budget:</b>       ₹{s['risk_inr']:,.2f}<br>"
                f"<b>Status:</b>            🟢 RISK LIMIT ACTIVE"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("No active sizing decision yet — strategy populates this via push_sizing().")

    # ── 4. Execution — Order Tape ───────────────────────────────────────
    st.markdown("#### 4.  Execution — Order Tape")
    with _safe_render("Execution — Order Tape"):
        if st.session_state.order_tape:
            st.code("\n".join(list(st.session_state.order_tape)[::-1]), language=None)
        else:
            st.info("No orders placed in this session yet.")

    # ── 5. Execution — Open Positions ───────────────────────────────────
    st.markdown("#### 5.  Execution — Open Positions")
    with _safe_render("Execution — Open Positions"):
        positions = list(st.session_state.open_positions.values())
        if positions:
            rows = []
            for p in positions:
                pnl_pct = (p.pnl / (p.entry * p.qty)) * 100 if p.entry > 0 else 0.0
                rows.append({
                    "Symbol":   p.symbol,
                    "Side":     p.side,
                    "Qty":      p.qty,
                    "Entry":    f"₹{p.entry:,.2f}",
                    "LTP":      f"₹{p.ltp:,.2f}",
                    "P&L":      f"₹{p.pnl:+,.2f} ({pnl_pct:+.2f}%)",
                    "Trail SL": f"₹{p.sl:,.2f}",
                    "Silo":     p.silo,
                    "Order ID": p.order_id,
                })
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.caption("No open positions.  Active commits show here mid-trade.")

    # ── 6. Recent Webhook Alerts ────────────────────────────────────────
    st.markdown("#### 6.  Recent Webhook Alerts")
    with _safe_render("Recent Webhook Alerts"):
        if st.session_state.alert_buffer:
            for a in list(st.session_state.alert_buffer)[-8:][::-1]:
                st.code(a.render_text(colour=False), language=None)
        else:
            st.caption("No alerts dispatched yet.")

    # Auto-refresh
    time.sleep(2)
    st.rerun()


# ════════════════════════════════════════════════════════════════════════════
#  HOOKS  —  call these from your strategy / execution loop
# ════════════════════════════════════════════════════════════════════════════
def push_tick(ts: str, symbol: str, ltp: float, delta: float = 0.0) -> None:
    if "tick_buffer" not in st.session_state:
        _init_state()
    line = f"[{ts}]  TICK  {symbol:<12}  LTP ₹{ltp:>10,.2f}  Δ {delta:+.2f}"
    st.session_state.tick_buffer.append(line)


def push_order(*, ts: str, verb: str, op: str, symbol: str,
               qty: int, fill: float, oid: str) -> None:
    """Append one line to the Order Tape.

    `verb`  — 'PLACE' | 'EXIT' | 'MODIFY' | 'CANCEL'
    `op`    — 'MARKET BUY' | 'MARKET SELL' | 'LIMIT BUY' | 'SL BUY' | …
    """
    if "order_tape" not in st.session_state:
        _init_state()
    line = (
        f"[{ts}]  {verb:<6}  {op:<12}  {symbol:<18}  "
        f"{qty:>4}q  fill ₹{fill:>8,.2f}  {oid}"
    )
    st.session_state.order_tape.append(line)


def push_alert(a: alerts.Alert) -> None:
    if "alert_buffer" not in st.session_state:
        _init_state()
    st.session_state.alert_buffer.append(a)


def push_sizing(*, qty: int, lots: int, vol_pct: float, risk_inr: float) -> None:
    st.session_state.sizing = {
        "qty": qty, "lots": lots, "vol_pct": vol_pct, "risk_inr": risk_inr,
    }


if __name__ == "__main__":
    render()
