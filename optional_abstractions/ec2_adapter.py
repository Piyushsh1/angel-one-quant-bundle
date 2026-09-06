"""angel-one-quant-bundle  ▸  ec2_adapter.py
==============================================================================
Production observation deck — bridge live PM2 bots to the dashboard.

WHY THIS EXISTS
───────────────
The dashboard renders from in-process Python state.  On a live EC2 box
the three trading bots (barbell-macro, barbell-smallcap, barbell-options)
run in SEPARATE Python processes under PM2 and persist their state in
three independent SQLite databases.  Streamlit cannot peek into those
processes.

This adapter is the bridge.  It runs as a background daemon thread inside
the Streamlit process and:

    1.  Polls each bot's SQLite database in READ-ONLY mode (`mode=ro`)
        with a short busy-timeout, so it can NEVER lock a live bot mid-
        write.
    2.  Reads PM2 status via `pm2 jlist` (subprocess), extracting LIVE/
        PAPER mode, uptime, restart counts.
    3.  Tails the last N lines of each bot's PM2 log to spot WS
        reconnects and 429 API pacing warnings.
    4.  Translates everything into the dashboard's existing `push_*`
        hooks — zero changes required to the UI layer.

DESIGN INVARIANTS
─────────────────
*  Adapter NEVER mutates broker state, the bot's SQLite, or any file
   under PM2_HOME.  Read-only end-to-end.
*  Adapter NEVER blocks the Streamlit render thread.  All work happens
   on a daemon thread.  Subprocess + SQLite calls have timeouts.
*  Adapter NEVER raises.  Any exception is logged and the loop continues.
   A flaky pm2 invocation must not blank the dashboard.
*  Schema-tolerant: SQLite columns are auto-discovered via PRAGMA so the
   adapter survives the minor schema variations between the three bots.

USAGE
─────
Enable via env vars (typically set in PM2 ecosystem.config.js):

    EC2_ADAPTER_ENABLE=true
    EC2_BOT_HOME=/home/ubuntu/algo-barbell           # default
    EC2_PM2_LOG_DIR=/home/ubuntu/.pm2/logs           # default
    EC2_POLL_INTERVAL_SECONDS=2.0                    # default
    EC2_ADAPTER_LOG_LEVEL=INFO                       # default

Then:

    streamlit run src/dashboard.py

The dashboard auto-starts the adapter on first render (via
`@st.cache_resource` so it boots exactly once across all Streamlit
script reruns).

For local testing without an EC2 box:

    python -m src.ec2_adapter --self-check    # prints what it WOULD do
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from . import alerts, config

log = logging.getLogger("ec2_adapter")
IST = timezone(timedelta(hours=5, minutes=30))


# ════════════════════════════════════════════════════════════════════════════
#  CONFIG  (env-driven, overridable per-instance)
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class BotMapping:
    """One row in the silo↔bot↔db mapping table."""
    pm2_name:    str             # e.g. "barbell-macro"
    silo_name:   str             # one of config.SILO_{A,B,C}_NAME
    db_filename: str             # relative to EC2_BOT_HOME
    label:       str             # human label for alert payloads


def _default_mappings() -> list[BotMapping]:
    """The canonical wiring for the production EC2 trio.  Override by
    constructing your own list and passing to ``EC2Adapter(...)``."""
    return [
        BotMapping("barbell-macro",    config.SILO_A_NAME, "macro_state.db",
                   "Macro Trend Engine"),
        BotMapping("barbell-smallcap", config.SILO_B_NAME, "smallcap_state.db",
                   "Small-Cap Swing Engine"),
        BotMapping("barbell-options",  config.SILO_C_NAME, "options_state.db",
                   "Options Predator Engine"),
    ]


@dataclass
class AdapterConfig:
    bot_home:      Path  = field(
        default_factory=lambda: Path(os.environ.get(
            "EC2_BOT_HOME", "/home/ubuntu/algo-barbell")))
    pm2_log_dir:   Path  = field(
        default_factory=lambda: Path(os.environ.get(
            "EC2_PM2_LOG_DIR", "/home/ubuntu/.pm2/logs")))
    poll_interval: float = field(
        default_factory=lambda: float(os.environ.get(
            "EC2_POLL_INTERVAL_SECONDS", "2.0")))
    pm2_binary:    str   = field(
        default_factory=lambda: os.environ.get("EC2_PM2_BINARY", "pm2"))
    log_tail_lines: int  = field(
        default_factory=lambda: int(os.environ.get(
            "EC2_LOG_TAIL_LINES", "50")))
    subprocess_timeout: float = field(
        default_factory=lambda: float(os.environ.get(
            "EC2_SUBPROCESS_TIMEOUT", "3.0")))


# ════════════════════════════════════════════════════════════════════════════
#  READ-ONLY SQLITE
# ════════════════════════════════════════════════════════════════════════════
def _open_ro(db_path: Path, *, timeout: float = 1.0) -> sqlite3.Connection:
    """Open a SQLite DB in READ-ONLY mode via URI.

    `mode=ro` tells SQLite to refuse any write, AND to use a less
    aggressive locking strategy that won't interfere with the live
    writer process.  `busy_timeout` is set to a small value so a poll
    that lands during a writer's commit window will retry briefly and
    then give up — failure here is non-fatal, the next poll will pick
    up the data.

    Raises sqlite3.OperationalError if the file doesn't exist (we don't
    create it — that would defeat read-only intent).
    """
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite DB missing: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=timeout, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 500")           # 500 ms upper bound
    return con


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def _first_existing(candidates: list[str], have: set[str]) -> Optional[str]:
    """Return the first candidate column name that actually exists.
    Used so schema variations don't crash the polls."""
    for c in candidates:
        if c in have:
            return c
    return None


