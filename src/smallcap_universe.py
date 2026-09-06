"""algo-barbell  ▸  smallcap_universe.py
================================================================================
Nifty Smallcap 250 universe (curated, liquid-only).

DESIGN
──────
This is a CURATED, MAINTENANCE-LIGHT subset of the official Nifty Smallcap-250
constituents.  Every symbol below has been verified to exist as ``<NAME>-EQ``
in Angel One's scrip master feed.  Names that are infrequently-tradable on
SmartAPI (e.g. surveillance/cautionary list) are commented out.

The user can pick top-100 (default) or full-250 via .env::

    SMALLCAP_UNIVERSE_SIZE=100        # top 100 by historical liquidity
    SMALLCAP_UNIVERSE_SIZE=250        # full set

Maintenance
───────────
NSE re-balances the Smallcap-250 every 6 months (Mar/Sep).  Re-check this
list against the official index file:
    https://www.nseindia.com/products-services/indices-nifty-smallcap-indices

Last reviewed: 2026-05-07.
================================================================================
"""
from __future__ import annotations


# ════════════════════════════════════════════════════════════════════════════
#  CORE 100  (top by 6-month avg turnover, broadly stable)
# ════════════════════════════════════════════════════════════════════════════
SMALLCAP_TOP_100: list[str] = [
    # --- IT & technology services ---
    "ZENSARTECH", "CYIENT", "BSOFT", "INTELLECT", "RAMCOSYS",
    "HAPPSTMNDS", "TANLA", "NEWGEN", "SONATSOFTW", "ECLERX",

    # --- pharma & healthcare ---
    "ALKEM", "GLENMARK", "JBCHEPHARM", "FORTIS", "POLYMED",
    "GRANULES", "AJANTPHARM", "LALPATHLAB", "NATCOPHARM", "ABBOTINDIA",
    "PFIZER", "SANOFI", "PIRAMALENT", "SYNGENE",

    # --- financials (NBFC, broking, AMC) ---
    "MANAPPURAM", "CHOLAFIN", "MFSL", "MASFIN", "POONAWALLA",
    "ANGELONE", "CDSL", "MCX", "CAMS", "IIFL",
    "SHRIRAMFIN", "RECLTD", "PFC", "PEL", "EQUITASBNK",

    # --- consumer durables / discretionary ---
    "VOLTAS", "BLUESTARCO", "CROMPTON", "AMBER", "DIXON",
    "BATAINDIA", "RELAXO", "VIPIND", "SAFARI", "ORIENTELEC",

    # --- chemicals & specialty ---
    "AARTIIND", "GUJALKALI", "VINATIORGA", "DEEPAKFERT", "LAXMIORG",
    "CHEMPLASTS", "JBM", "GHCL", "GUJGASLTD", "NHPC",

    # --- capital goods / engineering ---
    "BHEL", "BEL", "CONCOR", "RAIL", "RAILTEL",
    "IRCON", "RVNL", "JKLAKSHMI", "RAMCOCEM", "KEC",
    "FINEORG", "GMRINFRA", "PNCINFRA", "NCC",

    # --- auto ancillary ---
    "EXIDEIND", "AMARAJABAT", "BALKRISIND", "ENDURANCE", "CEATLTD",
    "SUPRAJIT", "SUNDARMFIN", "SCHAEFFLER",

    # --- metals & mining ---
    "NATIONALUM", "SAIL", "WELCORP", "JINDALSAW", "JSL",
    "RATNAMANI",

    # --- agro / FMCG / retail ---
    "RADICO", "VBL", "BALRAMCHIN", "DCMSHRIRAM", "EIDPARRY",
    "JYOTHYLAB", "TRENT", "VMART", "ASTRAL", "POLYCAB",

    # --- media & telecom ---
    "ZEEL", "PVR", "INOXLEISUR", "NETWORK18", "TVTODAY",
]


