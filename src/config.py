"""algo-barbell  ▸  config.py
================================================================================
Strict capital-silo enforcement and centralised configuration loader.

This module is the *architectural anchor* of the entire system.  Every engine
imports from here.  Three rules are enforced at import time:

    1.  All three silo limits are typed numerics (no strings, no None).
    2.  The sum of silo limits is ALLOWED to differ from broker availablecash
        (we want explicit signal if user under/over-funds the account), but
        we LOG a warning when the gap is > 5% so operator notices.
    3.  Risk percentages are bounded ∈ (0, 1] — a typo like ``0.5`` would
        trigger a ``RiskBudgetError`` rather than silently risking 50% per trade.

The bots NEVER call ``rmsLimit()`` for sizing decisions.  Live broker margin
is informational only; all position-size math uses the hardcoded silo.
================================================================================
"""
from __future__ import annotations

import logging
import os
import re
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# ─── load .env — override=True so PM2-cached env can never shadow file ────
# config.py lives in src/, so the repo root is two levels up (src/ -> root).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=True)


# ════════════════════════════════════════════════════════════════════════════
#  HELPERS — typed env reader with defensive validation
# ════════════════════════════════════════════════════════════════════════════
class ConfigError(ValueError):
    """Raised when .env values are missing, malformed, or inconsistent."""


def _req(key: str) -> str:
    """Return os.getenv(key); raise ConfigError if missing/blank."""
    val = os.getenv(key, "").strip()
    if not val:
        raise ConfigError(f"Missing required env var: {key}")
    return val


def _int(key: str, default: int | None = None) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        if default is None:
            raise ConfigError(f"Missing required int env var: {key}")
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"Env var {key}={raw!r} is not a valid int")


def _float(key: str, default: float | None = None) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        if default is None:
            raise ConfigError(f"Missing required float env var: {key}")
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"Env var {key}={raw!r} is not a valid float")


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "y")


def _pct(key: str, default: float | None = None) -> float:
    """Risk percentage that MUST be in (0, 1].  0.015 = 1.5%."""
    val = _float(key, default)
    if not (0.0 < val <= 1.0):
        raise ConfigError(
            f"Env var {key}={val!r} must satisfy 0 < {key} ≤ 1.0  "
            f"(e.g. 0.015 for 1.5%, NOT 1.5)"
        )
    return val


def _hhmm(key: str) -> dtime:
    """Parse HH:MM string into datetime.time."""
    raw = _req(key)
    try:
        hh, mm = raw.split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        raise ConfigError(f"Env var {key}={raw!r} is not HH:MM format")


def _hhmm_opt(key: str, default: dtime) -> dtime:
    """Parse HH:MM, return ``default`` if env var missing or blank."""
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        hh, mm = raw.split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        raise ConfigError(f"Env var {key}={raw!r} is not HH:MM format")


# ════════════════════════════════════════════════════════════════════════════
#  DEPLOYMENT ENVIRONMENT  (dev / preprod / prod)
# ════════════════════════════════════════════════════════════════════════════
# The SAME application image is deployed to three environments.  The
# environment decides two things and nothing else:
#
#     1.  WHICH DATABASE it talks to  (one Postgres DB per environment, so
#         dev/preprod/prod data can never mix).
#     2.  WHETHER orders are simulated or sent to the broker for real.
#
#         dev      → paper: orders simulated, live market data, safe sandbox
#         preprod  → paper: orders simulated against live data, separate DB
#                     (a full production rehearsal with no money at risk)
#         prod     → LIVE:  real orders, real money
#
# Live orders are refused unless APP_ENV=prod *and* ALLOW_LIVE_ORDERS=true.
# That double gate means a mis-set APP_ENV can never silently spend money.
VALID_ENVS = ("dev", "preprod", "prod")
APP_ENV: str = os.getenv("APP_ENV", "dev").strip().lower()
if APP_ENV not in VALID_ENVS:
    raise ConfigError(
        f"APP_ENV={APP_ENV!r} invalid — must be one of {VALID_ENVS}"
    )

