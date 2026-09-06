"""algo-barbell  ▸  auth.py
================================================================================
Shared Angel One SmartAPI authentication — used by all three engines.

DESIGN
──────
This is a *factory function*, not a singleton.  Each engine calls
:func:`login()` at the start of its run and gets back its OWN authenticated
``SmartConnect`` instance.  The engines never share a session object —
this keeps the API rate-limit budget bounded per process.

LESSONS BAKED IN
────────────────
* TOTP can roll over between generation and submission (race condition near
  the 30s boundary).  We retry up to 2 times with a 2-second sleep.
* Login itself can hit Angel rate-limits when many engines start within
  the same minute.  Wrapped with the shared ``retry_api`` decorator.
* JWT tokens are stored in the returned object; engines should call
  ``api.terminateSession(client_id)`` on shutdown to free the slot.

USAGE
─────
    from auth import login
    api = login()                       # returns authenticated SmartConnect
    candles = api.getCandleData({...})
    api.terminateSession(config.CLIENT_ID)
================================================================================
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import pyotp
from SmartApi import SmartConnect

import config
from retry import retry_api

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
#  LOW-LEVEL LOGIN  (TOTP race-aware)
# ════════════════════════════════════════════════════════════════════════════
@retry_api(max_attempts=3, base_delay=2.0, raise_on_none=False)
def _generate_session(api: SmartConnect, client_id: str, password: str, totp: str):
    """Wrapped SmartConnect.generateSession with retry on transient errors."""
    return api.generateSession(client_id, password, totp)


def _try_totp_with_retry(
    api: SmartConnect,
    client_id: str,
    password: str,
    totp_secret: str,
    max_totp_attempts: int = 2,
) -> dict:
    """Generate fresh TOTP and call generateSession; retry once if Angel
    complains about an "Invalid totp" (which happens on the 30s rollover).
    """
    last_resp = None
    for attempt in range(1, max_totp_attempts + 1):
        totp = pyotp.TOTP(totp_secret).now()
        log.info("Login attempt %d/%d for %s", attempt, max_totp_attempts, client_id)
        resp = _generate_session(api, client_id, password, totp)
        if resp and resp.get("status"):
            log.info("Authentication successful — tokens stored")
            return resp
        last_resp = resp
        msg = (resp or {}).get("message", "").lower()
        if "totp" in msg and attempt < max_totp_attempts:
            log.warning(
                "TOTP rejected (likely rolled over) — sleeping 2s and regenerating"
            )
            time.sleep(2)
            continue
        break

    raise AuthError(
        f"generateSession failed for {client_id}: "
        f"{(last_resp or {}).get('message', last_resp)}"
    )


# ════════════════════════════════════════════════════════════════════════════
#  PUBLIC FACTORY
# ════════════════════════════════════════════════════════════════════════════
class AuthError(RuntimeError):
    """Raised when SmartAPI authentication cannot be established."""


def login(
    api_key: Optional[str] = None,
    client_id: Optional[str] = None,
    password: Optional[str] = None,
    totp_secret: Optional[str] = None,
) -> SmartConnect:
    """Authenticate with Angel One SmartAPI and return the SmartConnect handle.

    Defaults read from the central :mod:`config` so engines just call
    ``login()`` with no arguments.  Passing explicit kwargs is supported
    for unit tests and for engines that need to swap credentials.

    Returns
    -------
    SmartConnect
        Fully authenticated client ready for ``getCandleData``,
        ``placeOrder``, etc.
    """
    api_key = api_key or config.API_KEY
    client_id = client_id or config.CLIENT_ID
    password = password or config.PASSWORD
    totp_secret = totp_secret or config.TOTP_SECRET

    api = SmartConnect(api_key=api_key)
    _try_totp_with_retry(api, client_id, password, totp_secret)
    return api


def terminate(api: SmartConnect, client_id: Optional[str] = None) -> None:
    """Best-effort session shutdown — never raises."""
    cid = client_id or config.CLIENT_ID
    try:
        api.terminateSession(cid)
        log.info("SmartAPI session terminated cleanly")
    except Exception as exc:
        log.warning("terminateSession failed (non-fatal): %s", exc)
