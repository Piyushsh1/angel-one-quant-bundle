#!/usr/bin/env python3
"""
================================================================================
seed_demo_data.py  ▸  Single demo/seed utility
================================================================================
Replaces the seven previous one-off scripts (create_all_databases.py,
create_correct_schema_dbs.py, fix_demo_data.py, generate_demo_trades.py,
simulate_paper_trading.py, run_options_demo.py, run_paper_trading_demo.py),
each of which hand-wrote its own copy of the SQLite schema and drifted from
the real one.

This version writes through ``BotDB``, so seeded rows always match the
production schema exactly — there is only one schema definition in the
codebase now (``src/database.py``).

USAGE
─────
    python scripts/seed_demo_data.py --trades 40        # seed closed trades
    python scripts/seed_demo_data.py --open 3           # seed open positions
    python scripts/seed_demo_data.py --reset            # wipe seeded rows
    python scripts/seed_demo_data.py --reset --trades 40 --open 2

SAFETY
──────
Refuses to run when APP_ENV=prod. Seeded rows are tagged
``meta_json.seeded = true`` so ``--reset`` can remove exactly what it created
and never touches genuine trading history.
================================================================================
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config                                            # noqa: E402
import database                                           # noqa: E402
from database import ENGINES, BotDB                       # noqa: E402

log = logging.getLogger("seed")

#: Marker written into meta_json so seeded rows are always identifiable.
SEED_TAG = "seeded"

#: Plausible instruments per engine, so demo rows look like the real thing.
UNIVERSE = {
    "MACRO": [
        ("NIFTYBEES-EQ", "10576", "NSE", "DELIVERY"),
        ("GOLDBEES-EQ", "14428", "NSE", "DELIVERY"),
        ("JUNIORBEES-EQ", "10939", "NSE", "DELIVERY"),
    ],
    "SMALLCAP": [
        ("KPITTECH-EQ", "9683", "NSE", "DELIVERY"),
        ("CYIENT-EQ", "5748", "NSE", "DELIVERY"),
        ("SONATSOFTW-EQ", "13227", "NSE", "DELIVERY"),
    ],
    "OPTIONS": [
        ("NIFTY30OCT2626000CE", "43219", "NFO", "CARRYFORWARD"),
        ("BANKNIFTY29OCT2658000PE", "44871", "NFO", "CARRYFORWARD"),
        ("FINNIFTY28OCT2624500CE", "45102", "NFO", "CARRYFORWARD"),
    ],
}


def _guard_environment() -> None:
    """Refuse to seed a production database."""
    if config.IS_PROD:
        log.error("❌ Refusing to seed: APP_ENV=%s (production).", config.APP_ENV)
        log.error("   Point APP_ENV at dev or preprod first.")
        sys.exit(1)


def reset(engines: list[str]) -> int:
    """Delete only rows this script created. Returns rows removed."""
    removed = 0
    with database._pool_instance().connection() as c:
        for eng in engines:
            cur = c.execute(
                "DELETE FROM trade_history WHERE engine = %s "
                "AND meta_json LIKE %s",
                (eng, f'%"{SEED_TAG}":true%'),
            )
            removed += cur.rowcount or 0
            cur = c.execute(
                "DELETE FROM open_positions WHERE engine = %s "
                "AND meta_json LIKE %s",
                (eng, f'%"{SEED_TAG}":true%'),
            )
            removed += cur.rowcount or 0
            c.execute(
                "DELETE FROM daily_pnl WHERE engine = %s", (eng,)
            )
        c.commit()
    return removed


def seed_trades(engine: str, count: int) -> None:
    """Insert ``count`` completed round-trip trades with realistic P&L."""
    db = BotDB(engine)
    instruments = UNIVERSE[engine]
    today = datetime.now(config.IST)
    daily: dict[str, float] = {}

    for i in range(count):
        symbol, token, exch, ptype = random.choice(instruments)
        # Spread trades over the last 20 sessions.
        day_offset = random.randint(0, 19)
        entry_ts = (today - timedelta(days=day_offset)).replace(
            hour=random.randint(9, 12), minute=random.randint(0, 59)
        )
        exit_ts = entry_ts + timedelta(minutes=random.randint(20, 300))

        entry_px = round(random.uniform(80, 1400), 2)
        # ~55% winners, so the demo equity curve trends up but not absurdly.
        is_win = random.random() < 0.55
        move = random.uniform(0.01, 0.09) * (1 if is_win else -1)
        exit_px = round(entry_px * (1 + move), 2)
        qty = random.choice([1, 2, 5, 10, 25, 50])
        pnl = round((exit_px - entry_px) * qty, 2)

        reason = (
            random.choice(["TARGET", "TRAIL", "EOD"]) if is_win
            else random.choice(["SL", "EOD"])
        )

        db.record_trade(
            side="BUY", symbol=symbol, quantity=qty, price=entry_px,
            order_type="MARKET", order_id=f"SEED-B-{i:04d}",
            timestamp=entry_ts.isoformat(),
            meta={SEED_TAG: True, "leg": "entry"},
        )
        db.record_trade(
            side="SELL", symbol=symbol, quantity=qty, price=exit_px,
            order_type="MARKET", order_id=f"SEED-S-{i:04d}", pnl=pnl,
            timestamp=exit_ts.isoformat(),
            meta={SEED_TAG: True, "leg": "exit", "reason": reason},
        )
        key = exit_ts.strftime("%Y-%m-%d")
        daily[key] = daily.get(key, 0.0) + pnl

    for date_str, total in sorted(daily.items()):
        db.add_to_daily_pnl(date_str, round(total, 2))

    net = sum(daily.values())
    log.info("  %-9s seeded %d trades | net ₹%+,.2f across %d session(s)",
             engine, count, net, len(daily))


def seed_open_positions(engine: str, count: int) -> None:
    """Insert ``count`` currently-open positions."""
    db = BotDB(engine)
    instruments = random.sample(
        UNIVERSE[engine], min(count, len(UNIVERSE[engine]))
    )
    now = datetime.now(config.IST)

    for i, (symbol, token, exch, ptype) in enumerate(instruments):
        entry_px = round(random.uniform(80, 1400), 2)
        qty = random.choice([1, 2, 5, 10, 25])
        sl = round(entry_px * 0.94, 2)
        db.add_position(
            symbol=symbol, token=token, exchange=exch, product_type=ptype,
            quantity=qty, entry_price=entry_px, stop_loss=sl,
            entry_time=(now - timedelta(minutes=25 * (i + 1))).isoformat(),
            order_id=f"SEED-OPEN-{i:04d}",
            meta={SEED_TAG: True, "peak_premium": entry_px,
                  "trail_armed": False},
        )
    log.info("  %-9s seeded %d open position(s)", engine, len(instruments))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Seed demo data into the current environment's database."
    )
    ap.add_argument("--trades", type=int, default=0,
                    help="closed trades to seed per engine")
    ap.add_argument("--open", type=int, default=0, dest="open_positions",
                    help="open positions to seed per engine")
    ap.add_argument("--reset", action="store_true",
                    help="remove previously seeded rows first")
    ap.add_argument("--engine", choices=ENGINES, action="append",
                    help="limit to specific engine(s); repeatable")
    ap.add_argument("--seed", type=int, default=None,
                    help="RNG seed for reproducible output")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    if args.seed is not None:
        random.seed(args.seed)

    _guard_environment()
    engines = args.engine or list(ENGINES)

    log.info("=" * 70)
    log.info("  SEED DEMO DATA — env=%s db=%s", config.APP_ENV, config.DB_NAME)
    log.info("=" * 70)

    database.wait_for_database()
    # Touch every engine once so the schema exists before we write.
    for eng in engines:
        BotDB(eng)

    if args.reset:
        n = reset(engines)
        log.info("")
        log.info("🧹 reset: removed %d seeded row(s)", n)

    if args.trades:
        log.info("")
        log.info("📈 seeding %d trade(s) per engine", args.trades)
        for eng in engines:
            seed_trades(eng, args.trades)

    if args.open_positions:
        log.info("")
        log.info("📌 seeding %d open position(s) per engine", args.open_positions)
        for eng in engines:
            seed_open_positions(eng, args.open_positions)

    if not (args.reset or args.trades or args.open_positions):
        log.warning("Nothing to do. Pass --trades, --open and/or --reset.")
        log.warning(
            "Example: python scripts/seed_demo_data.py --reset --trades 40 --open 2"
        )

    database.close_pool()
    log.info("")
    log.info("✅ done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
