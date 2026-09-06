"""algo-barbell  ▸  retry.py
================================================================================
Shared retry decorator for every Angel One API call across the three engines.

Lessons baked in (each from a real-world incident):

    1.  ``placeOrder()`` returns a bare ``None`` on rate-limit / non-JSON
        responses.  We detect this and convert to a transient-tagged
        exception so the retry path engages.

    2.  Some endpoints return ``{"status": False, "message": "..."}`` instead
        of raising.  We detect transient strings ("exceeding access rate",
        "could not parse the json", etc.) and retry those too.

    3.  ``DataException``, ``ConnectionError``, ``TimeoutError``, and 5xx
        gateway errors are all retried.

    4.  Hard-rejection messages ("insufficient funds", "invalid symbol",
        "cautionary listings") are NOT retried — they bubble up as the
        actual broker reason for the operator.

USAGE
─────
    from retry import retry_api

    @retry_api()
    def fetch_candles(api, params): ...

    @retry_api(max_attempts=5, base_delay=2.0)
    def place_order(api, params): ...
================================================================================
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

_T = TypeVar("_T")


# ════════════════════════════════════════════════════════════════════════════
#  TRANSIENT-ERROR DETECTION
# ════════════════════════════════════════════════════════════════════════════
_TRANSIENT_TOKENS = (
    "bad gateway",
    "gateway timeout",
    "rate limit",
    "too many requests",
    "exceeding access rate",
    "couldn't parse the json",
    "could not parse",
    "timed out",
    "timeout",
    "ab1004",
    "ab1021",
    "connection reset",
    "connection refused",
    "503",
    "502",
    "504",
)

#: Strings that should NEVER be retried — operator must see them as-is.
_HARD_REJECT_TOKENS = (
    "insufficient funds",
    "insufficient margin",
    "you require rs",         # Angel "You require Rs. X funds to execute…" form
    "you require ₹",
    "margin shortfall",
    "invalid symbol",
    "invalid token",
    "invalid security",
    "cautionary listings",
    "rms:",
    "frozen",
    "circuit",
)

#: Subset of hard-reject tokens that specifically indicate a margin / funds
#: structural failure on a SELL of an FNO option.  Callers can use this to
#: trigger the "[MARGIN GUARDRAIL]" abort path documented in the order
#: placement layer (see broker.place_market).
_MARGIN_REJECT_TOKENS = (
    "insufficient funds",
    "insufficient margin",
    "you require rs",
    "you require ₹",
    "margin shortfall",
)


def is_margin_reject(text: Any) -> bool:
    """Return True if ``text`` (str / exception / dict) reads like an Angel
    pre-trade margin / insufficient-funds rejection on a SELL leg.

    Detection is conservative: matches the Angel verbatim phrasings the
    bundle has observed in production.  Adding new phrasings should be
    done here so the broker layer, the retry decorator, and the engine
    monitor loop all agree on what counts as a margin failure.
    """
    s = str(text).lower()
    return any(tok in s for tok in _MARGIN_REJECT_TOKENS)


def _is_transient(exc_or_resp: Any) -> bool:
    """True if the message contains a known retryable token."""
    s = str(exc_or_resp).lower()
    if any(tok in s for tok in _HARD_REJECT_TOKENS):
        return False
    return any(tok in s for tok in _TRANSIENT_TOKENS)


# ════════════════════════════════════════════════════════════════════════════
#  RETRY DECORATOR
# ════════════════════════════════════════════════════════════════════════════
def retry_api(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    raise_on_none: bool = True,
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Retry a function on transient API failures with exponential back-off.

    Parameters
    ----------
    max_attempts
        Total number of attempts before giving up (1 = no retry).
    base_delay
        Initial back-off in seconds.  Doubled each attempt.
    raise_on_none
        If True (default), a ``None`` return value is treated as a transient
        rate-limit silent failure and retried.  Set to False ONLY for
        functions that legitimately return None on success.
    """

    def decorator(fn: Callable[..., _T]) -> Callable[..., _T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> _T:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    resp = fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_attempts and _is_transient(exc):
                        wait = base_delay * (2 ** (attempt - 1))
                        log.warning(
                            "[RETRY] %s exception '%s' — retry %d/%d in %.1fs",
                            fn.__name__,
                            exc,
                            attempt,
                            max_attempts - 1,
                            wait,
                        )
                        time.sleep(wait)
                        continue
                    raise
                else:
                    # Soft-failure dict: {"status": False, "message": "..."}
                    if (
                        isinstance(resp, dict)
                        and resp.get("status") is False
                        and _is_transient(resp.get("message", ""))
                        and attempt < max_attempts
                    ):
                        wait = base_delay * (2 ** (attempt - 1))
                        log.warning(
                            "[RETRY] %s soft-failure '%s' — retry %d/%d in %.1fs",
                            fn.__name__,
                            resp.get("message", ""),
                            attempt,
                            max_attempts - 1,
                            wait,
                        )
                        time.sleep(wait)
                        continue

                    # Bare None: treat as transient rate-limit silent failure.
                    # SmartAPI's placeOrder() does this on non-JSON responses.
                    if resp is None and raise_on_none and attempt < max_attempts:
                        wait = base_delay * (2 ** (attempt - 1))
                        log.warning(
                            "[RETRY] %s returned None (likely rate-limit) — "
                            "retry %d/%d in %.1fs",
                            fn.__name__,
                            attempt,
                            max_attempts - 1,
                            wait,
                        )
                        time.sleep(wait)
                        continue

                    return resp

            # Loop exited without success
            if last_exc is not None:
                raise last_exc
            log.critical(
                "[RETRY] %s exhausted %d attempts with no transient signal",
                fn.__name__,
                max_attempts,
            )
            return None  # type: ignore[return-value]

        return wrapper

    return decorator
