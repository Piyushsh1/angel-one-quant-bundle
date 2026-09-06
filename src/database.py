"""
================================================================================
database.py  ▸  Single-schema PostgreSQL state manager
================================================================================
ONE database per deployment environment.  ONE schema for the whole
application.  Engines are isolated by an ``engine`` discriminator column
instead of by separate database files.

    environment isolation :  separate Postgres DB per APP_ENV
                             (algo_barbell_dev / _preprod / _prod)
    engine isolation      :  ``engine`` column ('MACRO' | 'SMALLCAP' | 'OPTIONS')

This replaces the previous design of four separate SQLite files that each
carried an identical schema.  Because the four schemas were already
identical, collapsing them into one table set with an ``engine`` column is
behaviour-preserving: every query is scoped to the owning engine, so one
engine can still never see or mutate another engine's rows.

SCHEMA
──────
    open_positions   (engine, symbol) PK      -- currently-held positions
    trade_history    id BIGSERIAL PK          -- append-only fill ledger
    daily_pnl        (engine, date) PK        -- realised P&L + kill switch
    bot_state        (engine, key) PK         -- free-form flags (halt, sync)
    regime_log       (date, underlying) PK    -- options regime evidence

Timestamps are stored as ISO-8601 TEXT exactly as before, so all existing
time math in the engines and dashboard keeps working unchanged.

USAGE
─────
    from database import BotDB
    db = BotDB("MACRO")                 # engine name, not a file path
    db.add_position(symbol="NIFTYBEES-EQ", token="...", ...)
    rows = db.open_positions()
================================================================================
"""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config

log = logging.getLogger(__name__)

#: Valid values for the ``engine`` discriminator column.
ENGINES = ("MACRO", "SMALLCAP", "OPTIONS")

#: Advisory-lock key so concurrent engine boots serialise schema creation.
_SCHEMA_LOCK_KEY = 848_215_001


# ════════════════════════════════════════════════════════════════════════════
#  SCHEMA
# ════════════════════════════════════════════════════════════════════════════
_SCHEMA = """
CREATE TABLE IF NOT EXISTS open_positions (
    engine        TEXT             NOT NULL,
    symbol        TEXT             NOT NULL,
    token         TEXT             NOT NULL,
    exchange      TEXT             NOT NULL,
    product_type  TEXT             NOT NULL,
    quantity      INTEGER          NOT NULL,
    entry_price   DOUBLE PRECISION NOT NULL,
    stop_loss     DOUBLE PRECISION,
    entry_time    TEXT             NOT NULL,
    order_id      TEXT,
    meta_json     TEXT,
    PRIMARY KEY (engine, symbol)
);

CREATE TABLE IF NOT EXISTS trade_history (
    id           BIGSERIAL        PRIMARY KEY,
    engine       TEXT             NOT NULL,
    timestamp    TEXT             NOT NULL,
    symbol       TEXT             NOT NULL,
    side         TEXT             NOT NULL,
    quantity     INTEGER          NOT NULL,
    price        DOUBLE PRECISION NOT NULL,
    order_type   TEXT             NOT NULL,
    order_id     TEXT,
    pnl          DOUBLE PRECISION,
    meta_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_trade_history_engine_symbol
    ON trade_history(engine, symbol);
CREATE INDEX IF NOT EXISTS idx_trade_history_engine_ts
    ON trade_history(engine, timestamp);

CREATE TABLE IF NOT EXISTS daily_pnl (
    engine          TEXT             NOT NULL,
    date            TEXT             NOT NULL,
    realised_pnl    DOUBLE PRECISION NOT NULL DEFAULT 0,
    kill_switch_hit BOOLEAN          NOT NULL DEFAULT FALSE,
    PRIMARY KEY (engine, date)
);

CREATE TABLE IF NOT EXISTS bot_state (
    engine     TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (engine, key)
);

-- Regime-filter telemetry (the "idle-day evidence" ledger).  Options-only:
-- the live bot writes one row per underlying at OR-computation time
-- (decision = SKIP / ARMED) and regime_eod.py back-fills the hypothetical
-- P&L of SKIP rows so the operator can SEE that standing down avoided a loss.
CREATE TABLE IF NOT EXISTS regime_log (
    date             TEXT             NOT NULL,
    underlying       TEXT             NOT NULL,
    or_high          DOUBLE PRECISION,
    or_low           DOUBLE PRECISION,
    or_width_pct     DOUBLE PRECISION,
    threshold_pct    DOUBLE PRECISION,
    decision         TEXT             NOT NULL,
    hypo_side        TEXT,
    hypo_breakout_ts TEXT,
    hypo_entry_prem  DOUBLE PRECISION,
    hypo_exit_prem   DOUBLE PRECISION,
    hypo_exit_reason TEXT,
    hypo_pnl         DOUBLE PRECISION,
    created_at       TEXT             NOT NULL,
    updated_at       TEXT,
    PRIMARY KEY (date, underlying)
);
CREATE INDEX IF NOT EXISTS idx_regime_log_date ON regime_log(date);

-- Historical OHLCV cache for the offline backtester.  Not trading state, but
-- it lives here rather than in a side-car SQLite file so the application has
-- exactly ONE database technology and this data gets backed up with the rest.
-- Refetching is expensive (rate-limited, 30-day chunks), so it is worth keeping.
CREATE TABLE IF NOT EXISTS candles (
    symbol      TEXT             NOT NULL,
    token       TEXT             NOT NULL,
    interval    TEXT             NOT NULL,
    timestamp   TEXT             NOT NULL,
    open        DOUBLE PRECISION,
    high        DOUBLE PRECISION,
    low         DOUBLE PRECISION,
    close       DOUBLE PRECISION,
    volume      BIGINT,
    PRIMARY KEY (symbol, interval, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON candles (symbol, interval, timestamp);
"""


