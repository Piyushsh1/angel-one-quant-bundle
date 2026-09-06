"""angel-one-quant-bundle  ▸  ingestion.py
==============================================================================
Async tick feed — bridges Angel's callback-based `SmartWebSocketV2` into a
modern `asyncio.Queue` consumer pattern.

WHY THE BRIDGE
──────────────
`SmartWebSocketV2` uses `websocket-client` under the hood, which is a blocking
thread.  Trying to run it inside an asyncio event loop directly will pin the
loop or deadlock on reconnects.  The accepted pattern is:

    [Angel WS thread]  →  callback  →  thread-safe queue.put_nowait
                                                ↓
    [asyncio loop]   ←  await queue.get()  ←  TickFeed.stream()

That's exactly what this module implements.

USAGE
─────
    from src.ingestion import login_and_feed_token, TickFeed

    api, feed_token = login_and_feed_token()

    feed = TickFeed(
        api_key=config.ANGEL_API_KEY,
        client_id=config.ANGEL_CLIENT_ID,
        auth_token=api.access_token,
        feed_token=feed_token,
        tokens=[
            {"exchangeType": 1, "tokens": ["26000", "26009"]},  # NIFTY, BANKNIFTY
        ],
    )
    feed.start()
    try:
        async for tick in feed.stream():
            handle(tick)
    finally:
        feed.stop()

EXCHANGE TYPE CODES (Angel SmartAPI)
────────────────────────────────────
    1 = NSE_CM    (NSE Cash)         3 = BSE_CM    (BSE Cash)
    2 = NSE_FO    (NSE F&O)          4 = BSE_FO    (BSE F&O)
    5 = MCX_FO    (commodity)        7 = NCX_FO    13 = CDE_FO

WS MODES (config.WS_MODE)
─────────────────────────
    1 = LTP only          (lightest, fastest)
    2 = Quote             (bid/ask/last)
    3 = Snap Quote        (depth + OHLC, heaviest)
==============================================================================
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pyotp
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from . import config

log = logging.getLogger("ingestion")


# ════════════════════════════════════════════════════════════════════════════
#  LOGIN  (REST handshake → JWT + feed token)
# ════════════════════════════════════════════════════════════════════════════
def login_and_feed_token() -> tuple[SmartConnect, str]:
    """One-shot login.  Returns (`SmartConnect` instance, feed_token).

    Re-use the returned `SmartConnect` for ALL REST calls in the process —
    do not call this twice unless you intend to invalidate the prior session.
    """
    api = SmartConnect(api_key=config.ANGEL_API_KEY)
    totp = pyotp.TOTP(config.ANGEL_TOTP_SECRET).now()

    log.info("Authenticating client_id=%s …", config.ANGEL_CLIENT_ID)
    resp = api.generateSession(
        config.ANGEL_CLIENT_ID,
        config.ANGEL_PASSWORD,
        totp,
    )
    if not resp or not resp.get("status"):
        raise RuntimeError(f"generateSession failed: {resp}")

    # Stash refresh_token so background loops can rotate the JWT (see B4 fix below).
    data = resp.get("data") or {}
    new_refresh = data.get("refreshToken")
    if new_refresh:
        api.refresh_token = new_refresh

    feed_token = api.getfeedToken()
    if not feed_token:
        raise RuntimeError("getfeedToken returned empty")

    log.info("Auth OK — feed_token acquired")
    return api, feed_token


# ════════════════════════════════════════════════════════════════════════════
#  TOKEN REFRESH  (Fix B4 — 26 May 2026)
# ════════════════════════════════════════════════════════════════════════════
# Angel One JWTs expire ~24h after issuance.  A bot that survives an overnight
# break will silently lose its WebSocket the moment the token rolls.  These
# helpers let you keep the session alive indefinitely.
#
# Pattern:
#     asyncio.create_task(refresh_session_loop(api, on_refresh=feed.rotate_tokens))
# ════════════════════════════════════════════════════════════════════════════
def refresh_session(api: SmartConnect) -> str:
    """Refresh the JWT (access_token) using the stored refresh_token.

    Returns the freshly-issued ``feed_token`` (also valid for WS reconnects).

    Raises ``RuntimeError`` if the broker refuses the refresh — caller should
    treat this as a hard halt and force a full re-login via
    :func:`login_and_feed_token`.
    """
    refresh_tok = getattr(api, "refresh_token", "") or ""
    if not refresh_tok:
        raise RuntimeError(
            "refresh_session: api.refresh_token is empty — "
            "did you call login_and_feed_token() first?"
        )

    log.info("Refreshing SmartAPI session tokens …")
    resp = api.generateToken(refresh_tok)
    if not isinstance(resp, dict) or not resp.get("status"):
        raise RuntimeError(f"generateToken failed: {resp}")

    data = resp.get("data") or {}
    new_jwt = (data.get("jwtToken") or "").strip()
    if new_jwt:
        api.access_token = new_jwt

    new_feed = api.getfeedToken()
    if not new_feed:
        raise RuntimeError("getfeedToken returned empty after refresh")

    log.info("Session refreshed — JWT + feed_token rotated")
    return new_feed


async def refresh_session_loop(
    api: SmartConnect,
    *,
    interval_hours: float = 12.0,
    on_refresh: Any = None,
) -> None:
    """Background coroutine — rotate the JWT every ``interval_hours``.

    Spawn alongside your tick consumer::

        asyncio.create_task(refresh_session_loop(api, on_refresh=feed.rotate_tokens))

    ``on_refresh`` (optional) is invoked with the new ``feed_token`` after
    each successful rotation.  Use it to push the new token into
    :class:`TickFeed` so the next reconnect picks it up.

    The loop is hardened against transient failures: any exception is
    logged and the loop sleeps the full interval before retrying.  Three
    consecutive failures escalates to CRITICAL — at that point your VPS
    probably can't reach the broker at all.
    """
    consecutive_failures = 0
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            new_feed = refresh_session(api)
            consecutive_failures = 0
            if callable(on_refresh):
                try:
                    on_refresh(new_feed, api.access_token)
                except Exception as exc:                                  # noqa: BLE001
                    log.exception("on_refresh callback raised: %s", exc)
        except Exception as exc:                                          # noqa: BLE001
            consecutive_failures += 1
            level = logging.CRITICAL if consecutive_failures >= 3 else logging.ERROR
            log.log(
                level,
                "Session refresh attempt #%d failed: %s",
                consecutive_failures, exc,
            )


# ════════════════════════════════════════════════════════════════════════════
#  TICK PAYLOAD
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Tick:
    """Normalised single-tick payload.

    SmartWebSocketV2 returns Mode-1/2/3 dicts with different fields.  We
    flatten the most common ones and stash the original under `raw` so a
    strategy that needs depth can still get to it.
    """
    token: str
    exchange_type: int
    ltp: float
    timestamp_ms: int
    volume: int = 0
    last_traded_qty: int = 0
    raw: dict = field(default_factory=dict, repr=False)


def _parse_tick(msg: dict) -> Tick | None:
    """Convert one raw WS payload into a `Tick`.

    Angel reports LTP in paise (i.e. price × 100).  We restore rupees here
    so every downstream price comparison is in the same unit.
    """
    try:
        token = str(msg.get("token") or msg.get("Tokens") or "")
        if not token:
            return None
        exch = int(msg.get("exchange_type") or msg.get("ExchangeType") or 0)
        raw_ltp = msg.get("last_traded_price") or msg.get("ltp") or 0
        ltp = float(raw_ltp) / 100.0
        ts_ms = int(msg.get("exchange_timestamp") or int(time.time() * 1000))
        vol = int(msg.get("volume_trade_for_the_day") or msg.get("volume") or 0)
        ltq = int(msg.get("last_traded_quantity") or 0)
        return Tick(
            token=token,
            exchange_type=exch,
            ltp=ltp,
            timestamp_ms=ts_ms,
            volume=vol,
            last_traded_qty=ltq,
            raw=msg,
        )
    except (ValueError, TypeError) as exc:
        log.warning("tick parse failed: %s | raw=%s", exc, msg)
        return None


# ════════════════════════════════════════════════════════════════════════════
#  TICK FEED  (the bridge)
# ════════════════════════════════════════════════════════════════════════════
class TickFeed:
    """Thread-safe async wrapper around `SmartWebSocketV2`.

    Lifecycle
    ─────────
        feed = TickFeed(...)
        feed.start()                       # spawns the WS thread
        async for tick in feed.stream():   # consume from asyncio
            ...
        feed.stop()                        # graceful close

    Reconnection
    ────────────
    On WS close or error, the worker thread sleeps for
    `config.WS_RECONNECT_BACKOFF` and re-attempts up to
    `config.WS_MAX_RECONNECTS` times before giving up.  Each successful
    reconnect re-subscribes the original token list.

    Backpressure
    ────────────
    The internal queue is bounded (default 10_000).  If the consumer can't
    keep up, the OLDEST tick is dropped (logged as WARNING).  A bot that
    routinely drops ticks is structurally broken — fix the consumer, not
    the feed.
    """

    def __init__(
        self,
        *,
        api_key: str,
        client_id: str,
        auth_token: str,
        feed_token: str,
        tokens: list[dict],
        mode: int = config.WS_MODE,
        max_queue_size: int = 10_000,
    ) -> None:
        self._api_key      = api_key
        self._client_id    = client_id
        self._auth_token   = auth_token
        self._feed_token   = feed_token
        self._tokens       = tokens
        self._mode         = mode
        self._correlation  = f"feed-{uuid.uuid4().hex[:8]}"

        self._queue: queue.Queue[Tick] = queue.Queue(maxsize=max_queue_size)
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws: SmartWebSocketV2 | None = None
        self._reconnects = 0

    # ── public API ──────────────────────────────────────────────────────
    def start(self) -> None:
        """Spawn the WS worker thread.  Idempotent."""
        if self._thread and self._thread.is_alive():
            log.warning("TickFeed already running")
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="TickFeed-WS", daemon=True
        )
        self._thread.start()
        log.info("TickFeed started | mode=%d | tokens=%s", self._mode, self._tokens)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal shutdown and join the worker."""
        self._stop_evt.set()
        # Fix B5: SmartWebSocketV2 SDK exposes either close_connection()
        # (>=1.5.x) or close() (<=1.4.x).  Try both before giving up.
        if self._ws is not None:
            close_fn = (
                getattr(self._ws, "close_connection", None)
                or getattr(self._ws, "close", None)
            )
            if callable(close_fn):
                try:
                    close_fn()
                except Exception as exc:                                  # noqa: BLE001
                    log.debug("WS close raised: %s", exc)
            else:
                log.warning("WS instance has no close method — relying on stop_evt")
        if self._thread:
            self._thread.join(timeout=timeout)
        log.info("TickFeed stopped")

    def rotate_tokens(self, new_feed_token: str, new_auth_token: str) -> None:
        """Push refreshed credentials into the feed.

        Designed to be wired as the ``on_refresh`` callback of
        :func:`refresh_session_loop`.  The new credentials are used on the
        NEXT reconnect (we don't kill the live socket — Angel honours both
        old and new tokens during a short overlap window, so a graceful
        rebuild on next natural disconnect is enough).
        """
        self._auth_token = new_auth_token
        self._feed_token = new_feed_token
        log.info("TickFeed credentials rotated (will apply on next reconnect)")

    async def stream(self) -> AsyncIterator[Tick]:
        """Yield ticks until `stop()` is called.

        We poll the thread-safe queue from the asyncio loop using a tiny
        timeout so the consumer can respond promptly to `stop()`.
        """
        loop = asyncio.get_event_loop()
        while not self._stop_evt.is_set() or not self._queue.empty():
            try:
                tick = await loop.run_in_executor(
                    None, self._queue.get, True, 0.5
                )
            except queue.Empty:
                continue
            yield tick

    # ── worker thread ───────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._connect_once()
            except Exception as exc:
                log.exception("WS loop crashed: %s", exc)

            if self._stop_evt.is_set():
                break

            self._reconnects += 1
            if self._reconnects > config.WS_MAX_RECONNECTS:
                log.critical(
                    "TickFeed exhausted %d reconnects — giving up",
                    config.WS_MAX_RECONNECTS,
                )
                return

            backoff = config.WS_RECONNECT_BACKOFF * min(self._reconnects, 5)
            log.warning(
                "WS will reconnect in %.1fs (attempt %d/%d)",
                backoff, self._reconnects, config.WS_MAX_RECONNECTS,
            )
            self._stop_evt.wait(backoff)

    def _connect_once(self) -> None:
        """One connect→subscribe→serve cycle.  Returns on disconnect.

        Fix B3 (26 May 2026): the reconnect counter was previously only
        reset inside ``on_open``.  If the WS opened TCP but failed to
        fire ``on_open`` (rare but possible — auth rejection, immediate
        server-side close, dropped subscribe ack), every reconnect
        attempt would still tick the counter and we'd hit
        ``WS_MAX_RECONNECTS`` even though connections were ostensibly
        succeeding.  We now track ``on_open_fired`` explicitly and treat
        "connect returned without on_open" as a hard failure that
        re-raises and lets ``_run()`` log + back off.
        """
        sws = SmartWebSocketV2(
            self._auth_token,
            self._api_key,
            self._client_id,
            self._feed_token,
        )
        self._ws = sws
        on_open_fired = threading.Event()

        def on_open(_wsapp: Any) -> None:
            log.info(
                "WS open — subscribing corr_id=%s mode=%d tokens=%s",
                self._correlation, self._mode, self._tokens,
            )
            sws.subscribe(self._correlation, self._mode, self._tokens)
            on_open_fired.set()
            self._reconnects = 0

        def on_data(_wsapp: Any, msg: Any) -> None:
            if not isinstance(msg, dict):
                return
            tick = _parse_tick(msg)
            if tick is None:
                return
            try:
                self._queue.put_nowait(tick)
            except queue.Full:
                # Drop oldest to keep latency bounded.
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(tick)
                    log.warning("TickFeed queue full — dropped oldest tick")
                except queue.Empty:
                    pass

        def on_error(_wsapp: Any, err: Any) -> None:
            log.error("WS error: %s", err)

        def on_close(_wsapp: Any, *args: Any) -> None:
            log.info("WS closed: %s", args)

        sws.on_open  = on_open
        sws.on_data  = on_data
        sws.on_error = on_error
        sws.on_close = on_close

        sws.connect()  # blocks until close

        # B3 guard: if we returned from connect() without on_open ever
        # firing, this was NOT a healthy session and the reconnect counter
        # must NOT be considered reset.  Raising here re-enters _run()'s
        # except path which logs + ticks the counter + backs off.
        if not on_open_fired.is_set() and not self._stop_evt.is_set():
            raise RuntimeError(
                "WS closed before on_open fired — likely auth, quota, "
                "or stale token (try refresh_session()?)"
            )


# ════════════════════════════════════════════════════════════════════════════
#  CLI smoke test
# ════════════════════════════════════════════════════════════════════════════
async def _smoke() -> None:
    """Print 30 NIFTY ticks then quit.  Run via `python -m src.ingestion`."""
    config.setup_logging()
    api, ft = login_and_feed_token()
    feed = TickFeed(
        api_key=config.ANGEL_API_KEY,
        client_id=config.ANGEL_CLIENT_ID,
        auth_token=api.access_token,
        feed_token=ft,
        tokens=[{"exchangeType": 1, "tokens": ["26000"]}],
    )
    feed.start()
    seen = 0
    try:
        async for tick in feed.stream():
            log.info("TICK | token=%s ltp=%.2f vol=%d ts=%d",
                     tick.token, tick.ltp, tick.volume, tick.timestamp_ms)
            seen += 1
            if seen >= 30:
                break
    finally:
        feed.stop()
        api.terminateSession(config.ANGEL_CLIENT_ID)


if __name__ == "__main__":
    asyncio.run(_smoke())
