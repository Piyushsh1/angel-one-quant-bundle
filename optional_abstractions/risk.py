"""angel-one-quant-bundle  ▸  risk.py
==============================================================================
Capital protection.  Three hard-capped silos + per-trade trailing stops +
daily drawdown kill switches.  This module is the *only* layer that should
ever say "no" to a strategy.  If you find yourself writing capital checks
inside a strategy file, stop and put them here instead.

DESIGN PRINCIPLES
─────────────────
1.  **Silos are independent.**  A loss in `DERIV` cannot drain `TREND`'s
    capital.  Each silo has its own ceiling, its own deployed amount, and
    its own daily PnL counter.

2.  **Locks are sticky.**  When a silo trips its DD limit, it locks for the
    entire session.  Unlocking requires a process restart — there is no
    `RiskManager.force_unlock()`.  This is intentional friction: the kind
    of bad day where you "just need one more trade to recover" is exactly
    the day you should be flat.

3.  **Trailing stops are stateful and per-position.**  A position owns its
    own `TrailingStop` — the risk module does not maintain a global map.
    This keeps the strategy code in control of when to update vs. discard.

4.  **Every refusal is logged.**  Audit trail is the whole point.  Look at
    your `risk` logger output the morning after a bad session — every
    `REFUSED` line tells you what your system caught.

USAGE SHAPE
───────────
    rm = RiskManager.from_config()

    # Position sizing — strategy asks the silo how much it can risk
    silo = rm.silo("DERIV")
    risk_budget = silo.risk_budget_per_trade()        # e.g. ₹1500
    qty = int(risk_budget / sl_distance)

    # Capital reservation — commit() is the ONLY public path and is fully
    # atomic.  No can_open() / commit() two-step (that pattern was TOCTOU-
    # racy and was removed in the 2026-05-26 audit, fix B7).
    cost = qty * entry_price
    try:
        silo.commit(cost=cost, position_id="ABC123")
    except RiskRefusedError:
        return  # silo logs the refusal at WARNING level
    # ... place order ...

    # During the trade — feed PnL updates
    silo.update_unrealised("ABC123", current_pnl)
    if silo.is_locked:
        # DD breached mid-trade — force exit
        ...

    # On close
    silo.release(position_id="ABC123", realised_pnl=+450.0)

    # Trailing stop
    ts = TrailingStop.from_long_entry(entry=100, hard_sl=95)
    new_sl = ts.update(ltp=121)   # may move SL up
    if ts.exit_signal(ltp=ltp):
        # ratchet hit — exit
==============================================================================
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from . import config

log = logging.getLogger("risk")


class RiskRefusedError(Exception):
    """Raised by `commit()` if the silo is locked or capital is insufficient."""


# ════════════════════════════════════════════════════════════════════════════
#  TRAILING STOP  (free-ride ratchet)
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class TrailingStop:
    """Per-position trailing stop with a "free-ride" activation gate.

    Behaviour
    ─────────
    *  Until the position is in profit by ``trigger_pct`` (e.g. +20%), the
       hard SL stays put.  This avoids tightening a stop on noise.

    *  ON THE ARM TICK — the exact tick gain first crosses ``trigger_pct`` —
       the SL is IMMEDIATELY ratcheted to ``extreme × (1 ∓ step_pct)``,
       NOT to break-even.  This is an intentional aggressive ratchet: by
       the time a position arms it has usually moved decisively, so we
       lock in protection BELOW the high-water-mark on the same tick.
       In practice this means the arm-tick SL is always strictly better
       than break-even.  (Fix B12 — 26 May 2026: was previously implied
       in docstring to lock at break-even first; corrected to match the
       actual aggressive arming behaviour the test suite asserts.)

    *  On every subsequent tick that prints a NEW extreme, the SL is
       ratcheted further in favour of the position.  The SL never moves
       against the position.

    *  ``exit_signal(ltp)`` is what the strategy polls each tick.

    The class is intentionally side-aware ("BUY" = long, "SELL" = short).
    Default config values come from `config.TRAIL_TRIGGER_PCT` and
    `config.TRAIL_STEP_PCT`.

    Thread-safety: no internal lock.  A position is owned by exactly one
    strategy coroutine, so external synchronisation is sufficient.
    """
    side: str               # "BUY" (long) or "SELL" (short)
    entry: float
    hard_sl: float          # initial SL set at entry
    trigger_pct: float = field(default_factory=lambda: config.TRAIL_TRIGGER_PCT)
    step_pct: float    = field(default_factory=lambda: config.TRAIL_STEP_PCT)
    extreme: float = 0.0    # high-watermark (long) or low-watermark (short)
    armed: bool    = False  # True once trigger_pct hurdle is cleared
    current_sl: float = 0.0

    def __post_init__(self) -> None:
        self.side = self.side.upper()
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"TrailingStop.side must be BUY/SELL, got {self.side}")
        self.extreme = self.entry
        self.current_sl = self.hard_sl

    @classmethod
    def from_long_entry(
        cls, *, entry: float, hard_sl: float,
        trigger_pct: float | None = None, step_pct: float | None = None,
    ) -> "TrailingStop":
        return cls(
            side="BUY", entry=entry, hard_sl=hard_sl,
            trigger_pct=trigger_pct if trigger_pct is not None else config.TRAIL_TRIGGER_PCT,
            step_pct=step_pct if step_pct is not None else config.TRAIL_STEP_PCT,
        )

    @classmethod
    def from_short_entry(
        cls, *, entry: float, hard_sl: float,
        trigger_pct: float | None = None, step_pct: float | None = None,
    ) -> "TrailingStop":
        return cls(
            side="SELL", entry=entry, hard_sl=hard_sl,
            trigger_pct=trigger_pct if trigger_pct is not None else config.TRAIL_TRIGGER_PCT,
            step_pct=step_pct if step_pct is not None else config.TRAIL_STEP_PCT,
        )

    def update(self, ltp: float) -> float:
        """Feed a fresh price.  Returns the (possibly updated) SL."""
        if ltp <= 0:
            return self.current_sl

        if self.side == "BUY":
            if ltp > self.extreme:
                self.extreme = ltp
            gain_pct = (self.extreme - self.entry) / self.entry
            if not self.armed and gain_pct >= self.trigger_pct:
                self.armed = True
                # First arm: lock SL at entry (break-even).
                self.current_sl = max(self.current_sl, self.entry)
                log.info(
                    "TRAIL ARMED long | entry=%.2f extreme=%.2f gain=%.1f%% "
                    "→ SL bumped to break-even %.2f",
                    self.entry, self.extreme, gain_pct * 100, self.current_sl,
                )
            if self.armed:
                ratchet = self.extreme * (1 - self.step_pct)
                if ratchet > self.current_sl:
                    log.info(
                        "TRAIL STEP long | extreme=%.2f SL %.2f → %.2f",
                        self.extreme, self.current_sl, ratchet,
                    )
                    self.current_sl = ratchet
        else:  # SELL / short
            if ltp < self.extreme:
                self.extreme = ltp
            gain_pct = (self.entry - self.extreme) / self.entry
            if not self.armed and gain_pct >= self.trigger_pct:
                self.armed = True
                self.current_sl = min(self.current_sl, self.entry)
                log.info(
                    "TRAIL ARMED short | entry=%.2f extreme=%.2f gain=%.1f%% "
                    "→ SL bumped to break-even %.2f",
                    self.entry, self.extreme, gain_pct * 100, self.current_sl,
                )
            if self.armed:
                ratchet = self.extreme * (1 + self.step_pct)
                if ratchet < self.current_sl:
                    log.info(
                        "TRAIL STEP short | extreme=%.2f SL %.2f → %.2f",
                        self.extreme, self.current_sl, ratchet,
                    )
                    self.current_sl = ratchet
        return self.current_sl

    def exit_signal(self, ltp: float) -> bool:
        """True when the current SL has been breached."""
        if self.side == "BUY":
            return ltp <= self.current_sl
        return ltp >= self.current_sl


# ════════════════════════════════════════════════════════════════════════════
#  POSITION TRACKER  (internal silo bookkeeping)
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class _OpenPosition:
    position_id: str
    cost: float
    unrealised_pnl: float = 0.0


# ════════════════════════════════════════════════════════════════════════════
#  CAPITAL SILO
# ════════════════════════════════════════════════════════════════════════════
class CapitalSilo:
    """One independent capital bucket.

    Invariants enforced
    ───────────────────
    *  `deployed_capital <= max_capital` at all times.
    *  Once `is_locked == True`, no new commit can succeed.
    *  All mutating methods are protected by a per-silo lock so multiple
       strategy coroutines can share a silo without races.
    """

    def __init__(
        self, *,
        name: str,
        max_capital: float,
        risk_pct_per_trade: float,
        daily_drawdown_pct: float = config.DAILY_DRAWDOWN_PCT,
    ) -> None:
        if max_capital <= 0:
            raise ValueError(f"max_capital must be > 0, got {max_capital}")
        if not (0 < risk_pct_per_trade < 1):
            raise ValueError(
                f"risk_pct_per_trade must be in (0,1), got {risk_pct_per_trade}"
            )
        if not (0 < daily_drawdown_pct < 1):
            raise ValueError(
                f"daily_drawdown_pct must be in (0,1), got {daily_drawdown_pct}"
            )

        self.name: Final[str]                 = name
        self.max_capital: Final[float]        = max_capital
        self.risk_pct_per_trade: Final[float] = risk_pct_per_trade
        self.daily_drawdown_pct: Final[float] = daily_drawdown_pct
        self.daily_loss_limit: Final[float]   = max_capital * daily_drawdown_pct

        self._lock = threading.RLock()
        self._open: dict[str, _OpenPosition] = {}
        self._realised_pnl_today: float = 0.0
        self._locked: bool = False
        self._lock_reason: str = ""
        self._session_date: date = date.today()

    # ── introspection ───────────────────────────────────────────────────
    @property
    def deployed_capital(self) -> float:
        with self._lock:
            return sum(p.cost for p in self._open.values())

    @property
    def available_capital(self) -> float:
        with self._lock:
            return max(0.0, self.max_capital - self.deployed_capital)

    @property
    def open_position_count(self) -> int:
        with self._lock:
            return len(self._open)

    @property
    def realised_pnl_today(self) -> float:
        with self._lock:
            return self._realised_pnl_today

    @property
    def unrealised_pnl(self) -> float:
        with self._lock:
            return sum(p.unrealised_pnl for p in self._open.values())

    @property
    def total_pnl_today(self) -> float:
        return self.realised_pnl_today + self.unrealised_pnl

    @property
    def is_locked(self) -> bool:
        return self._locked

    @property
    def lock_reason(self) -> str:
        return self._lock_reason

    def risk_budget_per_trade(self) -> float:
        """Rupees-at-risk for a single new entry."""
        return self.max_capital * self.risk_pct_per_trade

    # ── capital management ──────────────────────────────────────────────
    # Fix B7 (26 May 2026): the public `can_open(cost)` soft-check was
    # removed.  Even with the read protected by `self._lock`, the
    # standard "if silo.can_open(...): silo.commit(...)" pattern was a
    # textbook TOCTOU race — two strategies clearing the gate concurrently
    # would both attempt `commit()` and only the locking inside `commit()`
    # would catch the over-allocation.  Worse, callers who *forgot* the
    # try/except around commit() would believe the can_open() check was
    # a hard guard.  The fix is to make `commit()` the ONLY public path
    # for reserving capital — it is fully atomic, and callers who need a
    # pre-flight check simply use try/except RiskRefusedError.
    #
    # MIGRATION EXAMPLE
    # ─────────────────
    # Before (TOCTOU-prone):
    #     if not silo.can_open(cost):
    #         return
    #     silo.commit(cost=cost, position_id=pid)
    #
    # After (atomic):
    #     try:
    #         silo.commit(cost=cost, position_id=pid)
    #     except RiskRefusedError:
    #         return    # silo logs the refusal at WARNING level
    def commit(self, *, position_id: str, cost: float) -> None:
        """Atomically evaluate-and-reserve ``cost`` against the silo.

        This is the ONLY public path for reserving capital; the entire
        read-modify-write is serialised under ``self._lock`` so no two
        concurrent callers can over-allocate the silo even when they
        race past the same balance read.

        Raises
        ──────
        RiskRefusedError
            If the silo is locked, the position_id is already in use, or
            insufficient capital is available.
        """
        with self._lock:
            if self._locked:
                msg = (
                    f"[{self.name}] REFUSED commit ₹{cost:,.0f} "
                    f"(silo LOCKED: {self._lock_reason})"
                )
                log.warning(msg)
                raise RiskRefusedError(msg)
            if cost <= 0:
                raise RiskRefusedError(f"[{self.name}] cost must be > 0 (got {cost})")
            if position_id in self._open:
                raise RiskRefusedError(
                    f"[{self.name}] position_id {position_id!r} already open"
                )
            if cost > self.available_capital:
                msg = (
                    f"[{self.name}] REFUSED commit ₹{cost:,.0f} "
                    f"(available=₹{self.available_capital:,.0f} of "
                    f"₹{self.max_capital:,.0f})"
                )
                log.warning(msg)
                raise RiskRefusedError(msg)

            self._open[position_id] = _OpenPosition(position_id, cost)
            log.info(
                "[%s] COMMIT ₹%,.0f → pos_id=%s | deployed=₹%,.0f/%,.0f",
                self.name, cost, position_id,
                self.deployed_capital, self.max_capital,
            )

    def update_unrealised(self, *, position_id: str, pnl: float) -> None:
        """Push the latest mark-to-market PnL for an open position."""
        with self._lock:
            pos = self._open.get(position_id)
            if pos is None:
                log.debug("[%s] update on unknown pos_id=%s", self.name, position_id)
                return
            pos.unrealised_pnl = float(pnl)
            self._maybe_lock_on_drawdown()

    def release(self, *, position_id: str, realised_pnl: float) -> None:
        """Close a position, free its capital, and book realised PnL."""
        with self._lock:
            pos = self._open.pop(position_id, None)
            if pos is None:
                log.warning(
                    "[%s] release on unknown pos_id=%s — ignored",
                    self.name, position_id,
                )
                return
            self._realised_pnl_today += float(realised_pnl)
            log.info(
                "[%s] RELEASE pos_id=%s pnl=₹%+.2f | realised_today=₹%+.2f | "
                "deployed=₹%,.0f/%,.0f",
                self.name, position_id, realised_pnl,
                self._realised_pnl_today,
                self.deployed_capital, self.max_capital,
            )
            self._maybe_lock_on_drawdown()

    # ── kill switch ─────────────────────────────────────────────────────
    def _maybe_lock_on_drawdown(self) -> None:
        """Lock the silo if total PnL crosses -daily_loss_limit."""
        if self._locked:
            return
        total = self.total_pnl_today
        if total <= -self.daily_loss_limit:
            self._locked = True
            self._lock_reason = (
                f"daily DD breach: total_pnl=₹{total:+.0f} "
                f"≤ -₹{self.daily_loss_limit:,.0f} "
                f"({self.daily_drawdown_pct:.0%} of cap)"
            )
            log.critical("[%s] 🔒 SILO LOCKED — %s", self.name, self._lock_reason)

    def force_lock(self, reason: str) -> None:
        """Externally lock this silo — e.g. when the global breaker fires."""
        with self._lock:
            if not self._locked:
                self._locked = True
                self._lock_reason = reason
                log.critical("[%s] 🔒 SILO LOCKED — %s", self.name, reason)

    def snapshot(self) -> dict:
        """JSON-friendly view of the silo's current state."""
        with self._lock:
            return {
                "name":               self.name,
                "max_capital":        self.max_capital,
                "deployed_capital":   self.deployed_capital,
                "available_capital":  self.available_capital,
                "open_positions":     self.open_position_count,
                "realised_pnl_today": self._realised_pnl_today,
                "unrealised_pnl":     self.unrealised_pnl,
                "total_pnl_today":    self.total_pnl_today,
                "daily_loss_limit":   self.daily_loss_limit,
                "is_locked":          self._locked,
                "lock_reason":        self._lock_reason,
            }


