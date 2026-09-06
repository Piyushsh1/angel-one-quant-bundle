"""angel-one-quant-bundle  ▸  config.py
==============================================================================
Environment-driven configuration.  All tunables are loaded ONCE at import time,
validated immediately, and exported as module-level constants.

Why this pattern?
  - Trade bots fail at 09:15:00 IST because someone left `MAX_CAPITAL=` empty
    in `.env`.  We refuse to start when that happens, instead of discovering
    it three minutes into the session.
  - Re-reading `.env` mid-session is forbidden.  If you change a parameter,
    you restart the process.  Configuration is a property of the run.

Loading order:
  1.  python-dotenv reads `.env` from the CWD (or whatever
      `ANGEL_DOTENV_PATH` points at).
  2.  Every constant below either:
        (a) reads from `os.environ` with a sane default and a type cast, OR
        (b) raises `ConfigError` at import if a hard-required key is missing.
  3.  `_validate()` runs cross-field sanity checks (e.g. silo caps > 0,
      risk %s in (0, 1), kill-switch DD% in (0, 1)).

Anything that is NOT covered here (e.g. instrument-master refresh cadence,
log retention) lives next to its caller as a module-level constant.  Only
secrets, capital limits, and risk knobs belong in `.env`.
==============================================================================
"""
from __future__ import annotations

import logging
import os
from datetime import time as _dt_time
from pathlib import Path
from typing import Final

from dotenv import load_dotenv
from pytz import timezone

# ── dotenv load ──────────────────────────────────────────────────────────────
_DOTENV_PATH = os.environ.get("ANGEL_DOTENV_PATH", ".env")
load_dotenv(_DOTENV_PATH, override=False)


class ConfigError(RuntimeError):
    """Raised when `.env` is missing a hard-required key or a value is invalid."""


