"""algo-barbell  >  flatten_all.py
================================================================================
EMERGENCY FLATTEN — sells every open position across all three engines.

Reads ``open_positions`` for each engine from the shared application database,
places a SELL MARKET order for each (through the same execution gateway the
engines use), records the exit in ``trade_history``, and removes the row from
``open_positions``.

In any paper environment (dev / preprod) no broker call is made — the
positions are not real, so each row is simply closed out in the database so
the engines restart clean.

USAGE
-----
CLI (panic):
    python src/flatten_all.py            # dry-run; lists what it WOULD do
    python src/flatten_all.py --execute  # actually places SELL MARKET orders

Dashboard:
    Operator Console -> Controls tab -> "FLATTEN" button (requires typed
    confirmation).

EXIT CODE
---------
    0 if everything succeeded (or dry-run completed).
    1 if any individual flatten failed (others still attempted).
================================================================================
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

import config
import execution
from auth import login, terminate
from database import ENGINES, BotDB, wait_for_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
)
log = logging.getLogger("flatten")


def _flatten_engine(label: str, *, execute: bool, api) -> tuple[int, int]:
    """Flatten one engine's positions. Returns ``(succeeded, failed)``."""
    db = BotDB(label)
    positions = db.open_positions()
    if not positions:
        log.info("[%s] no open positions", label)
        return 0, 0

    # In dev / preprod nothing was ever sent to the broker, so there is no
    # real position to sell — the rows are simply closed out.
    is_paper_db = config.PAPER_TRADING

    log.warning("[%s] %d open position(s) found", label, len(positions))
    succeeded = failed = 0

    for pos in positions:
        sym = pos["symbol"]
        qty = int(pos["quantity"] or 0)
        token = pos["token"]
        exch = pos["exchange"]
        ptype = pos["product_type"]

        if qty <= 0:
            log.error("[%s] %s qty=%d invalid; skipping", label, sym, qty)
            failed += 1
            continue

        # Paper environment: do NOT touch broker, just clean up the row.
        if is_paper_db:
            if not execute:
                log.info("[%s] DRY-RUN would purge paper row %s qty=%d", label, sym, qty)
                continue
            db.close_position(
                side="SELL",
                symbol=sym,
                quantity=qty,
                price=float(pos["entry_price"]),
                order_type="MARKET",
                pnl=0.0,
                meta={"flatten": "paper-purge"},
            )
            log.warning("[%s] PAPER row purged: %s qty=%d", label, sym, qty)
            succeeded += 1
            continue

        if not execute:
            log.info(
                "[%s] DRY-RUN would SELL %dx %s on %s/%s",
                label, qty, sym, exch, ptype,
            )
            continue

        if api is None:
            log.error("[%s] cannot execute — no broker session", label)
            failed += 1
            continue

        result = execution.place_market(
            api,
            symbol=sym,
            token=token,
            exchange=exch,
            side="SELL",
            quantity=qty,
            product_type=ptype,
            wait_for_fill_s=10.0,
        )
        if result is None:
            log.critical("[%s] FLATTEN FAILED for %s — manual intervention required", label, sym)
            failed += 1
            continue

        order_id, avg_px = result
        entry_px = float(pos["entry_price"])
        pnl = (avg_px - entry_px) * qty
        db.close_position(
            side="SELL",
            symbol=sym,
            quantity=qty,
            price=avg_px,
            order_type="MARKET",
            order_id=order_id,
            pnl=pnl,
            meta={"flatten": "emergency", "entry_price": entry_px},
        )
        log.warning(
            "[%s] FLATTENED %s qty=%d @ %.2f | entry=%.2f pnl=%+.2f oid=%s",
            label, sym, qty, avg_px, entry_px, pnl, order_id,
        )
        succeeded += 1

    return succeeded, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Emergency flatten all open positions across all silos.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually place SELL MARKET orders. Without this flag, runs in dry-run mode.",
    )
    args = parser.parse_args()

    log.warning("=" * 70)
    log.warning("EMERGENCY FLATTEN — mode=%s", "EXECUTE" if args.execute else "DRY-RUN")
    log.warning("env=%s trading=%s", config.APP_ENV, config.TRADING_MODE)
    log.warning("started at %s IST", datetime.now(config.IST).isoformat())
    log.warning("=" * 70)

    wait_for_database()

    # Defer login until we actually need it (keeps dry-run cheap).  A broker
    # session is only required when real orders will be transmitted.
    api = None
    if args.execute and not config.PAPER_TRADING:
        log.info("Logging in to Angel One for live SELL orders...")
        try:
            api = login()
        except Exception as exc:
            log.critical("Login failed — cannot execute. %s", exc)
            return 1

    total_ok = total_fail = 0
    try:
        for label in ENGINES:
            ok, fail = _flatten_engine(label, execute=args.execute, api=api)
            total_ok += ok
            total_fail += fail
    finally:
        if api is not None:
            terminate(api)

    log.warning("=" * 70)
    log.warning("FLATTEN COMPLETE — succeeded=%d failed=%d", total_ok, total_fail)
    log.warning("=" * 70)
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