IS_DEV: bool = APP_ENV == "dev"
IS_PREPROD: bool = APP_ENV == "preprod"
IS_PROD: bool = APP_ENV == "prod"

#: True when orders must be SIMULATED instead of sent to the broker.
#: Fail-safe: anything other than an explicit prod+opt-in stays on paper.
PAPER_TRADING: bool = not (IS_PROD and _bool("ALLOW_LIVE_ORDERS", False))

# Legacy alias.  Older .env files carry OPTIONS_PAPER_TRADING; if EITHER the
# global switch or the legacy key says "paper", we stay on paper.
OPTIONS_PAPER_TRADING: bool = PAPER_TRADING or _bool("OPTIONS_PAPER_TRADING", True)
OPTIONS_PAPER_MODE: bool = OPTIONS_PAPER_TRADING  # back-compat alias

#: Human-readable mode label used in logs, the dashboard banner and alerts.
TRADING_MODE: str = "LIVE" if not PAPER_TRADING else "PAPER"


# ════════════════════════════════════════════════════════════════════════════
#  ANGEL ONE CREDENTIALS
# ════════════════════════════════════════════════════════════════════════════
API_KEY: str = _req("API_KEY")
CLIENT_ID: str = _req("CLIENT_ID")
PASSWORD: str = _req("PASSWORD")
TOTP_SECRET: str = _req("TOTP_SECRET")


# ════════════════════════════════════════════════════════════════════════════
#  CAPITAL SILOS  (strict isolation; broker margin is informational only)
# ════════════════════════════════════════════════════════════════════════════
MACRO_MAX_CAPITAL: float = _float("MACRO_MAX_CAPITAL")
SMALLCAP_MAX_CAPITAL: float = _float("SMALLCAP_MAX_CAPITAL")
OPTIONS_MAX_CAPITAL: float = _float("OPTIONS_MAX_CAPITAL")

#: Safety cushion (fraction kept idle inside each silo)
MARGIN_CUSHION_PCT: float = _pct("MARGIN_CUSHION_PCT", 0.10)

#: Convenience: total nominal capital (informational only)
TOTAL_BARBELL_CAPITAL: float = (
    MACRO_MAX_CAPITAL + SMALLCAP_MAX_CAPITAL + OPTIONS_MAX_CAPITAL
)


# ════════════════════════════════════════════════════════════════════════════
#  RISK PARAMETERS
# ════════════════════════════════════════════════════════════════════════════
# ---  Macro engine  -------------------------------------------------------
MACRO_RISK_PCT: float = _pct("MACRO_RISK_PCT", 0.03)
MACRO_ATR_SL_MULT: float = _float("MACRO_ATR_SL_MULT", 2.0)
MACRO_DONCHIAN_LOOKBACK: int = _int("MACRO_DONCHIAN_LOOKBACK", 10)
MACRO_RSI_PERIOD: int = _int("MACRO_RSI_PERIOD", 14)
MACRO_RSI_THRESHOLD: float = _float("MACRO_RSI_THRESHOLD", 55.0)

# ---  Smallcap engine  ----------------------------------------------------
SMALLCAP_RISK_PCT: float = _pct("SMALLCAP_RISK_PCT", 0.015)
SMALLCAP_ATR_SL_MULT: float = _float("SMALLCAP_ATR_SL_MULT", 2.0)
SMALLCAP_BB_PERIOD: int = _int("SMALLCAP_BB_PERIOD", 20)
SMALLCAP_BB_STD: float = _float("SMALLCAP_BB_STD", 2.0)
SMALLCAP_VOL_MULT: float = _float("SMALLCAP_VOL_MULT", 2.5)
SMALLCAP_MIN_TURNOVER: float = _float("SMALLCAP_MIN_TURNOVER", 5_000_000)
SMALLCAP_UNIVERSE_SIZE: int = _int("SMALLCAP_UNIVERSE_SIZE", 100)
if SMALLCAP_UNIVERSE_SIZE not in (100, 250):
    raise ConfigError(
        f"SMALLCAP_UNIVERSE_SIZE must be 100 or 250 (got {SMALLCAP_UNIVERSE_SIZE})"
    )