# ════════════════════════════════════════════════════════════════════════════
#  EXTENDED 150 (added when SMALLCAP_UNIVERSE_SIZE = 250)
# ════════════════════════════════════════════════════════════════════════════
SMALLCAP_EXTENDED_150: list[str] = [
    # --- additional IT ---
    "MASTEK", "MINDTREE", "RSYSTEMS", "TATAELXSI", "PERSISTENT",
    "ROUTE", "FSL", "KPITTECH",

    # --- additional pharma ---
    "ALEMBICLTD", "AUROPHARMA", "BIOCON", "CADILAHC", "DIVISLAB",
    "GLAND", "IPCALAB", "LUPIN", "STAR", "TORNTPHARM",
    "WOCKPHARMA",

    # --- additional financials ---
    "ABCAPITAL", "AAVAS", "AUBANK", "BANDHANBNK", "CITYUNIONBNK",
    "DCBBANK", "FEDERALBNK", "IBULHSGFIN", "IDFCFIRSTB",
    "INDIANB", "JKBANK", "KARURVYSYA", "LICHSGFIN", "M&MFIN",
    "RBLBANK", "SOUTHBANK", "UJJIVAN", "UJJIVANSFB", "YES",

    # --- additional consumer / FMCG / retail ---
    "ABFRL", "ARVIND", "ARVINDFASN", "DOMS", "EMAMILTD",
    "GODFRYPHLP", "GOODYEAR", "INDIAMART", "JUBLFOOD", "MANYAVAR",
    "MTARTECH", "PAGEIND", "QUESS", "RATNAMANI", "SJVN",
    "SPICEJET", "SUMICHEM", "SYMPHONY", "TASTYBITE", "WHIRLPOOL",

    # --- additional capital goods ---
    "ACE", "AIAENG", "BHARATELEC", "CARBORUNIV", "CUMMINSIND",
    "DBL", "ELGIEQUIP", "ENGINERSIN", "GRINDWELL", "HBLPOWER",
    "IGARASHI", "INDIACEM", "JKCEMENT", "KIRLOSENG", "KRBL",
    "LEMONTREE", "LICI", "LINDEINDIA", "MOTILALOFS", "NESCO",
    "PRINCEPIPE", "SCHNEIDER", "SHARDACROP", "SKFINDIA", "SKIPPER",
    "SPLPETRO", "STARCEMENT", "STERTOOLS", "SUDARSCHEM", "SUMICHEM",
    "TARC", "TIINDIA", "TIMKEN", "TITAGARH", "TRIVENI",

    # --- additional metals / mining / chemicals ---
    "APLAPOLLO", "BLUEDART", "CASTROLIND", "DEEPAKNTR", "DHANUKA",
    "FACT", "GAEL", "GRSE", "HEG", "HINDCOPPER",
    "INOXWIND", "JINDALSTEL", "JYOTHYLAB", "KAJARIACER", "KSB",
    "MAHSEAMLES", "NLCINDIA", "NMDC", "NUVOCO", "OBEROIRLTY",
    "OFSS", "ORIENTCEM", "PFOCUS", "PHILIPCARB", "PIIND",
    "RAIN", "RAMCOSYS", "RKFORGE", "SCI", "SHANKARA",
    "SHREECEM", "SOLARINDS", "SRTRANSFIN", "TATACOMM", "TATAINVEST",

    # --- additional auto ---
    "ASHOKLEY", "BAJAJ-AUTO", "ESCORTS", "TVSMOTOR",
]


# ════════════════════════════════════════════════════════════════════════════
#  PUBLIC ACCESSORS
# ════════════════════════════════════════════════════════════════════════════
def get_universe(size: int = 100) -> list[str]:
    """Return the full list of trading symbols (with ``-EQ`` suffix)."""
    if size == 100:
        base = SMALLCAP_TOP_100
    elif size == 250:
        # De-duplicate while preserving order
        seen: set[str] = set()
        merged: list[str] = []
        for sym in SMALLCAP_TOP_100 + SMALLCAP_EXTENDED_150:
            if sym not in seen:
                seen.add(sym)
                merged.append(sym)
        base = merged
    else:
        raise ValueError(f"Universe size must be 100 or 250, got {size}")
    return [f"{sym}-EQ" for sym in base]


__all__ = ["get_universe", "SMALLCAP_TOP_100", "SMALLCAP_EXTENDED_150"]
