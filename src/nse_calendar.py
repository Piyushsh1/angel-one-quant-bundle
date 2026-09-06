"""NSE trading calendar helpers.

Centralised holiday list for the National Stock Exchange (cash market).
Used by ``swing_engine.py`` to skip cron runs on holidays so we don't
submit orders the exchange will silently park as AMOs (or worse, reject).

⚠ MAINTENANCE
─────────────
Update ``NSE_HOLIDAYS`` every December once NSE publishes the next year's
holiday circular.  Source of truth:

    https://www.nseindia.com/resources/exchange-communication-holidays

Variable-date holidays (Holi, Eid, Ganesh Chaturthi, Diwali, Dussehra,
Guru Nanak Jayanti, etc.) shift each year and **must** be re-checked.
Fixed-date holidays (Republic Day, Maharashtra Day, Independence Day,
Gandhi Jayanti, Christmas) are stable.

This module is deliberately offline-only — no network calls — so the
swing-bot can decide its own fate even if the EC2 instance loses
internet between cron firings.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import zoneinfo

IST = zoneinfo.ZoneInfo("Asia/Kolkata")


# ─────────────────────────────────────────────────────────────────────
# NSE Cash-Market Holidays — 2026
# ─────────────────────────────────────────────────────────────────────
# CONFIRMED FIXED DATES (national holidays, do not move year-on-year):
#   Jan 26  Republic Day
#   May  1  Maharashtra Day      ← today (confirmed by user 2026-05-01)
#   Aug 15  Independence Day
#   Oct  2  Mahatma Gandhi Jayanti
#   Dec 25  Christmas
#
# VARIABLE-DATE — the following are *best-effort estimates* based on
# typical lunar/festival calendars.  Cross-check against the official
# NSE 2026 holiday circular and amend before relying on it for live
# trading.  Each entry has a comment indicating confidence level.
NSE_HOLIDAYS_2026: frozenset[date] = frozenset({
    # ---- FIXED (high confidence) ------------------------------------
    date(2026, 1, 26),   # Republic Day (Mon)
    date(2026, 5,  1),   # Maharashtra Day (Fri) ← confirmed
    date(2026, 8, 15),   # Independence Day (Sat — already weekend)
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti (Fri)
    date(2026, 12, 25),  # Christmas (Fri)

    # ---- CHRISTIAN (high confidence — fixed Easter calendar) --------
    date(2026, 4,  3),   # Good Friday (Fri)

    # ---- HINDU / SIKH / MUSLIM (estimates — VERIFY) -----------------
    # date(2026, 2, 17),   # Mahashivratri  ← verify
    # date(2026, 3,  6),   # Holi           ← verify
    # date(2026, 3, 21),   # Eid-ul-Fitr    ← verify
    # date(2026, 5, 27),   # Bakri Id       ← verify
    # date(2026, 9,  7),   # Ganesh Chaturthi ← verify
    # date(2026, 10, 21),  # Dussehra       ← verify
    # date(2026, 11,  9),  # Diwali Balipratipada (Laxmi Pujan = Sun) ← verify
    # date(2026, 11, 24),  # Guru Nanak Jayanti ← verify
})


# ─────────────────────────────────────────────────────────────────────
# Aggregated holiday set across all years we know about
# ─────────────────────────────────────────────────────────────────────
NSE_HOLIDAYS: frozenset[date] = NSE_HOLIDAYS_2026


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def is_weekend(d: date) -> bool:
    """Saturday (5) or Sunday (6) — NSE cash market is shut."""
    return d.weekday() >= 5


def is_nse_holiday(d: date) -> bool:
    """True if `d` is in the maintained NSE holiday list."""
    return d in NSE_HOLIDAYS


def is_nse_trading_day(d: date | None = None) -> bool:
    """Return True iff NSE cash market is open on `d` (default: today IST).

    Combines weekend check + holiday list.  Does NOT check whether *now*
    falls inside the 09:15–15:30 trading session — that's the strategy
    layer's job.  This function answers the day-level question only.
    """
    if d is None:
        d = datetime.now(IST).date()
    return not is_weekend(d) and not is_nse_holiday(d)


def next_trading_day(d: date | None = None) -> date:
    """Smallest date strictly after `d` (default: today IST) that is a trading day."""
    if d is None:
        d = datetime.now(IST).date()
    nxt = d + timedelta(days=1)
    # Bound the search at 30 days as a safety net — never expected to be needed.
    for _ in range(30):
        if is_nse_trading_day(nxt):
            return nxt
        nxt += timedelta(days=1)
    raise RuntimeError(
        f"Could not find a trading day within 30 days after {d} — "
        "is NSE_HOLIDAYS misconfigured (e.g. an entire month listed)?"
    )


def reason_market_closed(d: date | None = None) -> str | None:
    """Human-readable reason why `d` is closed, or None if it's a trading day."""
    if d is None:
        d = datetime.now(IST).date()
    if is_weekend(d):
        return f"weekend ({d.strftime('%A')})"
    if is_nse_holiday(d):
        return f"NSE holiday on {d.isoformat()}"
    return None


__all__ = [
    "IST",
    "NSE_HOLIDAYS",
    "NSE_HOLIDAYS_2026",
    "is_weekend",
    "is_nse_holiday",
    "is_nse_trading_day",
    "next_trading_day",
    "reason_market_closed",
]