# ---  Options engine  -----------------------------------------------------
OPTIONS_DAILY_LOSS: float = _float("OPTIONS_DAILY_LOSS", -1500.0)
if OPTIONS_DAILY_LOSS >= 0:
    raise ConfigError(
        f"OPTIONS_DAILY_LOSS must be negative (got {OPTIONS_DAILY_LOSS})"
    )
OPTIONS_BREAKOUT_WINDOW_MINS: int = _int("OPTIONS_BREAKOUT_WINDOW_MINS", 15)
OPTIONS_HARD_EXIT_TIME: dtime = _hhmm("OPTIONS_HARD_EXIT_TIME")

# Breakout-volume confirmation gate: a 1-min FUTIDX bar must clear
# ``OPTIONS_VOL_MULT × OR_avg_vol`` for the price-break to convert to an
# entry.  Default 1.5 = "deliberate, not dormant" (calibrated post the
# 5-day audit-week 881-bar sample).  Set higher (2.0–3.0) to throttle
# trades; lower (1.0) to widen the funnel toward Day-2 frequencies.
OPTIONS_VOL_MULT: float = _float("OPTIONS_VOL_MULT", 1.5)
if OPTIONS_VOL_MULT <= 0:
    raise ConfigError(
        f"OPTIONS_VOL_MULT must be positive (got {OPTIONS_VOL_MULT})"
    )

# Defensive floor on the futures OR average volume.  Live-market incident on
# 2026-05-21 saw BANKNIFTY's OR avg_vol come back as 0 (broker artefact /
# fetch failure during 09:15–09:30) which made the vol-gate threshold
# 1.5×0 = 0 — a degenerate gate that every breakout trivially passes.
# When ``OR_avg_vol < OPTIONS_MIN_FUTURES_OR_VOL`` the engine refuses to
# trade THAT underlying for the entire day.  Other underlyings are
# unaffected.  Default 50 contracts (= 1 lot equiv per second over 60s).
OPTIONS_MIN_FUTURES_OR_VOL: int = _int("OPTIONS_MIN_FUTURES_OR_VOL", 50)
if OPTIONS_MIN_FUTURES_OR_VOL < 0:
    raise ConfigError(
        f"OPTIONS_MIN_FUTURES_OR_VOL must be ≥ 0 (got {OPTIONS_MIN_FUTURES_OR_VOL})"
    )

# NOTE: OPTIONS_PAPER_TRADING / OPTIONS_PAPER_MODE are now derived from
# APP_ENV in the DEPLOYMENT ENVIRONMENT section near the top of this file.
# They are kept as aliases of the global PAPER_TRADING switch so that a
# single environment variable governs every engine.


# ── V3 Alpha Layer  ────────────────────────────────────────────────────────
# HTF Daily Bias filter: at boot we fetch NIFTY 50 daily candles, compute the
# N-period SMA, and label the day BULLISH if prior_close > SMA else BEARISH.
# In the 1-min loop, a breakout is rejected if its direction conflicts with
# the bias (CE-only when BULLISH, PE-only when BEARISH).  Disable with
# OPTIONS_HTF_BIAS_ENABLED=false to fall back to V2 behaviour.
OPTIONS_HTF_BIAS_ENABLED: bool = _bool("OPTIONS_HTF_BIAS_ENABLED", True)
OPTIONS_HTF_SMA_PERIOD: int = _int("OPTIONS_HTF_SMA_PERIOD", 50)
if OPTIONS_HTF_SMA_PERIOD < 5:
    raise ConfigError(
        f"OPTIONS_HTF_SMA_PERIOD must be ≥ 5 (got {OPTIONS_HTF_SMA_PERIOD})"
    )