# ════════════════════════════════════════════════════════════════════════════
#  RISK MANAGER  (three-silo orchestrator)
# ════════════════════════════════════════════════════════════════════════════
class RiskManager:
    """Holds the three named silos and the global drawdown breaker."""

    def __init__(self, silos: dict[str, CapitalSilo]) -> None:
        if len(silos) != 3:
            raise ValueError(
                f"RiskManager expects exactly 3 silos, got {len(silos)}"
            )
        self._silos = silos
        self._total_max = sum(s.max_capital for s in silos.values())
        self._global_dd_limit = self._total_max * config.GLOBAL_DRAWDOWN_PCT
        self._global_locked = False

    @classmethod
    def from_config(cls) -> "RiskManager":
        """Build the canonical 3-silo manager from `.env`-driven config."""
        silos = {
            config.SILO_A_NAME: CapitalSilo(
                name=config.SILO_A_NAME,
                max_capital=config.SILO_A_MAX_CAPITAL,
                risk_pct_per_trade=config.SILO_A_RISK_PCT,
            ),
            config.SILO_B_NAME: CapitalSilo(
                name=config.SILO_B_NAME,
                max_capital=config.SILO_B_MAX_CAPITAL,
                risk_pct_per_trade=config.SILO_B_RISK_PCT,
            ),
            config.SILO_C_NAME: CapitalSilo(
                name=config.SILO_C_NAME,
                max_capital=config.SILO_C_MAX_CAPITAL,
                risk_pct_per_trade=config.SILO_C_RISK_PCT,
            ),
        }
        return cls(silos)

    # ── lookups ─────────────────────────────────────────────────────────
    def silo(self, name: str) -> CapitalSilo:
        try:
            return self._silos[name]
        except KeyError:
            valid = list(self._silos)
            raise KeyError(
                f"Unknown silo {name!r} — valid: {valid}"
            ) from None

    def silos(self) -> list[CapitalSilo]:
        return list(self._silos.values())

    # ── global breaker ──────────────────────────────────────────────────
    @property
    def global_pnl_today(self) -> float:
        return sum(s.total_pnl_today for s in self._silos.values())

    @property
    def is_globally_locked(self) -> bool:
        return self._global_locked

    def check_global_breaker(self) -> None:
        """Run after any silo PnL update.  Locks ALL silos if global DD breached."""
        if self._global_locked:
            return
        if self.global_pnl_today <= -self._global_dd_limit:
            self._global_locked = True
            reason = (
                f"global DD breach: total=₹{self.global_pnl_today:+.0f} "
                f"≤ -₹{self._global_dd_limit:,.0f} "
                f"({config.GLOBAL_DRAWDOWN_PCT:.0%} of total)"
            )
            log.critical("⛔ GLOBAL BREAKER FIRED — %s", reason)
            for s in self._silos.values():
                s.force_lock(f"global breaker: {reason}")

    # ── reporting ───────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        return {
            "global_pnl_today":   self.global_pnl_today,
            "global_dd_limit":    self._global_dd_limit,
            "is_globally_locked": self._global_locked,
            "total_max_capital":  self._total_max,
            "silos":              [s.snapshot() for s in self._silos.values()],
        }

    def log_summary(self) -> None:
        snap = self.snapshot()
        log.info("══════════ RISK SNAPSHOT ══════════")
        log.info(
            "GLOBAL pnl=₹%+.2f / limit=₹%,.0f | locked=%s",
            snap["global_pnl_today"], snap["global_dd_limit"],
            snap["is_globally_locked"],
        )
        for s in snap["silos"]:
            log.info(
                "  %-10s deployed=₹%,.0f/%,.0f pnl=₹%+.2f open=%d locked=%s",
                s["name"], s["deployed_capital"], s["max_capital"],
                s["total_pnl_today"], s["open_positions"], s["is_locked"],
            )
        log.info("═══════════════════════════════════")