# ════════════════════════════════════════════════════════════════════════════
#  PER-BOT POLLERS
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class _BotSnapshot:
    """Output of one poll cycle for one bot.  Adapter translates this
    into push_* calls."""
    mapping:        BotMapping
    daily_pnl:      float = 0.0
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    recent_trades:  list[dict[str, Any]] = field(default_factory=list)
    db_error:       str   = ""


def _poll_one_bot(mapping: BotMapping, cfg: AdapterConfig) -> _BotSnapshot:
    """Poll a single bot's SQLite DB.  Returns a snapshot; on any error
    fills ``db_error`` with a short string and leaves other fields empty.
    """
    snap = _BotSnapshot(mapping=mapping)
    db_path = cfg.bot_home / mapping.db_filename
    try:
        con = _open_ro(db_path)
    except (FileNotFoundError, sqlite3.OperationalError) as exc:
        snap.db_error = f"db open: {exc}"
        return snap

    try:
        tables = _tables(con)

        # ── 1. Open positions ────────────────────────────────────────
        if "open_positions" in tables:
            try:
                cols = _columns(con, "open_positions")
                # Auto-discover the timestamp column — different bots use
                # different names (entry_time / created_at / ts / opened_at).
                ts_col = _first_existing(
                    ["entry_time", "created_at", "opened_at", "ts", "timestamp"],
                    cols,
                ) or "rowid"
                sym_col = _first_existing(
                    ["symbol", "tradingsymbol", "instrument"], cols,
                ) or "rowid"
                qty_col   = _first_existing(["qty", "quantity", "lots"], cols)
                entry_col = _first_existing(
                    ["entry_price", "avg_price", "price", "entry"], cols)
                side_col  = _first_existing(["side", "direction"], cols)
                sl_col    = _first_existing(
                    ["sl", "stop_loss", "trail_sl", "hard_sl"], cols)
                oid_col   = _first_existing(
                    ["order_id", "orderid", "broker_order_id"], cols)

                projection = ", ".join(c for c in [
                    sym_col, ts_col, qty_col, entry_col,
                    side_col, sl_col, oid_col,
                ] if c)
                rows = con.execute(
                    f"SELECT {projection} FROM open_positions "
                    f"ORDER BY {ts_col} DESC LIMIT 50"
                ).fetchall()
                for row in rows:
                    d = dict(row)
                    snap.open_positions.append({
                        "symbol":   str(d.get(sym_col, "")),
                        "qty":      int(d.get(qty_col, 0) or 0) if qty_col else 0,
                        "entry":    float(d.get(entry_col, 0.0) or 0.0) if entry_col else 0.0,
                        "side":     str(d.get(side_col, "BUY") if side_col else "BUY").upper(),
                        "sl":       float(d.get(sl_col, 0.0) or 0.0) if sl_col else 0.0,
                        "order_id": str(d.get(oid_col, "") if oid_col else ""),
                        "ts":       str(d.get(ts_col, "")) if ts_col != "rowid" else "",
                    })
            except sqlite3.Error as exc:
                log.warning("[%s] open_positions read: %s", mapping.pm2_name, exc)

        # ── 2. Recent trades / order tape ────────────────────────────
        # Each bot may name this table differently — try common variants.
        trade_table = None
        for candidate in ("trade_history", "trades", "order_history", "fills"):
            if candidate in tables:
                trade_table = candidate
                break
        if trade_table:
            try:
                cols = _columns(con, trade_table)
                ts_col = _first_existing(
                    ["ts", "timestamp", "created_at", "entry_time", "exit_time"],
                    cols,
                ) or "rowid"
                sym_col = _first_existing(
                    ["symbol", "tradingsymbol", "instrument"], cols,
                ) or "rowid"
                qty_col   = _first_existing(["qty", "quantity"], cols)
                px_col    = _first_existing(
                    ["price", "fill_price", "avg_price"], cols)
                side_col  = _first_existing(["side", "direction"], cols)
                oid_col   = _first_existing(
                    ["order_id", "orderid", "broker_order_id"], cols)
                projection = ", ".join(c for c in [
                    ts_col, sym_col, qty_col, px_col, side_col, oid_col,
                ] if c)
                rows = con.execute(
                    f"SELECT {projection} FROM {trade_table} "
                    f"ORDER BY {ts_col} DESC LIMIT 20"
                ).fetchall()
                for row in rows:
                    d = dict(row)
                    snap.recent_trades.append({
                        "ts":       str(d.get(ts_col, "")) if ts_col != "rowid" else "",
                        "symbol":   str(d.get(sym_col, "")),
                        "qty":      int(d.get(qty_col, 0) or 0) if qty_col else 0,
                        "price":    float(d.get(px_col, 0.0) or 0.0) if px_col else 0.0,
                        "side":     str(d.get(side_col, "BUY") if side_col else "BUY").upper(),
                        "order_id": str(d.get(oid_col, "") if oid_col else ""),
                    })
            except sqlite3.Error as exc:
                log.warning("[%s] %s read: %s", mapping.pm2_name, trade_table, exc)

        # ── 3. Daily PnL ─────────────────────────────────────────────
        # Some bots persist a `daily_pnl` table; others compute it from
        # trades.  Try the table first.
        if "daily_pnl" in tables:
            try:
                today = datetime.now(IST).strftime("%Y-%m-%d")
                cols = _columns(con, "daily_pnl")
                pnl_col = _first_existing(["pnl", "realised_pnl", "net_pnl"], cols)
                date_col = _first_existing(["date", "session_date", "ts"], cols)
                if pnl_col and date_col:
                    row = con.execute(
                        f"SELECT {pnl_col} FROM daily_pnl "
                        f"WHERE date({date_col})=? LIMIT 1",
                        (today,),
                    ).fetchone()
                    if row is not None:
                        snap.daily_pnl = float(row[0] or 0.0)
            except sqlite3.Error as exc:
                log.warning("[%s] daily_pnl read: %s", mapping.pm2_name, exc)
        # Fallback: sum of today's trades.
        if snap.daily_pnl == 0.0 and snap.recent_trades:
            today = datetime.now(IST).strftime("%Y-%m-%d")
            snap.daily_pnl = sum(
                (t["price"] * t["qty"] * (1 if t["side"] == "SELL" else -1))
                for t in snap.recent_trades
                if today in t.get("ts", "")
            )
    finally:
        con.close()
    return snap