# Intraday Dead Zone: a fixed IST window during which NEW entries are
# suppressed.  Existing positions continue to be managed (SL / trail / hard
# exit / kill switch all live).  Default = 11:30–13:00 IST (classic lunch
# chop / institutional rebalance window).
OPTIONS_DEAD_ZONE_START: dtime = _hhmm_opt("OPTIONS_DEAD_ZONE_START", dtime(11, 30))
OPTIONS_DEAD_ZONE_END:   dtime = _hhmm_opt("OPTIONS_DEAD_ZONE_END",   dtime(13, 0))
if OPTIONS_DEAD_ZONE_START >= OPTIONS_DEAD_ZONE_END:
    raise ConfigError(
        f"OPTIONS_DEAD_ZONE_START ({OPTIONS_DEAD_ZONE_START}) must be "
        f"earlier than OPTIONS_DEAD_ZONE_END ({OPTIONS_DEAD_ZONE_END})"
    )


# ════════════════════════════════════════════════════════════════════════════
#  EXECUTION TUNING
# ════════════════════════════════════════════════════════════════════════════
TICK_SIZE: float = _float("TICK_SIZE", 0.05)
LIMIT_BUFFER: float = _float("LIMIT_BUFFER", 0.05)
API_MAX_RETRIES: int = _int("API_MAX_RETRIES", 3)
API_BACKOFF_BASE: float = _float("API_BACKOFF_BASE", 2.0)
API_PACING_SECONDS: float = _float("API_PACING_SECONDS", 0.5)
MAX_OPS: int = _int("MAX_OPS", 10)


# ════════════════════════════════════════════════════════════════════════════
#  DATABASE  (single PostgreSQL database per environment)
# ════════════════════════════════════════════════════════════════════════════
# One schema for the whole application.  Engines are isolated by an ``engine``
# discriminator column, NOT by separate database files.  Environments are
# isolated by having a separate database each (algo_barbell_dev / _preprod /
# _prod), so a dev run can never write into production state.
#
# Supply either a full DATABASE_URL, or the individual parts below.
DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_PORT: int = _int("DB_PORT", 5432)
DB_NAME: str = os.getenv("DB_NAME", f"algo_barbell_{APP_ENV}")
DB_USER: str = os.getenv("DB_USER", "algo")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "algo")

