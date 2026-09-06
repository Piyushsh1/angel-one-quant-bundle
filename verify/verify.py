#!/usr/bin/env python3
"""
================================================================================
verify.py  ▸  Single preflight checker for every environment
================================================================================
Replaces the previous six overlapping scripts (verify_setup.py,
verify_live_setup.py, verify_live_ready.py, test_api_connection.py,
test_dashboard.py, test_simple_dashboard.py) with one tool that runs a
graduated set of checks.

USAGE
─────
    python verify/verify.py              # config + database + imports
    python verify/verify.py --broker     # also test the Angel One login
    python verify/verify.py --full       # everything, including live-readiness

EXIT CODE
─────────
    0  all selected checks passed
    1  at least one check failed

Checks scale with the environment: the live-readiness gate only demands
real-money preconditions when APP_ENV=prod.
================================================================================
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from pathlib import Path

# Make src/ importable regardless of where this script is invoked from.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OK = "✅"
BAD = "❌"
WARN = "⚠️ "


log = logging.getLogger("verify")


class Report:
    """Collects pass/fail results and logs a section-oriented summary.

    Routes through ``logging`` rather than ``print`` so results carry levels:
    passes are INFO, warnings are WARNING and failures are ERROR. That makes
    the output greppable and lets a CI job or cron wrapper filter on severity.
    """

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def section(self, title: str) -> None:
        log.info("")
        log.info(title)
        log.info("─" * 74)

    def ok(self, msg: str) -> None:
        log.info("  %s %s", OK, msg)

    def warn(self, msg: str) -> None:
        log.warning("  %s %s", WARN, msg)
        self.warnings.append(msg)

    def fail(self, msg: str) -> None:
        log.error("  %s %s", BAD, msg)
        self.failures.append(msg)

    def check(self, condition: bool, ok_msg: str, fail_msg: str) -> bool:
        if condition:
            self.ok(ok_msg)
        else:
            self.fail(fail_msg)
        return condition


# ════════════════════════════════════════════════════════════════════════════
#  1. CONFIG / ENVIRONMENT
# ════════════════════════════════════════════════════════════════════════════
def check_config(r: Report):
    r.section("1. Configuration")
    # Settings may arrive either from a local .env (bare-metal runs) or from
    # real environment variables (Docker, where compose supplies env_file).
    # Either is valid, so this is informational rather than a failure.
    if (ROOT / ".env").exists():
        r.ok(".env present")
    elif os.getenv("APP_ENV"):
        r.ok(f"config from environment (APP_ENV={os.getenv('APP_ENV')})")
    else:
        r.fail("no configuration found — create .env or supply env vars")
        return None

    try:
        import config
    except Exception as exc:                                       # noqa: BLE001
        r.fail(f"config.py failed to load: {exc}")
        return None

    r.ok(f"config loaded — env={config.APP_ENV} mode={config.TRADING_MODE}")

    for key in ("API_KEY", "CLIENT_ID", "PASSWORD", "TOTP_SECRET"):
        val = getattr(config, key, "")
        r.check(bool(val), f"{key} set", f"{key} missing")

    total = config.TOTAL_BARBELL_CAPITAL
    r.check(total > 0, f"capital configured: ₹{total:,.0f}", "capital is zero")

    # The safety interlock: live orders must never be possible outside prod.
    if config.PAPER_TRADING:
        r.ok(f"orders are SIMULATED (safe) — env={config.APP_ENV}")
    else:
        r.warn(
            f"LIVE ORDERS ENABLED — env={config.APP_ENV}. "
            f"Real money will be spent."
        )
    return config


# ════════════════════════════════════════════════════════════════════════════
#  2. DEPENDENCIES
# ════════════════════════════════════════════════════════════════════════════
def check_dependencies(r: Report) -> None:
    r.section("2. Python dependencies")
    required = [
        ("psycopg", "Postgres driver"),
        ("psycopg_pool", "Postgres connection pool"),
        ("pandas", "data frames"),
        ("dotenv", "env loading"),
        ("pyotp", "broker TOTP"),
    ]
    for mod, why in required:
        try:
            importlib.import_module(mod)
            r.ok(f"{mod} ({why})")
        except ImportError:
            r.fail(f"{mod} missing ({why}) — pip install -r requirements.txt")

    # SmartApi is the broker SDK; its absence only matters when trading.
    try:
        importlib.import_module("SmartApi")
        r.ok("SmartApi (broker SDK)")
    except ImportError:
        r.warn("SmartApi missing — broker calls will fail")


# ════════════════════════════════════════════════════════════════════════════
#  3. DATABASE
# ════════════════════════════════════════════════════════════════════════════
def check_database(r: Report, config) -> None:
    r.section("3. Database")
    log.info("  target: %s", config._redacted_dsn())
    try:
        import database
    except Exception as exc:                                       # noqa: BLE001
        r.fail(f"database.py import failed: {exc}")
        return

    try:
        database.wait_for_database(timeout_s=15.0, interval_s=2.0)
        r.ok(f"Postgres reachable (db={config.DB_NAME})")
    except Exception as exc:                                       # noqa: BLE001
        r.fail(f"Postgres unreachable: {exc}")
        r.warn("start it with: docker compose up -d postgres")
        return

    # Constructing a BotDB creates/validates the schema.
    try:
        for eng in database.ENGINES:
            db = database.BotDB(eng)
            n = len(db.open_positions())
            r.ok(f"engine {eng:<9} schema OK, {n} open position(s)")
    except Exception as exc:                                       # noqa: BLE001
        r.fail(f"schema check failed: {exc}")
        return
    finally:
        try:
            database.close_pool()
        except Exception:                                          # noqa: BLE001
            pass


# ════════════════════════════════════════════════════════════════════════════
#  4. ENGINE IMPORTS
# ════════════════════════════════════════════════════════════════════════════
def check_engine_imports(r: Report) -> None:
    r.section("4. Engine imports")
    for mod in ("option_predator", "macro_engine", "smallcap_engine",
                "regime_eod", "flatten_all", "execution", "broker", "auth"):
        try:
            importlib.import_module(mod)
            r.ok(f"{mod}")
        except Exception as exc:                                   # noqa: BLE001
            r.fail(f"{mod}: {type(exc).__name__}: {exc}")


# ════════════════════════════════════════════════════════════════════════════
#  5. BROKER CONNECTIVITY  (opt-in: performs a real login)
# ════════════════════════════════════════════════════════════════════════════
def check_broker(r: Report) -> None:
    r.section("5. Broker connectivity (Angel One)")
    try:
        import auth
    except Exception as exc:                                       # noqa: BLE001
        r.fail(f"auth import failed: {exc}")
        return

    api = None
    try:
        api = auth.login()
        r.ok("login succeeded — session established")
    except Exception as exc:                                       # noqa: BLE001
        r.fail(f"login failed: {exc}")
        r.warn("common causes: IP not allowlisted, wrong TOTP/mPIN, "
               "or outside broker login hours")
        return
    finally:
        if api is not None:
            try:
                auth.terminate(api)
            except Exception:                                      # noqa: BLE001
                pass


# ════════════════════════════════════════════════════════════════════════════
#  6. LIVE READINESS  (only strict when APP_ENV=prod)
# ════════════════════════════════════════════════════════════════════════════
def check_live_readiness(r: Report, config) -> None:
    r.section("6. Live-trading readiness")
    if not config.IS_PROD:
        r.ok(f"env={config.APP_ENV} — paper environment, live gates not required")
        return

    r.check(
        not config.PAPER_TRADING,
        "ALLOW_LIVE_ORDERS=true — real orders armed",
        "APP_ENV=prod but ALLOW_LIVE_ORDERS is false — orders stay simulated",
    )
    r.check(
        config.OPTIONS_DAILY_LOSS < 0,
        f"options kill switch set: ₹{config.OPTIONS_DAILY_LOSS:+,.0f}",
        "OPTIONS_DAILY_LOSS must be negative",
    )
    if config.LOG_DIR.exists():
        r.ok(f"log dir writable: {config.LOG_DIR}")
    else:
        r.fail(f"log dir missing: {config.LOG_DIR}")


# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Preflight verification for the algo-barbell stack."
    )
    ap.add_argument("--broker", action="store_true",
                    help="also perform a real Angel One login test")
    ap.add_argument("--full", action="store_true",
                    help="run every check, including broker + live readiness")
    args = ap.parse_args()

    # Plain message format: this is an operator-facing report, so the level
    # prefix would only add noise. Severity still rides on the record, so
    # `... 2>/dev/null` shows passes only and CI can filter on it.
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    log.info("=" * 74)
    log.info("  ALGO-BARBELL — PREFLIGHT VERIFICATION")
    log.info("=" * 74)

    r = Report()
    config = check_config(r)
    check_dependencies(r)

    if config is not None:
        check_database(r, config)
        check_engine_imports(r)
        if args.broker or args.full:
            check_broker(r)
        if args.full:
            check_live_readiness(r, config)

    log.info("")
    log.info("=" * 74)
    if r.failures:
        log.error("  %s %d CHECK(S) FAILED", BAD, len(r.failures))
        for f in r.failures:
            log.error("      · %s", f)
    else:
        log.info("  %s ALL CHECKS PASSED", OK)
    if r.warnings:
        log.warning("")
        log.warning("  %s %d warning(s):", WARN, len(r.warnings))
        for w in r.warnings:
            log.warning("      · %s", w)
    log.info("=" * 74)
    return 1 if r.failures else 0


if __name__ == "__main__":
    sys.exit(main())