# ════════════════════════════════════════════════════════════════════════════
#  PM2 TELEMETRY
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class _Pm2Process:
    name:          str
    status:        str       # "online" | "stopped" | "errored"
    restart_count: int
    uptime_sec:    int
    cpu_pct:       float
    mem_bytes:     int
    paper_trading: Optional[bool] = None   # parsed from env if PM2_HOME exposes it


def _read_pm2_jlist(cfg: AdapterConfig) -> list[_Pm2Process]:
    """Run `pm2 jlist` and parse the JSON list.  Bounded subprocess timeout."""
    try:
        proc = subprocess.run(
            [cfg.pm2_binary, "jlist"],
            capture_output=True, text=True,
            timeout=cfg.subprocess_timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("pm2 jlist failed: %s", exc)
        return []
    if proc.returncode != 0:
        log.warning("pm2 jlist exit=%d stderr=%s",
                    proc.returncode, proc.stderr.strip())
        return []
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        log.warning("pm2 jlist not JSON: %s", exc)
        return []

    out: list[_Pm2Process] = []
    for entry in data:
        try:
            mon = entry.get("monit") or {}
            ps2 = entry.get("pm2_env") or {}
            env = ps2.get("env") or {}
            # PAPER_TRADING env can be missing / "true" / True / "1"; tolerate all.
            pt_raw = env.get("PAPER_TRADING")
            paper = None
            if isinstance(pt_raw, bool):
                paper = pt_raw
            elif isinstance(pt_raw, str):
                paper = pt_raw.lower() in ("1", "true", "yes")
            # Uptime: PM2 reports `pm_uptime` as a unix ms timestamp of the
            # last start; compute seconds since.
            uptime_ms = ps2.get("pm_uptime") or 0
            uptime_sec = (
                int((time.time() * 1000 - uptime_ms) / 1000)
                if uptime_ms else 0
            )
            out.append(_Pm2Process(
                name=str(entry.get("name", "")),
                status=str(ps2.get("status", "unknown")),
                restart_count=int(ps2.get("restart_time") or 0),
                uptime_sec=max(0, uptime_sec),
                cpu_pct=float(mon.get("cpu") or 0.0),
                mem_bytes=int(mon.get("memory") or 0),
                paper_trading=paper,
            ))
        except Exception as exc:                                       # noqa: BLE001
            log.warning("pm2 entry parse failed: %s", exc)
    return out


# ════════════════════════════════════════════════════════════════════════════
#  LOG TAILING
# ════════════════════════════════════════════════════════════════════════════
# Patterns we care about.  Compile once.
_RE_WS_RECONNECT = re.compile(
    r"WS will reconnect|websocket.*reconnect|WS_RECONNECT|on_close",
    re.IGNORECASE,
)
_RE_RATE_LIMIT   = re.compile(
    r"\b429\b|too many requests|rate.?limit|exceeding.*quota",
    re.IGNORECASE,
)
_RE_API_ERROR    = re.compile(
    r"\bAB\d{4}\b|invalid.*token|generateSession failed|UnauthorizedError",
    re.IGNORECASE,
)


def _tail_lines(path: Path, n: int) -> list[str]:
    """Memory-bounded tail of the last `n` lines.  Reads only the trailing
    bytes (not the whole file) for performance — PM2 logs can grow large."""
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
        approx_block = max(4096, n * 200)               # ~200 bytes/line guess
        with path.open("rb") as fh:
            if size > approx_block:
                fh.seek(size - approx_block)
                fh.readline()                           # discard partial line
            tail = fh.read().decode("utf-8", errors="replace").splitlines()
        return tail[-n:]
    except OSError as exc:
        log.warning("log tail failed for %s: %s", path, exc)
        return []


def _scan_log_signals(lines: list[str]) -> dict[str, int]:
    """Count occurrences of each signal pattern in the tailed lines."""
    counts = {"ws_reconnect": 0, "rate_limit": 0, "api_error": 0}
    for line in lines:
        if _RE_WS_RECONNECT.search(line): counts["ws_reconnect"] += 1
        if _RE_RATE_LIMIT.search(line):   counts["rate_limit"]   += 1
        if _RE_API_ERROR.search(line):    counts["api_error"]    += 1
    return counts


# ════════════════════════════════════════════════════════════════════════════
#  ADAPTER  (the public class)
# ════════════════════════════════════════════════════════════════════════════
class EC2Adapter:
    """Background daemon that drives the dashboard from live EC2 state.

    Construction is cheap — the thread starts lazily on ``start()``.  Use
    ``stop()`` for clean shutdown, but the thread is a daemon so it dies
    with the process anyway.
    """

    def __init__(
        self,
        *,
        mappings: Optional[list[BotMapping]] = None,
        cfg: Optional[AdapterConfig] = None,
    ) -> None:
        self.mappings = mappings or _default_mappings()
        self.cfg      = cfg or AdapterConfig()
        self._stop_evt = threading.Event()
        self._thread:  Optional[threading.Thread] = None

        # Per-bot "last seen" rowsets so we only push NEW trades / positions
        # to the dashboard instead of replaying everything on every poll.
        self._seen_trade_ids:  dict[str, set[str]] = {m.pm2_name: set() for m in self.mappings}
        self._seen_position_ids: dict[str, set[str]] = {m.pm2_name: set() for m in self.mappings}
        self._last_log_signal_alert: dict[str, float] = {}
        # Throttle: don't fire the same log-signal alert more than once per N seconds.
        self._log_alert_cooldown_sec = 60.0

    # ── lifecycle ───────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="EC2Adapter", daemon=True,
        )
        self._thread.start()
        log.info("EC2 adapter started — polling every %.1fs from %s",
                 self.cfg.poll_interval, self.cfg.bot_home)

    def stop(self, *, timeout: float = 3.0) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        log.info("EC2 adapter stopped")

    # ── main loop ───────────────────────────────────────────────────────
    def _run(self) -> None:
        """Forever-poll loop.  Each iteration is ENTIRELY wrapped in
        try/except — the adapter must never die from a transient error."""
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            try:
                self._poll_once()
            except Exception:                                          # noqa: BLE001
                log.exception("adapter poll cycle failed")
            elapsed = time.monotonic() - t0
            sleep_for = max(0.05, self.cfg.poll_interval - elapsed)
            self._stop_evt.wait(sleep_for)

    def _poll_once(self) -> None:
        # The dashboard hooks live in `src.dashboard`.  Import lazily so
        # this module can also be imported without streamlit available
        # (e.g. for the `--self-check` CLI).
        from src import dashboard

        # 1. PM2 telemetry → dashboard mode banner + engine status alerts
        pm2_state = _read_pm2_jlist(self.cfg)
        self._push_pm2(dashboard, pm2_state)

        # 2. Per-bot SQLite snapshot → silo PnL, positions, order tape
        for mp in self.mappings:
            snap = _poll_one_bot(mp, self.cfg)
            if snap.db_error:
                log.debug("[%s] %s", mp.pm2_name, snap.db_error)
                continue
            self._push_snapshot(dashboard, snap)

        # 3. Log tail → alerts on rate-limit / reconnect / API error spikes
        for mp in self.mappings:
            self._push_log_signals(dashboard, mp)

    # ── pushers ─────────────────────────────────────────────────────────
    def _push_pm2(self, dashboard: Any, processes: list[_Pm2Process]) -> None:
        """Surface PM2 state as a synthetic [ENGINE STATUS] alert when
        anything degrades (restart count rising, status != online)."""
        # Map by name for fast lookup
        by_name = {p.name: p for p in processes}
        for mp in self.mappings:
            p = by_name.get(mp.pm2_name)
            if p is None:
                continue                                # bot not in PM2 yet
            if p.status != "online":
                a = alerts.build_engine_status(
                    reconnect_attempt=p.restart_count,
                    max_reconnects=config.WS_MAX_RECONNECTS,
                    backoff_seconds=0.0,
                    reason=(f"PM2 process '{mp.pm2_name}' status="
                            f"{p.status} after {p.restart_count} restarts"),
                )
                self._safe_alert(dashboard, a, dedupe_key=f"pm2:{mp.pm2_name}:{p.status}")

    def _push_snapshot(self, dashboard: Any, snap: _BotSnapshot) -> None:
        """Drive the dashboard hooks from a single bot's snapshot."""
        # ── Update silo state in RiskManager via realised PnL ────────
        # We DON'T re-commit capital here (that would double-count the
        # bot's own commits).  We only sync `daily_pnl` so the silo
        # matrix card reflects the live trading day.
        try:
            import streamlit as st
            rm: Optional[Any] = st.session_state.get("risk_mgr")
        except Exception:                                              # noqa: BLE001
            rm = None
        if rm is not None and snap.daily_pnl:
            try:
                silo = rm.silo(snap.mapping.silo_name)
                # Simple model: assume the bot has one synthetic "session"
                # position that carries the realised PnL.  We commit a
                # nominal ₹1 cost and update its mark-to-market.
                pid = f"EC2_SESSION_{snap.mapping.silo_name}"
                if not getattr(silo, "_ec2_session_open", False):
                    try:
                        silo.commit(position_id=pid, cost=1.0)
                        silo._ec2_session_open = True                  # type: ignore[attr-defined]
                    except Exception:                                  # noqa: BLE001
                        pass
                silo.update_unrealised(position_id=pid, pnl=snap.daily_pnl)
            except Exception:                                          # noqa: BLE001
                log.exception("silo sync failed")

        # ── Open positions → dashboard `open_positions` dict ─────────
        try:
            import streamlit as st
            if hasattr(st, "session_state") and "open_positions" in st.session_state:
                from src.dashboard import _Position
                target = st.session_state.open_positions
                bot_oids = {p["order_id"] or f"ec2-{i}" for i, p in
                            enumerate(snap.open_positions)}
                # Add / update
                for i, p in enumerate(snap.open_positions):
                    oid = p["order_id"] or f"ec2-{snap.mapping.pm2_name}-{i}"
                    existing = target.get(oid)
                    if existing is None:
                        target[oid] = _Position(
                            symbol=p["symbol"],   side=p["side"],
                            qty=p["qty"],         entry=p["entry"],
                            ltp=p["entry"],       sl=p["sl"],
                            order_id=oid,
                            silo=snap.mapping.silo_name,
                        )
                    else:
                        # Refresh price-sensitive fields only.
                        existing.sl    = p["sl"] or existing.sl
                # Reap closed positions for THIS bot (don't touch other bots').
                for oid in list(target.keys()):
                    if (target[oid].silo == snap.mapping.silo_name
                        and oid.startswith(("ec2-", "PAPER-"))
                        and oid not in bot_oids):
                        # was open on a previous poll but is now gone
                        del target[oid]
        except Exception:                                              # noqa: BLE001
            log.exception("open-positions sync failed")

        # ── Recent trades → Order Tape ───────────────────────────────
        seen = self._seen_trade_ids[snap.mapping.pm2_name]
        for t in snap.recent_trades:
            key = t["order_id"] or f"{t['ts']}|{t['symbol']}|{t['qty']}|{t['price']}"
            if key in seen:
                continue
            seen.add(key)
            ts_short = (t["ts"][-8:] if t["ts"] else
                        datetime.now(IST).strftime("%H:%M:%S"))
            try:
                dashboard.push_order(
                    ts=ts_short,
                    verb="FILL",
                    op=f"{t['side']:<6}".strip(),
                    symbol=t["symbol"][:18],
                    qty=t["qty"],
                    fill=t["price"],
                    oid=t["order_id"][:24] if t["order_id"] else "—",
                )
            except Exception:                                          # noqa: BLE001
                log.exception("push_order failed for %s", t)

        # Cap the seen-trade memory so we don't grow unbounded across
        # multi-day sessions.
        if len(seen) > 1000:
            self._seen_trade_ids[snap.mapping.pm2_name] = set(list(seen)[-500:])

    def _push_log_signals(self, dashboard: Any, mp: BotMapping) -> None:
        """Tail the bot's PM2 logs and surface anomalies as alerts."""
        for stream in ("out", "error"):
            path = self.cfg.pm2_log_dir / f"{mp.pm2_name}-{stream}.log"
            lines = _tail_lines(path, self.cfg.log_tail_lines)
            if not lines:
                continue
            signals = _scan_log_signals(lines)

            if signals["rate_limit"] >= 3:
                a = alerts.build_engine_status(
                    reconnect_attempt=0,
                    max_reconnects=config.WS_MAX_RECONNECTS,
                    backoff_seconds=config.API_PACING_SECONDS,
                    reason=(f"{mp.label}: {signals['rate_limit']}× "
                            f"HTTP 429 in last {self.cfg.log_tail_lines} log lines"),
                )
                self._safe_alert(dashboard, a, dedupe_key=f"log:{mp.pm2_name}:429")

            if signals["ws_reconnect"] >= 2:
                a = alerts.build_engine_status(
                    reconnect_attempt=signals["ws_reconnect"],
                    max_reconnects=config.WS_MAX_RECONNECTS,
                    backoff_seconds=config.WS_RECONNECT_BACKOFF,
                    reason=(f"{mp.label}: {signals['ws_reconnect']}× WS "
                            f"reconnects in last {self.cfg.log_tail_lines} lines"),
                )
                self._safe_alert(dashboard, a, dedupe_key=f"log:{mp.pm2_name}:ws")

            if signals["api_error"] >= 1:
                a = alerts.build_critical_risk(
                    silo_id=mp.silo_name,
                    realised_drawdown=0.0,
                    hard_threshold=0.0,
                )
                # Override the canned fields with log-specific context
                a.title = "BROKER API ERROR"
                a.fields = {
                    "Bot":     mp.label,
                    "Source":  f"{stream}.log (last {self.cfg.log_tail_lines} lines)",
                    "Count":   str(signals["api_error"]),
                    "Action":  "Check pm2 logs " + mp.pm2_name,
                }
                self._safe_alert(dashboard, a, dedupe_key=f"log:{mp.pm2_name}:api")

    def _safe_alert(self, dashboard: Any, alert: alerts.Alert,
                    *, dedupe_key: str) -> None:
        """Push an alert with cooldown so a stuck-on log signal doesn't
        spam the dashboard or the operator's Telegram."""
        now = time.monotonic()
        last = self._last_log_signal_alert.get(dedupe_key, 0.0)
        if now - last < self._log_alert_cooldown_sec:
            return
        self._last_log_signal_alert[dedupe_key] = now
        try:
            alerts.dispatch(alert)
            dashboard.push_alert(alert)
        except Exception:                                              # noqa: BLE001
            log.exception("dispatch failed for %s", dedupe_key)


