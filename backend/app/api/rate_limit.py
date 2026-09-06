"""
================================================================================
api/rate_limit.py  ▸  Per-IP sliding-window rate limiter
================================================================================
A small dependency-based limiter for the credential endpoints. Implemented as a
FastAPI dependency (not a decorator) so it never interferes with endpoint
signature inspection — the problem that decorator-based limiters cause when
combined with Pydantic body parameters.

The store is in-process, which is correct for a single API instance. For a
horizontally-scaled deployment, swap `_HITS` for a shared Redis counter so the
limit holds across instances; the dependency interface stays the same.

Concurrency note: the event loop is single-threaded, and the check-and-append
below runs without an await in between, so it is atomic with respect to other
requests. No lock is required.
================================================================================
NOTE: no `from __future__ import annotations` here. This class is a FastAPI
dependency; a stringised `request: Request` annotation on __call__ fails
FastAPI's resolution and gets misread as a query parameter, producing a
spurious 422 on any route that depends on it.
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


def _parse_rule(rule: str) -> tuple[int, int]:
    """Parse '10/minute' → (10, 60). Supports second|minute|hour."""
    count_str, _, period = rule.partition("/")
    count = int(count_str)
    seconds = {"second": 1, "minute": 60, "hour": 3600}.get(period.strip(), 60)
    return count, seconds


class RateLimit:
    """FastAPI dependency enforcing `max` requests per `window` per client IP."""

    # bucket key -> timestamps of recent hits
    _HITS: dict[str, deque[float]] = defaultdict(deque)

    def __init__(self, rule: str):
        self.max, self.window = _parse_rule(rule)
        # Distinct bucket per rule so /login and /signup limits don't share.
        self.rule = rule

    def _client_key(self, request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        ip = (
            fwd.split(",")[0].strip()
            if fwd
            else (request.client.host if request.client else "unknown")
        )
        return f"{self.rule}:{ip}"

    async def __call__(self, request: Request) -> None:
        key = self._client_key(request)
        now = time.monotonic()
        hits = self._HITS[key]

        # Drop timestamps outside the window.
        cutoff = now - self.window
        while hits and hits[0] < cutoff:
            hits.popleft()

        if len(hits) >= self.max:
            retry_after = int(self.window - (now - hits[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please wait and try again.",
                headers={"Retry-After": str(max(retry_after, 1))},
            )

        hits.append(now)
