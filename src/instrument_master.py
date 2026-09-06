"""algo-barbell  ▸  instrument_master.py
================================================================================
Multi-segment instrument resolver — NSE Cash, NSE F&O, MCX commodities.

WHY THIS EXISTS
───────────────
Angel One's SmartAPI requires a *symbol token* for every order.  The full
list of (symbol → token → expiry/strike) triples is published as a JSON
URL ("scrip master") that's refreshed daily.  We download once, cache it
locally, and provide three resolver functions:

    resolve_equity(symbol)       → (token, exchange)        for NSE cash
    resolve_option(underlying,   → (symbol, token, lot_size) for NSE F&O
                   strike, opt_type, expiry)
    resolve_commodity(symbol,    → (token, exchange)        for MCX
                      expiry)

CACHING
───────
We persist the JSON to ``instrument_cache.json`` next to this file with a
modification-time check — if the cache is from today (Asia/Kolkata), we
skip the download.  Otherwise we re-download (~10 MB).
================================================================================
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional
from urllib.request import urlopen
import ssl

import config

log = logging.getLogger(__name__)


# Angel One's official scrip master URL (HTTPS, refreshed daily ~08:00 IST).
_SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)

_CACHE_PATH = config.PROJECT_ROOT / "data" / "instrument_cache.json"


# ════════════════════════════════════════════════════════════════════════════
#  CACHE MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════
def _is_cache_fresh() -> bool:
    """Cache is fresh if it was modified today (Asia/Kolkata)."""
    if not _CACHE_PATH.exists():
        return False
    mtime = datetime.fromtimestamp(_CACHE_PATH.stat().st_mtime, tz=config.IST)
    today = datetime.now(config.IST).date()
    return mtime.date() == today


def _download_scrip_master() -> list[dict]:
    """Download the scrip master JSON and persist to disk."""
    log.info("Downloading Angel One scrip master from %s", _SCRIP_MASTER_URL)
    
    # Create SSL context that doesn't verify certificates (for macOS compatibility)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    with urlopen(_SCRIP_MASTER_URL, timeout=60, context=ssl_context) as resp:
        raw = resp.read()
    data = json.loads(raw)
    _CACHE_PATH.write_bytes(raw)
    log.info("Scrip master cached: %d instruments → %s", len(data), _CACHE_PATH)
    return data


def _load_master() -> list[dict]:
    """Return the scrip master list, downloading if cache is stale/missing."""
    if _is_cache_fresh():
        try:
            return json.loads(_CACHE_PATH.read_bytes())
        except Exception as exc:
            log.warning("Cache read failed (%s) — re-downloading", exc)
    return _download_scrip_master()


# ════════════════════════════════════════════════════════════════════════════
#  PUBLIC RESOLVERS
# ════════════════════════════════════════════════════════════════════════════
def resolve_equity(symbol: str) -> tuple[str, str]:
    """Look up an NSE equity by symbol (e.g. 'NIFTYBEES-EQ').

    Returns
    -------
    tuple[str, str]
        ``(token, exchange)`` ready for use in placeOrder / getCandleData.
    """
    sym = symbol.upper().strip()
    for inst in _load_master():
        if (
            inst.get("exch_seg") == "NSE"
            and inst.get("symbol", "").upper() == sym
            and inst.get("instrumenttype") == ""
        ):
            return str(inst["token"]), "NSE"
    raise LookupError(f"NSE equity not found in scrip master: {symbol}")


def resolve_commodity(
    name: str, expiry: Optional[str] = None
) -> tuple[str, str, str]:
    """Look up an MCX commodity (e.g. 'SILVERMIC').

    Parameters
    ----------
    name
        Underlying name (no expiry suffix).  e.g. 'SILVERMIC', 'GOLD'.
    expiry
        Optional expiry filter ('DDMONYY' format like '28MAY26').  When
        omitted, returns the *nearest* expiry contract.

    Returns
    -------
    tuple[str, str, str]
        ``(symbol, token, exchange)``.  Example: ``('SILVERMIC28MAY26FUT', '...', 'MCX')``.
    """
    name = name.upper().strip()
    candidates: list[dict] = []
    for inst in _load_master():
        if inst.get("exch_seg") != "MCX":
            continue
        sym = inst.get("symbol", "").upper()
        if not sym.startswith(name):
            continue
        candidates.append(inst)

    if not candidates:
        raise LookupError(f"MCX commodity not found: {name}")

    if expiry is not None:
        expiry_u = expiry.upper()
        for c in candidates:
            if expiry_u in c.get("symbol", "").upper():
                return c["symbol"], str(c["token"]), "MCX"
        raise LookupError(f"MCX {name} expiry {expiry} not found")

    # No expiry given — return nearest.  Angel encodes expiry inside symbol
    # name, so we sort by parsed expiry date.
    def _parse_expiry(inst: dict) -> datetime:
        try:
            return datetime.strptime(inst["expiry"], "%d%b%Y")
        except Exception:
            return datetime.max

    candidates.sort(key=_parse_expiry)
    chosen = candidates[0]
    return chosen["symbol"], str(chosen["token"]), "MCX"


def resolve_option(
    underlying: str,
    strike: float,
    opt_type: str,
    expiry: str,
) -> tuple[str, str, int]:
    """Look up an NSE F&O option contract.

    Parameters
    ----------
    underlying : str
        'NIFTY' or 'BANKNIFTY' or any other Angel-listed underlying.
    strike : float
        Strike price.
    opt_type : str
        'CE' for Call, 'PE' for Put.
    expiry : str
        Expiry in ``DDMONYYYY`` format with 4-digit year (e.g. '12MAY2026').
        This is the format Angel's scrip master ``expiry`` field returns,
        so callers can pass through the value from
        :func:`nearest_weekly_expiry` directly.

    Returns
    -------
    tuple[str, str, int]
        ``(symbol, token, lot_size)``.

    Raises
    ------
    LookupError if the contract does not exist in the scrip master.

    NOTES
    -----
    Angel's scrip master is inconsistent between the ``expiry`` field
    (4-digit year, e.g. ``"12MAY2026"``) and the ``symbol`` field
    (2-digit year, e.g. ``"NIFTY12MAY2624150PE"``).  We match by
    constructing the **exact expected symbol** from the 2-digit-year
    form and comparing equality — both more correct and more robust
    than substring matching (which would false-match on strikes that
    appear in the expiry digits for low-priced underlyings).
    """
    underlying = underlying.upper().strip()
    opt_type = opt_type.upper().strip()
    if opt_type not in ("CE", "PE"):
        raise ValueError(f"opt_type must be 'CE' or 'PE', got {opt_type!r}")

    # Build the exact symbol Angel uses inside the ``symbol`` field.
    # Accept both 4-digit-year ('12MAY2026') and 2-digit-year ('12MAY26') input.
    expiry_u = expiry.upper().strip()
    try:
        if len(expiry_u) == 9:        # DDMMMYYYY
            expiry_dt = datetime.strptime(expiry_u, "%d%b%Y")
        else:                         # DDMMMYY
            expiry_dt = datetime.strptime(expiry_u, "%d%b%y")
    except ValueError as exc:
        raise LookupError(f"bad expiry format {expiry!r}: {exc}") from exc
    expiry_short = expiry_dt.strftime("%d%b%y").upper()  # → '12MAY26'
    strike_int = int(round(strike))
    expected_symbol = f"{underlying}{expiry_short}{strike_int}{opt_type}"

    for inst in _load_master():
        if inst.get("exch_seg") != "NFO":
            continue
        if inst.get("instrumenttype") != "OPTIDX":
            continue
        if inst.get("name", "").upper() != underlying:
            continue
        if inst.get("symbol", "").upper() == expected_symbol:
            return inst["symbol"], str(inst["token"]), int(inst.get("lotsize", 0))

    raise LookupError(
        f"Option not found: {expected_symbol} (looked for {underlying} "
        f"{strike_int} {opt_type} {expiry_short})"
    )


# ════════════════════════════════════════════════════════════════════════════
#  CONVENIENCE — list expiries / nearest-expiry helpers
# ════════════════════════════════════════════════════════════════════════════
def list_option_expiries(underlying: str) -> list[str]:
    """Return all available expiries for an underlying, sorted ascending."""
    underlying = underlying.upper().strip()
    seen = set()
    for inst in _load_master():
        if inst.get("exch_seg") == "NFO" and inst.get("name", "").upper() == underlying:
            exp = inst.get("expiry", "")
            if exp:
                seen.add(exp)
    return sorted(seen, key=lambda e: datetime.strptime(e, "%d%b%Y"))


def resolve_index_future(name: str) -> tuple[str, str, int]:
    """Look up the nearest-expiry NSE index-future contract (NFO / FUTIDX).

    Used by the F&O engine as a *volume proxy* — spot indices (NIFTY,
    BANKNIFTY) themselves return ``volume=0`` from Angel's historical-candle
    endpoint because they are price-only indices.  Their nearest-expiry
    futures contract is the canonical liquidity stream the bot uses to
    validate breakout conviction.

    Parameters
    ----------
    name : str
        Underlying name as Angel encodes it in the scrip-master ``name``
        field (e.g. ``"NIFTY"``, ``"BANKNIFTY"``).

    Returns
    -------
    tuple[str, str, int]
        ``(trading_symbol, token, lot_size)``.  Example:
        ``("NIFTY29MAY25FUT", "53216", 75)``.

    Raises
    ------
    LookupError if no FUTIDX contract for the underlying exists.
    """
    name_u = name.upper().strip()
    candidates: list[dict] = []
    for inst in _load_master():
        if inst.get("exch_seg") != "NFO":
            continue
        if inst.get("instrumenttype") != "FUTIDX":
            continue
        if inst.get("name", "").upper() != name_u:
            continue
        candidates.append(inst)

    if not candidates:
        raise LookupError(f"No FUTIDX contract found for {name!r}")

    def _parse_expiry(inst: dict) -> datetime:
        try:
            return datetime.strptime(inst["expiry"], "%d%b%Y")
        except Exception:
            return datetime.max

    today = datetime.now(config.IST).date()
    upcoming = [c for c in candidates if _parse_expiry(c).date() >= today]
    if not upcoming:
        upcoming = candidates
    upcoming.sort(key=_parse_expiry)
    chosen = upcoming[0]
    return (
        chosen["symbol"],
        str(chosen["token"]),
        int(chosen.get("lotsize", 0)),
    )


def nearest_weekly_expiry(underlying: str = "NIFTY") -> str:
    """Return the nearest upcoming expiry in ``DDMONYYYY`` (4-digit year)
    format, e.g. ``"12MAY2026"``.

    Note that for BANKNIFTY (post-2024) only monthly contracts exist; for
    that underlying this returns the nearest monthly. Callers that want
    a strict weekly should filter by weekday themselves.
    """
    expiries = list_option_expiries(underlying)
    today = datetime.now(config.IST).date()
    for e in expiries:
        if datetime.strptime(e, "%d%b%Y").date() >= today:
            return e
    raise LookupError(f"No upcoming expiries for {underlying}")


# ════════════════════════════════════════════════════════════════════════════
#  CLI SELF-TEST
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log.info("Loading scrip master…")
    n = len(_load_master())
    log.info("Scrip master loaded: %d total instruments", n)

    # Spot-check the symbols our engines will hit
    for s in ("NIFTYBEES-EQ", "BANKBEES-EQ"):
        try:
            tok, exch = resolve_equity(s)
            log.info("  %-15s → token=%s exch=%s", s, tok, exch)
        except LookupError as e:
            log.error("  %s: %s", s, e)

    try:
        sym, tok, exch = resolve_commodity("SILVERMIC")
        log.info("  SILVERMIC      → %s token=%s exch=%s", sym, tok, exch)
    except LookupError as e:
        log.error("  SILVERMIC: %s", e)

    try:
        nxt = nearest_weekly_expiry("NIFTY")
        log.info("  Nearest NIFTY weekly expiry: %s", nxt)
    except LookupError as e:
        log.error("  NIFTY weekly: %s", e)