# ── helpers ──────────────────────────────────────────────────────────────────
def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise ConfigError(f"Missing required env var: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _as_bool(key: str, default: bool = False) -> bool:
    raw = _optional(key, str(default)).lower()
    return raw in ("1", "true", "yes", "on", "y", "t")


def _as_int(key: str, default: int) -> int:
    raw = _optional(key, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}: not an integer ({raw!r})") from exc


def _as_float(key: str, default: float) -> float:
    raw = _optional(key, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}: not a float ({raw!r})") from exc


def _as_hhmm(key: str, default: str) -> _dt_time:
    raw = _optional(key, default)
    try:
        hh, mm = raw.split(":")
        return _dt_time(int(hh), int(mm))
    except (ValueError, AttributeError) as exc:
        raise ConfigError(f"{key}: not HH:MM ({raw!r})") from exc


# ════════════════════════════════════════════════════════════════════════════
#  BROKER CREDENTIALS  (hard-required)
# ════════════════════════════════════════════════════════════════════════════
ANGEL_API_KEY: Final[str]    = _require("ANGEL_API_KEY")
ANGEL_CLIENT_ID: Final[str]  = _require("ANGEL_CLIENT_ID")
ANGEL_PASSWORD: Final[str]   = _require("ANGEL_PASSWORD")
ANGEL_TOTP_SECRET: Final[str] = _require("ANGEL_TOTP_SECRET")


# ════════════════════════════════════════════════════════════════════════════
#  RUNTIME FLAGS
# ════════════════════════════════════════════════════════════════════════════
# When True, `execution.py` short-circuits every order call and logs the
# would-be payload instead of hitting the broker.  Keep this ON until you
# have eyeballed at least one full session of paper logs.
PAPER_TRADING: Final[bool] = _as_bool("PAPER_TRADING", default=True)

LOG_LEVEL: Final[str] = _optional("LOG_LEVEL", "INFO").upper()
LOG_DIR: Final[Path]  = Path(_optional("LOG_DIR", "./logs")).resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════════════════════════════════════
#  EXCHANGE / SESSION
# ════════════════════════════════════════════════════════════════════════════
IST = timezone("Asia/Kolkata")

MARKET_OPEN: Final[_dt_time]  = _as_hhmm("MARKET_OPEN",  "09:15")
MARKET_CLOSE: Final[_dt_time] = _as_hhmm("MARKET_CLOSE", "15:30")
SQUARE_OFF_TIME: Final[_dt_time] = _as_hhmm("SQUARE_OFF_TIME", "15:15")

TICK_SIZE: Final[float] = _as_float("TICK_SIZE", 0.05)


# ════════════════════════════════════════════════════════════════════════════
#  API PACING  (Angel rate-limit guardrail)
# ════════════════════════════════════════════════════════════════════════════
# Angel One enforces ~10 req/s on most endpoints.  We pad it heavily because
# the cost of a 429 during a breakout is unbounded.
API_PACING_SECONDS: Final[float] = _as_float("API_PACING_SECONDS", 1.0)
API_MAX_RETRIES: Final[int]      = _as_int("API_MAX_RETRIES", 3)
API_RETRY_BACKOFF: Final[float]  = _as_float("API_RETRY_BACKOFF", 1.5)


# ════════════════════════════════════════════════════════════════════════════
#  CAPITAL SILOS  (the three independent risk buckets)
# ════════════════════════════════════════════════════════════════════════════
# Each silo gets its own hard ceiling.  If a strategy tries to deploy
# capital that exceeds its silo, `risk.RiskManager.allocate()` refuses the
# order — no exceptions, no overrides, no "just this once."
#
# `SILO_*_MAX_CAPITAL` is the absolute ceiling.  `SILO_*_RISK_PCT` is the
# fraction of the silo that is at-risk PER TRADE (used by position sizing).
SILO_A_NAME: Final[str]           = _optional("SILO_A_NAME", "TREND")
SILO_A_MAX_CAPITAL: Final[float]  = _as_float("SILO_A_MAX_CAPITAL", 100_000.0)
SILO_A_RISK_PCT: Final[float]     = _as_float("SILO_A_RISK_PCT", 0.02)

SILO_B_NAME: Final[str]           = _optional("SILO_B_NAME", "SWING")
SILO_B_MAX_CAPITAL: Final[float]  = _as_float("SILO_B_MAX_CAPITAL", 100_000.0)
SILO_B_RISK_PCT: Final[float]     = _as_float("SILO_B_RISK_PCT", 0.01)

SILO_C_NAME: Final[str]           = _optional("SILO_C_NAME", "DERIV")
SILO_C_MAX_CAPITAL: Final[float]  = _as_float("SILO_C_MAX_CAPITAL", 100_000.0)
SILO_C_RISK_PCT: Final[float]     = _as_float("SILO_C_RISK_PCT", 0.05)


# ════════════════════════════════════════════════════════════════════════════
#  KILL SWITCHES  (drawdown-driven trading halts)
# ════════════════════════════════════════════════════════════════════════════
# Per-silo daily loss cap.  When realised+unrealised PnL for a silo crosses
# -X% of its max capital, the silo locks for the rest of the session.
# Re-opening it requires a process restart (intentional friction).
DAILY_DRAWDOWN_PCT: Final[float] = _as_float("DAILY_DRAWDOWN_PCT", 0.04)

# Global circuit breaker: if combined unrealised PnL across all silos
# breaches this, ALL three silos lock simultaneously.
GLOBAL_DRAWDOWN_PCT: Final[float] = _as_float("GLOBAL_DRAWDOWN_PCT", 0.06)


# ════════════════════════════════════════════════════════════════════════════
#  TRAILING-STOP DEFAULTS  (overridable per-trade by strategy code)
# ════════════════════════════════════════════════════════════════════════════
TRAIL_TRIGGER_PCT: Final[float] = _as_float("TRAIL_TRIGGER_PCT", 0.20)
TRAIL_STEP_PCT: Final[float]    = _as_float("TRAIL_STEP_PCT", 0.10)


# ════════════════════════════════════════════════════════════════════════════
#  WEBSOCKET FEED
# ════════════════════════════════════════════════════════════════════════════
# Mode 1 = LTP only, 2 = Quote, 3 = Snap Quote (full depth + OHLC).
# Use 1 unless you genuinely need DOM — Mode 3 ticks are heavy.
WS_MODE: Final[int]              = _as_int("WS_MODE", 1)
WS_RECONNECT_BACKOFF: Final[float] = _as_float("WS_RECONNECT_BACKOFF", 3.0)
WS_MAX_RECONNECTS: Final[int]    = _as_int("WS_MAX_RECONNECTS", 20)


# ════════════════════════════════════════════════════════════════════════════
#  WEBHOOK ALERTS  (Telegram / Discord broadcast layer)
# ════════════════════════════════════════════════════════════════════════════
# Either / both / none can be set.  When neither is configured, alerts fall
# through to stdout — still useful, tee to a log file.
#
# TELEGRAM:   bot token from @BotFather, chat_id from @userinfobot.
# DISCORD:    server settings → Integrations → Webhooks → "New Webhook".
ALERT_TELEGRAM_BOT_TOKEN: Final[str] = _optional("ALERT_TELEGRAM_BOT_TOKEN", "")
ALERT_TELEGRAM_CHAT_ID: Final[str]   = _optional("ALERT_TELEGRAM_CHAT_ID", "")
ALERT_DISCORD_WEBHOOK_URL: Final[str] = _optional("ALERT_DISCORD_WEBHOOK_URL", "")

# Brand name shown in every payload header — change once per client.
ALERT_BRAND_NAME: Final[str] = _optional("ALERT_BRAND_NAME", "CODE & CAPITAL")

# Dispatch HTTP timeout.  Bounded so a flaky webhook can never stall the
# tick loop.
ALERT_HTTP_TIMEOUT: Final[float] = _as_float("ALERT_HTTP_TIMEOUT", 4.0)


# ════════════════════════════════════════════════════════════════════════════
#  VALIDATION
# ════════════════════════════════════════════════════════════════════════════
def _validate() -> None:
    """Cross-field sanity checks.  Raises ConfigError on any failure."""
    if not (0 < SILO_A_RISK_PCT < 1):
        raise ConfigError(f"SILO_A_RISK_PCT must be in (0,1), got {SILO_A_RISK_PCT}")
    if not (0 < SILO_B_RISK_PCT < 1):
        raise ConfigError(f"SILO_B_RISK_PCT must be in (0,1), got {SILO_B_RISK_PCT}")
    if not (0 < SILO_C_RISK_PCT < 1):
        raise ConfigError(f"SILO_C_RISK_PCT must be in (0,1), got {SILO_C_RISK_PCT}")

    for name, cap in (
        ("SILO_A", SILO_A_MAX_CAPITAL),
        ("SILO_B", SILO_B_MAX_CAPITAL),
        ("SILO_C", SILO_C_MAX_CAPITAL),
    ):
        if cap <= 0:
            raise ConfigError(f"{name}_MAX_CAPITAL must be > 0, got {cap}")

    if not (0 < DAILY_DRAWDOWN_PCT < 1):
        raise ConfigError(
            f"DAILY_DRAWDOWN_PCT must be in (0,1), got {DAILY_DRAWDOWN_PCT}"
        )
    if not (0 < GLOBAL_DRAWDOWN_PCT < 1):
        raise ConfigError(
            f"GLOBAL_DRAWDOWN_PCT must be in (0,1), got {GLOBAL_DRAWDOWN_PCT}"
        )
    if GLOBAL_DRAWDOWN_PCT < DAILY_DRAWDOWN_PCT:
        raise ConfigError(
            "GLOBAL_DRAWDOWN_PCT must be ≥ DAILY_DRAWDOWN_PCT "
            f"({GLOBAL_DRAWDOWN_PCT} < {DAILY_DRAWDOWN_PCT})"
        )

    if API_PACING_SECONDS < 0:
        raise ConfigError(f"API_PACING_SECONDS cannot be negative ({API_PACING_SECONDS})")
    if TICK_SIZE <= 0:
        raise ConfigError(f"TICK_SIZE must be > 0, got {TICK_SIZE}")
    if WS_MODE not in (1, 2, 3):
        raise ConfigError(f"WS_MODE must be 1, 2 or 3 (got {WS_MODE})")


_validate()


# ════════════════════════════════════════════════════════════════════════════
#  PUBLIC HELPERS
# ════════════════════════════════════════════════════════════════════════════
def total_capital() -> float:
    """Sum of all three silo caps.  Used by the global drawdown check."""
    return SILO_A_MAX_CAPITAL + SILO_B_MAX_CAPITAL + SILO_C_MAX_CAPITAL


def banner() -> str:
    """One-line summary for log boot lines."""
    return (
        f"angel-one-quant-bundle | paper={PAPER_TRADING} | "
        f"silos=[{SILO_A_NAME}:₹{SILO_A_MAX_CAPITAL:,.0f}, "
        f"{SILO_B_NAME}:₹{SILO_B_MAX_CAPITAL:,.0f}, "
        f"{SILO_C_NAME}:₹{SILO_C_MAX_CAPITAL:,.0f}] | "
        f"DD daily={DAILY_DRAWDOWN_PCT:.0%} global={GLOBAL_DRAWDOWN_PCT:.0%}"
    )


def setup_logging() -> None:
    """Idempotent root logger setup — call once from your entry point."""
    fmt = logging.Formatter(
        "%(asctime)s | %(name)-12s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        return
    root.addHandler(sh)
    root.setLevel(LOG_LEVEL)