# ════════════════════════════════════════════════════════════════════════════
#  CONNECTION POOL  (one per process, lazily created)
# ════════════════════════════════════════════════════════════════════════════
_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()

# Schema creation uses its OWN lock. Sharing _pool_lock would deadlock, because
# the schema path needs a connection and _pool_instance() takes _pool_lock —
# threading.Lock is not reentrant.
_schema_lock = threading.Lock()
_schema_ready = False


def _pool_instance() -> ConnectionPool:
    """Return the process-wide connection pool, creating it on first use."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(
                conninfo=config.DATABASE_URL,
                min_size=1,
                max_size=5,
                timeout=15.0,
                max_idle=300.0,
                kwargs={
                    "row_factory": dict_row,
                    "autocommit": False,
                    "options": f"-c statement_timeout={config.DB_STATEMENT_TIMEOUT_MS}",
                },
                open=True,
            )
            log.info("Postgres pool opened → %s", config._redacted_dsn())
    return _pool


def wait_for_database(timeout_s: float = 60.0, interval_s: float = 2.0) -> None:
    """Block until Postgres answers, or raise after ``timeout_s``.

    Containers start in parallel, so an engine can easily win the race
    against its own database.  Every entry point calls this before touching
    state so a cold ``docker compose up`` doesn't crash-loop the engines.
    """
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(config.DATABASE_URL, connect_timeout=5) as c:
                c.execute("SELECT 1")
            return
        except Exception as exc:              # noqa: BLE001 - retry any failure
            last_exc = exc
            log.info("Waiting for Postgres … (%s)", type(exc).__name__)
            time.sleep(interval_s)
    raise RuntimeError(
        f"Postgres unreachable after {timeout_s:.0f}s at "
        f"{config._redacted_dsn()}: {last_exc}"
    )


def close_pool() -> None:
    """Close the pool. Safe to call on shutdown; a no-op if never opened."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