# ════════════════════════════════════════════════════════════════════════════
#  SELF-CHECK
# ════════════════════════════════════════════════════════════════════════════
def _self_check() -> int:
    """Dry-run the adapter once and print what it WOULD push.  Used to
    validate paths and SQLite schemas on a fresh deploy before exposing
    the dashboard port."""
    logging.basicConfig(
        level=os.environ.get("EC2_ADAPTER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = AdapterConfig()
    mappings = _default_mappings()

    print(f"\n=== EC2 adapter self-check ===")
    print(f"  Bot home:        {cfg.bot_home}")
    print(f"  PM2 log dir:     {cfg.pm2_log_dir}")
    print(f"  Poll interval:   {cfg.poll_interval}s\n")

    print("--- PM2 jlist ---")
    procs = _read_pm2_jlist(cfg)
    if not procs:
        print("  (no pm2 processes detected — pm2 daemon may be down)")
    for p in procs:
        print(f"  {p.name:<28} {p.status:<10} "
              f"restarts={p.restart_count:<3} uptime={p.uptime_sec:>6}s "
              f"cpu={p.cpu_pct:.1f}% mem={p.mem_bytes/1e6:.1f}MB "
              f"paper={p.paper_trading}")

    print("\n--- SQLite polls ---")
    for mp in mappings:
        print(f"\n  [{mp.silo_name}] {mp.pm2_name}  →  {mp.db_filename}")
        snap = _poll_one_bot(mp, cfg)
        if snap.db_error:
            print(f"    db_error: {snap.db_error}")
            continue
        print(f"    daily_pnl:     ₹{snap.daily_pnl:,.2f}")
        print(f"    open positions: {len(snap.open_positions)}")
        for op in snap.open_positions[:5]:
            print(f"      • {op['symbol']:<20} {op['side']:<5} "
                  f"{op['qty']}q @ ₹{op['entry']:,.2f}  SL=₹{op['sl']:,.2f}")
        print(f"    recent trades:  {len(snap.recent_trades)}")
        for tr in snap.recent_trades[:5]:
            print(f"      • {tr['ts']:<20} {tr['symbol']:<18} {tr['side']:<5} "
                  f"{tr['qty']}q @ ₹{tr['price']:,.2f}  {tr['order_id']}")

    print("\n--- Log tail signals ---")
    for mp in mappings:
        for stream in ("out", "error"):
            path = cfg.pm2_log_dir / f"{mp.pm2_name}-{stream}.log"
            lines = _tail_lines(path, cfg.log_tail_lines)
            sig = _scan_log_signals(lines) if lines else {}
            print(f"  {mp.pm2_name}-{stream}.log "
                  f"({len(lines)} lines tailed): {sig}")

    print("\n✓ self-check complete — wire EC2_ADAPTER_ENABLE=true to go live\n")
    return 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="EC2 state adapter")
    p.add_argument("--self-check", action="store_true",
                   help="Dry-run once and print what would be pushed")
    args = p.parse_args()
    if args.self_check:
        raise SystemExit(_self_check())
    print("Adapter is meant to be started by the dashboard "
          "(set EC2_ADAPTER_ENABLE=true).  Use --self-check for a dry-run.")