#: Full libpq connection string used by every engine and the dashboard.
DATABASE_URL: str = os.getenv("DATABASE_URL", "").strip() or (
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

#: Connection statement timeout (ms) — stops a wedged query pinning an engine.
DB_STATEMENT_TIMEOUT_MS: int = _int("DB_STATEMENT_TIMEOUT_MS", 15_000)

#: Local scratch dir (backtest candle cache, instrument master cache).
#: This is NOT trading state — trading state lives in Postgres.
DATA_DIR: Path = PROJECT_ROOT / os.getenv("DATA_DIR", "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════════════════════════════════════
#  TELEMETRY
# ════════════════════════════════════════════════════════════════════════════
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR: Path = PROJECT_ROOT / os.getenv("LOG_DIR", "logs")
LOG_DIR.mkdir(exist_ok=True)


# ════════════════════════════════════════════════════════════════════════════
#  TIMEZONE — every bot uses Asia/Kolkata for trading-window math
# ════════════════════════════════════════════════════════════════════════════
IST = ZoneInfo("Asia/Kolkata")


# ════════════════════════════════════════════════════════════════════════════
#  DERIVED HELPERS — useful constants exposed to engines
# ════════════════════════════════════════════════════════════════════════════
def usable_silo(silo_capital: float) -> float:
    """Return ``silo_capital × (1 - MARGIN_CUSHION_PCT)``.

    This is the amount the bot may actually deploy.  The cushion absorbs
    adverse ticks and rounding drift.
    """
    return silo_capital * (1.0 - MARGIN_CUSHION_PCT)


def risk_budget(silo_capital: float, risk_pct: float) -> float:
    """Return absolute ₹ amount that may be risked per trade."""
    return silo_capital * risk_pct


# ════════════════════════════════════════════════════════════════════════════
#  STARTUP BANNER  (printed by every engine that imports this module)
# ════════════════════════════════════════════════════════════════════════════
def banner() -> str:
    return (
        f"╔══════════════════════════════════════════════════════════════════╗\n"
        f"║  ALGO-BARBELL  ▸  Strict Capital Silos                           ║\n"
        f"╠══════════════════════════════════════════════════════════════════╣\n"
        f"║  Macro     ₹{MACRO_MAX_CAPITAL:>10,.0f}  ×  {MACRO_RISK_PCT*100:>4.1f}%  "
        f"= ₹{risk_budget(MACRO_MAX_CAPITAL, MACRO_RISK_PCT):>5,.0f} risk/trade   ║\n"
        f"║  Smallcap  ₹{SMALLCAP_MAX_CAPITAL:>10,.0f}  ×  {SMALLCAP_RISK_PCT*100:>4.1f}%  "
        f"= ₹{risk_budget(SMALLCAP_MAX_CAPITAL, SMALLCAP_RISK_PCT):>5,.0f} risk/trade   ║\n"
        f"║  Options   ₹{OPTIONS_MAX_CAPITAL:>10,.0f}  daily-loss kill ₹"
        f"{OPTIONS_DAILY_LOSS:>+7,.0f}    ║\n"
        f"║  Total     ₹{TOTAL_BARBELL_CAPITAL:>10,.0f}  cushion {MARGIN_CUSHION_PCT*100:>4.1f}%"
        f"                          ║\n"
        f"║  Environment: {APP_ENV:<8}  mode: {TRADING_MODE:<5}  db: {DB_NAME:<22}"
        f"║\n"
        f"╚══════════════════════════════════════════════════════════════════╝"
    )


def _redacted_dsn() -> str:
    """Return DATABASE_URL with only the password masked, safe for logs.

    Masks the credentials segment specifically rather than doing a blind
    string replace: a password like "algo" would otherwise also blank out the
    username and the database name (``algo_barbell_dev`` → ``***_barbell_dev``),
    leaving operators unable to tell which database a process is talking to.
    """
    # postgresql://user:password@host:port/dbname  →  mask just `password`
    return re.sub(r"://([^:/?#@]+):[^@]*@", r"://\1:***@", DATABASE_URL)


# ════════════════════════════════════════════════════════════════════════════
#  SELF-TEST  (run `python config.py` to validate the .env)
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Bare %(message)s keeps the banner's box-drawing readable while still
    # routing through logging, so output is capturable and level-aware.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _log = logging.getLogger("config")

    for _line in banner().splitlines():
        _log.info(_line)
    _log.info("")
    _log.info("Project root : %s", PROJECT_ROOT)
    _log.info("Log dir      : %s", LOG_DIR)
    _log.info("Data dir     : %s", DATA_DIR)
    _log.info("")
    _log.info("Environment  : %s", APP_ENV)
    _log.info(
        "Trading mode : %s%s",
        TRADING_MODE,
        "  (orders simulated)" if PAPER_TRADING else "  (real money at risk)",
    )
    _log.info("Database     : %s", _redacted_dsn())
    _log.info("")
    _log.info("Hard exit (options) : %s IST", OPTIONS_HARD_EXIT_TIME)
    _log.info("Smallcap universe   : top %d liquid", SMALLCAP_UNIVERSE_SIZE)
    _log.info("")
    if PAPER_TRADING:
        _log.info("✅  config.py loaded successfully — all silos validated.")
    else:
        _log.warning(
            "⚠️  config.py loaded — LIVE MODE: real orders are armed."
        )