# ════════════════════════════════════════════════════════════════════════════
#  BOT STATE DB CLASS
# ════════════════════════════════════════════════════════════════════════════
class BotDB:
    """Engine-scoped view over the shared application database.

    Every read and write is automatically filtered to ``self.engine``, so
    two engines sharing one database stay as isolated as they were when
    each owned a private SQLite file.
    """

    def __init__(self, engine: str):
        eng = str(engine).upper().strip()
        if eng not in ENGINES:
            raise ValueError(
                f"engine must be one of {ENGINES} (got {engine!r})"
            )
        self.engine = eng
        self._init_schema()
        log.info(
            "BotDB ready | engine=%s env=%s db=%s",
            self.engine, config.APP_ENV, config.DB_NAME,
        )

    # ----- low-level connection helper -----------------------------------
    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        """Context-managed pooled connection.

        Commits on clean exit, rolls back on any exception — the same
        transaction guarantee the previous SQLite implementation provided,
        which the atomic ``close_position`` / ``partial_exit`` methods rely on.
        """
        with _pool_instance().connection() as c:
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise

    def _init_schema(self) -> None:
        """Create tables once per process, guarded by a Postgres advisory lock.

        Several engines boot simultaneously under Docker Compose; concurrent
        ``CREATE TABLE IF NOT EXISTS`` can otherwise deadlock in Postgres.
        """
        global _schema_ready
        if _schema_ready:
            return
        # Resolve the pool BEFORE taking the schema lock so the two locks are
        # never held in a nested order.
        pool = _pool_instance()
        with _schema_lock:
            if _schema_ready:
                return
            with pool.connection() as c:
                try:
                    c.execute("SELECT pg_advisory_lock(%s)", (_SCHEMA_LOCK_KEY,))
                    c.execute(_SCHEMA)
                    c.commit()
                finally:
                    c.execute("SELECT pg_advisory_unlock(%s)", (_SCHEMA_LOCK_KEY,))
                    c.commit()
            _schema_ready = True
            log.info("Schema verified on %s", config.DB_NAME)

    # ============ open_positions =========================================
    def add_position(
        self,
        *,
        symbol: str,
        token: str,
        exchange: str,
        product_type: str,
        quantity: int,
        entry_price: float,
        stop_loss: Optional[float] = None,
        entry_time: Optional[str] = None,
        order_id: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> None:
        if entry_time is None:
            entry_time = datetime.now(config.IST).isoformat()
        meta_blob = json.dumps(meta or {}, separators=(",", ":"))
        with self._conn() as c:
            c.execute(
                """INSERT INTO open_positions
                   (engine, symbol, token, exchange, product_type, quantity,
                    entry_price, stop_loss, entry_time, order_id, meta_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (engine, symbol) DO UPDATE SET
                       token        = excluded.token,
                       exchange     = excluded.exchange,
                       product_type = excluded.product_type,
                       quantity     = excluded.quantity,
                       entry_price  = excluded.entry_price,
                       stop_loss    = excluded.stop_loss,
                       entry_time   = excluded.entry_time,
                       order_id     = excluded.order_id,
                       meta_json    = excluded.meta_json""",
                (
                    self.engine, symbol, token, exchange, product_type, quantity,
                    entry_price, stop_loss, entry_time, order_id, meta_blob,
                ),
            )

    def remove_position(self, symbol: str) -> None:
        with self._conn() as c:
            c.execute(
                "DELETE FROM open_positions WHERE engine = %s AND symbol = %s",
                (self.engine, symbol),
            )

    def update_stop_loss(self, symbol: str, new_sl: float) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE open_positions SET stop_loss = %s "
                "WHERE engine = %s AND symbol = %s",
                (new_sl, self.engine, symbol),
            )

    def update_position_meta(self, symbol: str, meta: dict) -> None:
        """Replace the meta_json blob for an open position.

        Used by the F&O trailing-stop monitor to persist ``peak_premium``
        and ``trail_armed`` between minute-bars so trail state survives a
        restart mid-position.
        """
        blob = json.dumps(meta or {}, separators=(",", ":"), default=str)
        with self._conn() as c:
            c.execute(
                "UPDATE open_positions SET meta_json = %s "
                "WHERE engine = %s AND symbol = %s",
                (blob, self.engine, symbol),
            )

    def open_positions(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM open_positions WHERE engine = %s "
                "ORDER BY entry_time",
                (self.engine,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ============ trade_history ==========================================
    def record_trade(
        self,
        *,
        side: str,
        symbol: str,
        quantity: int,
        price: float,
        order_type: str,
        order_id: Optional[str] = None,
        pnl: Optional[float] = None,
        meta: Optional[dict] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        if timestamp is None:
            timestamp = datetime.now(config.IST).isoformat()
        meta_blob = json.dumps(meta or {}, separators=(",", ":"))
        with self._conn() as c:
            c.execute(
                """INSERT INTO trade_history
                   (engine, timestamp, symbol, side, quantity, price,
                    order_type, order_id, pnl, meta_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (self.engine, timestamp, symbol, side, quantity, price,
                 order_type, order_id, pnl, meta_blob),
            )

    def trade_history(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM trade_history WHERE engine = %s "
                "ORDER BY id DESC LIMIT %s",
                (self.engine, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ============ atomic exit  (record SELL + remove position in one TX) ===
    def close_position(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        order_type: str,
        order_id: Optional[str] = None,
        pnl: Optional[float] = None,
        meta: Optional[dict] = None,
        timestamp: Optional[str] = None,
        book_pnl_date: Optional[str] = None,
    ) -> None:
        """Atomically record a closing trade, delete the position, book the P&L.

        All THREE operations share one transaction. The daily-P&L increment
        used to be committed separately by the caller, which left a crash
        window where P&L was counted against a position the database still
        showed as open — on restart the reconciler would then book the same
        fill a second time, corrupting the number the kill switch depends on.

        Pass ``book_pnl_date`` (YYYY-MM-DD IST) to have ``pnl`` added to
        ``daily_pnl`` inside this same transaction. Callers should no longer
        call :meth:`add_to_daily_pnl` around this method.
        """
        if timestamp is None:
            timestamp = datetime.now(config.IST).isoformat()
        meta_blob = json.dumps(meta or {}, separators=(",", ":"), default=str)
        with self._conn() as c:
            c.execute(
                """INSERT INTO trade_history
                   (engine, timestamp, symbol, side, quantity, price,
                    order_type, order_id, pnl, meta_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (self.engine, timestamp, symbol, side, quantity, price,
                 order_type, order_id, pnl, meta_blob),
            )
            cur = c.execute(
                "DELETE FROM open_positions WHERE engine = %s AND symbol = %s",
                (self.engine, symbol),
            )
            if (cur.rowcount or 0) == 0:
                # The position was already gone. Recording another closing fill
                # would double-count it, so abort rather than write a phantom.
                raise RuntimeError(
                    f"close_position: no open position for "
                    f"engine={self.engine} symbol={symbol} — refusing to "
                    f"record a duplicate closing trade"
                )
            if book_pnl_date is not None and pnl is not None:
                c.execute(
                    """INSERT INTO daily_pnl (engine, date, realised_pnl)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (engine, date) DO UPDATE SET
                           realised_pnl =
                               daily_pnl.realised_pnl + excluded.realised_pnl""",
                    (self.engine, book_pnl_date, pnl),
                )

    # ============ atomic partial-exit  (Scaled Runner / V3) ===============
    def partial_exit(
        self,
        *,
        symbol: str,
        qty_sold: int,
        qty_remaining: int,
        price: float,
        order_type: str = "MARKET",
        order_id: Optional[str] = None,
        pnl: Optional[float] = None,
        trade_meta: Optional[dict] = None,
        position_meta: Optional[dict] = None,
        new_stop_loss: Optional[float] = None,
        timestamp: Optional[str] = None,
        book_pnl_date: Optional[str] = None,
    ) -> None:
        """Record a partial SELL while keeping the position open with reduced qty.

        Used by the F&O Scaled Runner Exit (Phase B): when ltp crosses the
        +TRAIL_TRIGGER_PCT threshold the bot sells ``qty_sold`` at MARKET and
        lets ``qty_remaining`` ride with the SL moved to break-even.

        Every statement — including the daily-P&L increment — runs in ONE
        transaction. This matters more here than anywhere else: if the process
        died between booking P&L and reducing the quantity, the next monitor
        pass would still see the full position with ``trail_armed`` unset, take
        the Phase-B branch again, and sell the same lots a SECOND time at
        MARKET. Pass ``book_pnl_date`` so the increment is committed atomically
        with the quantity reduction.
        """
        if timestamp is None:
            timestamp = datetime.now(config.IST).isoformat()
        trade_blob = json.dumps(trade_meta or {}, separators=(",", ":"), default=str)
        pos_blob = json.dumps(position_meta or {}, separators=(",", ":"), default=str)
        with self._conn() as c:
            c.execute(
                """INSERT INTO trade_history
                   (engine, timestamp, symbol, side, quantity, price,
                    order_type, order_id, pnl, meta_json)
                   VALUES (%s, %s, %s, 'SELL', %s, %s, %s, %s, %s, %s)""",
                (self.engine, timestamp, symbol, qty_sold, price,
                 order_type, order_id, pnl, trade_blob),
            )
            if new_stop_loss is not None:
                cur = c.execute(
                    "UPDATE open_positions SET quantity = %s, meta_json = %s, "
                    "stop_loss = %s WHERE engine = %s AND symbol = %s",
                    (qty_remaining, pos_blob, float(new_stop_loss),
                     self.engine, symbol),
                )
            else:
                cur = c.execute(
                    "UPDATE open_positions SET quantity = %s, meta_json = %s "
                    "WHERE engine = %s AND symbol = %s",
                    (qty_remaining, pos_blob, self.engine, symbol),
                )
            if (cur.rowcount or 0) == 0:
                raise RuntimeError(
                    f"partial_exit: no open position for engine={self.engine} "
                    f"symbol={symbol} — refusing to record a phantom partial"
                )
            if book_pnl_date is not None and pnl is not None:
                c.execute(
                    """INSERT INTO daily_pnl (engine, date, realised_pnl)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (engine, date) DO UPDATE SET
                           realised_pnl =
                               daily_pnl.realised_pnl + excluded.realised_pnl""",
                    (self.engine, book_pnl_date, pnl),
                )

    # ============ daily_pnl  (kill-switch persistence) ===================
    def get_daily_pnl(self, date_str: str) -> tuple[float, bool]:
        """Return ``(realised_pnl_today, kill_switch_hit)``."""
        with self._conn() as c:
            row = c.execute(
                "SELECT realised_pnl, kill_switch_hit FROM daily_pnl "
                "WHERE engine = %s AND date = %s",
                (self.engine, date_str),
            ).fetchone()
        if row is None:
            return 0.0, False
        return float(row["realised_pnl"]), bool(row["kill_switch_hit"])

    def add_to_daily_pnl(self, date_str: str, delta: float) -> float:
        """Atomically increment today's realised P&L and return the new total."""
        with self._conn() as c:
            row = c.execute(
                """INSERT INTO daily_pnl (engine, date, realised_pnl)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (engine, date) DO UPDATE SET
                       realised_pnl = daily_pnl.realised_pnl + excluded.realised_pnl
                   RETURNING realised_pnl""",
                (self.engine, date_str, delta),
            ).fetchone()
        return float(row["realised_pnl"])

    def trigger_kill_switch(self, date_str: str) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO daily_pnl (engine, date, kill_switch_hit)
                   VALUES (%s, %s, TRUE)
                   ON CONFLICT (engine, date) DO UPDATE SET
                       kill_switch_hit = TRUE""",
                (self.engine, date_str),
            )
        log.critical(
            "KILL SWITCH TRIGGERED | engine=%s date=%s — refusing new trades",
            self.engine, date_str,
        )

    # ============ bot_state  (free-form key/value) ========================
    def set_state(self, key: str, value: str) -> None:
        """Set a free-form key/value pair (``position_halt``, ``last_sync``…).

        Overwrites any prior value for this engine.
        """
        ts = datetime.now(config.IST).isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                """INSERT INTO bot_state (engine, key, value, updated_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (engine, key) DO UPDATE SET
                       value      = excluded.value,
                       updated_at = excluded.updated_at""",
                (self.engine, key, value, ts),
            )

    def get_state(self, key: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT value FROM bot_state WHERE engine = %s AND key = %s",
                (self.engine, key),
            ).fetchone()
        return row["value"] if row else None

    def is_halted(self) -> tuple[bool, Optional[str]]:
        """Return ``(halted, reason)``.

        Entry/exit gates use this to refuse all new orders after a detected
        DB↔broker mismatch.  Cleared only by an explicit operator action.
        """
        raw = self.get_state("position_halt")
        if not raw or raw == "0":
            return False, None
        return True, self.get_state("position_halt_reason")

    def halt_trading(self, reason: str) -> None:
        """Latch the trading-halt flag.

        After this, every entry gate MUST refuse new orders until an operator
        runs the reconciler and explicitly clears the halt.
        """
        self.set_state("position_halt", "1")
        self.set_state("position_halt_reason", reason)
        log.critical("POSITION HALT LATCHED | engine=%s reason=%r",
                     self.engine, reason)

    def clear_halt(self) -> None:
        self.set_state("position_halt", "0")
        self.set_state("position_halt_reason", "")
        log.warning("POSITION HALT CLEARED by operator | engine=%s", self.engine)

    # ============ regime_log  (idle-day evidence ledger) =================
    def log_regime(
        self,
        *,
        date_str: str,
        underlying: str,
        or_high: Optional[float],
        or_low: Optional[float],
        or_width_pct: Optional[float],
        threshold_pct: float,
        decision: str,
    ) -> None:
        """Record the regime-gate decision for one underlying on one day.

        Cheap and idempotent (PK = date+underlying).  Callers wrap this in
        try/except so it can NEVER disrupt the live execution loop.
        """
        ts = datetime.now(config.IST).isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                """INSERT INTO regime_log
                   (date, underlying, or_high, or_low, or_width_pct,
                    threshold_pct, decision, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (date, underlying) DO UPDATE SET
                       or_high       = excluded.or_high,
                       or_low        = excluded.or_low,
                       or_width_pct  = excluded.or_width_pct,
                       threshold_pct = excluded.threshold_pct,
                       decision      = excluded.decision,
                       created_at    = excluded.created_at""",
                (date_str, underlying, or_high, or_low, or_width_pct,
                 threshold_pct, decision, ts),
            )

    def update_regime_hypo(
        self,
        *,
        date_str: str,
        underlying: str,
        hypo_side: Optional[str],
        hypo_breakout_ts: Optional[str],
        hypo_entry_prem: Optional[float],
        hypo_exit_prem: Optional[float],
        hypo_exit_reason: Optional[str],
        hypo_pnl: Optional[float],
    ) -> None:
        """Back-fill the hypothetical-P&L columns for a SKIP row (post-close)."""
        ts = datetime.now(config.IST).isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                """UPDATE regime_log SET
                       hypo_side        = %s,
                       hypo_breakout_ts = %s,
                       hypo_entry_prem  = %s,
                       hypo_exit_prem   = %s,
                       hypo_exit_reason = %s,
                       hypo_pnl         = %s,
                       updated_at       = %s
                   WHERE date = %s AND underlying = %s""",
                (hypo_side, hypo_breakout_ts, hypo_entry_prem, hypo_exit_prem,
                 hypo_exit_reason, hypo_pnl, ts, date_str, underlying),
            )

    def regime_log_for_date(self, date_str: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM regime_log WHERE date = %s ORDER BY underlying",
                (date_str,),
            ).fetchall()
        return [dict(r) for r in rows]

    def regime_log_recent(self, limit_days: int = 30) -> list[dict[str, Any]]:
        """Return regime rows for the most recent ``limit_days`` distinct dates."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM regime_log
                   WHERE date IN (
                       SELECT DISTINCT date FROM regime_log
                       ORDER BY date DESC LIMIT %s
                   )
                   ORDER BY date DESC, underlying""",
                (limit_days,),
            ).fetchall()
        return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════════
#  CANDLE CACHE  (historical OHLCV for the offline backtester)
# ════════════════════════════════════════════════════════════════════════════
class CandleCache:
    """Historical OHLCV store used by ``backtest_engine``.

    Not engine-scoped — market data is the same whichever engine looks at it —
    so this is a separate class rather than a method on :class:`BotDB`.
    """

    def __init__(self) -> None:
        # Reuse BotDB's schema bootstrap; the candles table is created with the
        # rest of the schema, so there is only one place that defines DDL.
        BotDB("OPTIONS")

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        with _pool_instance().connection() as c:
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise

    def insert(self, symbol: str, token: str, interval: str,
               rows: list[tuple]) -> int:
        """Insert candles, ignoring duplicates. Returns rows actually written.

        ``rows`` is a sequence of
        ``(timestamp, open, high, low, close, volume)`` tuples.
        """
        if not rows:
            return 0
        payload = [(symbol, token, interval, *r) for r in rows]
        with self._conn() as c:
            with c.cursor() as cur:
                cur.executemany(
                    """INSERT INTO candles
                       (symbol, token, interval, timestamp,
                        open, high, low, close, volume)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (symbol, interval, timestamp) DO NOTHING""",
                    payload,
                )
                return cur.rowcount or 0

    def load(self, symbol: str, interval: str,
             start: Optional[str] = None,
             end: Optional[str] = None) -> list[dict[str, Any]]:
        """Return cached candles ordered oldest-first.

        ``start`` / ``end`` are ISO-8601 strings matching the stored format.
        """
        sql = ["SELECT timestamp, open, high, low, close, volume FROM candles",
               "WHERE symbol = %s AND interval = %s"]
        args: list[Any] = [symbol, interval]
        if start:
            sql.append("AND timestamp >= %s")
            args.append(start)
        if end:
            sql.append("AND timestamp <= %s")
            args.append(end)
        sql.append("ORDER BY timestamp ASC")
        with self._conn() as c:
            rows = c.execute(" ".join(sql), args).fetchall()
        return [dict(r) for r in rows]

    def symbols(self, like: Optional[str] = None) -> list[str]:
        """Return distinct cached symbols, optionally filtered by SQL LIKE."""
        with self._conn() as c:
            if like:
                rows = c.execute(
                    "SELECT DISTINCT symbol FROM candles WHERE symbol LIKE %s "
                    "ORDER BY symbol",
                    (like,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT DISTINCT symbol FROM candles ORDER BY symbol"
                ).fetchall()
        return [r["symbol"] for r in rows]


# ════════════════════════════════════════════════════════════════════════════
#  SELF-TEST  (python src/database.py — verifies connectivity + schema)
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    _log = logging.getLogger("database")

    _log.info("Environment : %s", config.APP_ENV)
    _log.info("Database    : %s", config._redacted_dsn())
    wait_for_database()
    for _eng in ENGINES:
        _db = BotDB(_eng)
        _open = _db.open_positions()
        _pnl, _ks = _db.get_daily_pnl(datetime.now(config.IST).strftime("%Y-%m-%d"))
        _log.info(
            "  %-9s open=%-3d today_pnl=%+.2f kill_switch=%s",
            _eng, len(_open), _pnl, _ks,
        )
    close_pool()
    _log.info("✅  database.py — schema verified, all engines reachable.")
