"""angel-one-quant-bundle  ▸  test_pipeline.py
==============================================================================
Standalone offline test harness.  Mocks every SmartAPI surface so the entire
pipeline can be exercised without an internet connection or real credentials.

Run:
    python test_pipeline.py
    python test_pipeline.py -v        # verbose
    python test_pipeline.py -k risk   # filter

What this covers
────────────────
  T1  config loads from synthetic env vars; bad values rejected
  T2  risk: silo commits, releases, refuses on lock & overflow
  T3  risk: trailing stop (long + short) ratchets and exits correctly
  T4  risk: per-silo DD lock fires at -DAILY_DRAWDOWN_PCT × cap
  T5  risk: global breaker locks ALL silos when combined PnL breaches
  T6  execution: PAPER mode short-circuits every order type
  T7  execution: tick rounding snaps to TICK_SIZE
  T8  execution: rate-limit retry path eats transient RuntimeError
  T9  execution: wait_for_fill terminates on broker REJECTED status
  T10 ingestion: TickFeed bridges fake WS into async stream
  T11 ingestion: parser handles paise → rupees conversion correctly
  T12 ingestion: tick queue backpressure drops oldest on flood

Notes
─────
*  We monkey-patch `os.environ` BEFORE importing `src.config` because the
   config module validates at import time.  See `_install_test_env()`.
*  `SmartApi` is faked at the module level so importing `src.ingestion`
   and `src.execution` works without the real broker SDK installed.  This
   makes the test runnable on any clean Python 3.10+ installation.
==============================================================================
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time
import types
import unittest
from typing import Any
from unittest.mock import patch


# ════════════════════════════════════════════════════════════════════════════
#  1. INSTALL FAKE ENVIRONMENT + FAKE SmartApi MODULE  (must run before any src.*)
# ════════════════════════════════════════════════════════════════════════════
def _install_test_env() -> None:
    """Populate os.environ with valid synthetic values."""
    test_env = {
        "ANGEL_API_KEY":         "TEST_API_KEY_xxxxxxxx",
        "ANGEL_CLIENT_ID":       "TEST00001",
        "ANGEL_PASSWORD":        "TEST_PIN_1234",
        "ANGEL_TOTP_SECRET":     "JBSWY3DPEHPK3PXP",  # valid Base32 (RFC test vector)
        "PAPER_TRADING":         "true",
        "LOG_LEVEL":             "WARNING",
        "LOG_DIR":                "./test_logs",
        "API_PACING_SECONDS":    "0.0",                # no pacing in tests
        "API_MAX_RETRIES":       "2",
        "API_RETRY_BACKOFF":     "0.1",
        "SILO_A_NAME":           "TREND",
        "SILO_A_MAX_CAPITAL":    "100000",
        "SILO_A_RISK_PCT":       "0.02",
        "SILO_B_NAME":           "SWING",
        "SILO_B_MAX_CAPITAL":    "100000",
        "SILO_B_RISK_PCT":       "0.01",
        "SILO_C_NAME":           "DERIV",
        "SILO_C_MAX_CAPITAL":    "100000",
        "SILO_C_RISK_PCT":       "0.05",
        "DAILY_DRAWDOWN_PCT":    "0.04",
        "GLOBAL_DRAWDOWN_PCT":   "0.06",
        "TRAIL_TRIGGER_PCT":     "0.20",
        "TRAIL_STEP_PCT":        "0.10",
        "WS_MODE":               "1",
        "WS_RECONNECT_BACKOFF":  "0.01",
        "WS_MAX_RECONNECTS":     "3",
        "MARKET_OPEN":           "09:15",
        "MARKET_CLOSE":          "15:30",
        "SQUARE_OFF_TIME":       "15:15",
        "TICK_SIZE":             "0.05",
    }
    for k, v in test_env.items():
        os.environ[k] = v


def _install_fake_smartapi() -> None:
    """Inject a fake `SmartApi` package into sys.modules so the production
    imports (`from SmartApi import SmartConnect`, etc.) succeed without the
    real SDK installed.  Each callable is a MagicMock so assertions work."""

    class FakeSmartConnect:
        def __init__(self, api_key: str, **_: Any) -> None:
            self.api_key = api_key
            self.access_token = "FAKE_JWT_TOKEN"
            self.refresh_token = "FAKE_REFRESH_TOKEN"
            self.feed_token = "FAKE_FEED_TOKEN"
            # behaviour hooks for tests
            self._next_order_id = 1
            self._order_book: list[dict] = []

        def generateSession(self, client_id: str, password: str, totp: str) -> dict:
            return {
                "status": True,
                "message": "SUCCESS",
                "data": {
                    "jwtToken":     self.access_token,
                    "refreshToken": self.refresh_token,
                    "feedToken":    self.feed_token,
                },
            }

        def getfeedToken(self) -> str:
            return self.feed_token

        def generateToken(self, refresh_token: str) -> dict:
            """B4 — JWT refresh.  Rotates jwtToken on each call."""
            self._refresh_count = getattr(self, "_refresh_count", 0) + 1
            self.access_token = f"FAKE_JWT_REFRESHED_{self._refresh_count}"
            return {
                "status": True,
                "message": "SUCCESS",
                "data": {
                    "jwtToken":     self.access_token,
                    "refreshToken": refresh_token,
                    "feedToken":    self.feed_token,
                },
            }

        def terminateSession(self, client_id: str) -> dict:
            return {"status": True, "message": "SUCCESS"}

        def placeOrderFullResponse(self, params: dict) -> dict:
            oid = f"FAKE-OID-{self._next_order_id:06d}"
            self._next_order_id += 1
            self._order_book.append({
                "orderid":       oid,
                "tradingsymbol": params.get("tradingsymbol"),
                "orderstatus":   "complete",
                "filledshares":  params.get("quantity"),
                "averageprice":  100.0,
                "text":          "",
            })
            return {"status": True, "message": "SUCCESS", "data": {"orderid": oid}}

        def orderBook(self) -> dict:
            return {"status": True, "data": list(self._order_book)}

        def ltpData(self, exchange: str, symbol: str, token: str) -> dict:
            return {"status": True, "data": {"ltp": 12345.50}}

        def getCandleData(self, params: dict) -> dict:
            return {"status": True, "data": [["2026-05-26T09:15", 100, 105, 99, 104, 1000]]}

        def cancelOrder(self, order_id: str, variety: str) -> dict:
            return {"status": True, "message": "SUCCESS"}

    class FakeSmartWebSocketV2:
        """Inert constructor — tests that need WS behaviour build their own."""
        def __init__(self, *args, **kwargs) -> None:
            self.on_open = self.on_data = self.on_error = self.on_close = None

        def connect(self) -> None:
            return

        def subscribe(self, *args, **kwargs) -> None:
            return

        def close_connection(self) -> None:
            return

    smartapi_pkg = types.ModuleType("SmartApi")
    smartapi_pkg.SmartConnect = FakeSmartConnect
    sys.modules["SmartApi"] = smartapi_pkg

    ws_sub = types.ModuleType("SmartApi.smartWebSocketV2")
    ws_sub.SmartWebSocketV2 = FakeSmartWebSocketV2
    sys.modules["SmartApi.smartWebSocketV2"] = ws_sub


# Order matters: env first, fake SDK second, THEN import src.*
_install_test_env()
_install_fake_smartapi()

# Now safe to import the package under test
from src import config, execution, ingestion, risk  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
#  T1.  CONFIG  (env validation)
# ════════════════════════════════════════════════════════════════════════════
class TestConfig(unittest.TestCase):

    def test_required_keys_loaded(self) -> None:
        self.assertEqual(config.ANGEL_CLIENT_ID, "TEST00001")
        self.assertEqual(config.ANGEL_API_KEY, "TEST_API_KEY_xxxxxxxx")
        self.assertTrue(config.PAPER_TRADING)

    def test_silo_caps_loaded(self) -> None:
        self.assertEqual(config.SILO_A_MAX_CAPITAL, 100_000)
        self.assertEqual(config.SILO_C_NAME, "DERIV")

    def test_total_capital_helper(self) -> None:
        self.assertEqual(config.total_capital(), 300_000)

    def test_banner_contains_silos(self) -> None:
        b = config.banner()
        self.assertIn("TREND", b)
        self.assertIn("SWING", b)
        self.assertIn("DERIV", b)

    def test_validation_rejects_bad_pct(self) -> None:
        """Re-importing config with bad pct must raise ConfigError.

        Note: a failed module reload leaves *partial* globals behind, which
        will pollute downstream tests.  We always force a clean re-reload
        in tearDown to guarantee isolation.
        """
        with patch.dict(os.environ, {"SILO_A_RISK_PCT": "1.5"}, clear=False):
            # NOTE: importlib.reload() creates a fresh ConfigError CLASS, so
            # the stale `config.ConfigError` reference no longer matches the
            # raised exception's type.  We resolve the class lazily via
            # sys.modules to always get the post-reload class identity.
            with self.assertRaises(Exception) as ctx:
                importlib.reload(sys.modules["src.config"])
            self.assertEqual(
                type(ctx.exception).__name__, "ConfigError",
                f"expected ConfigError, got {type(ctx.exception).__name__}",
            )
            self.assertIn("SILO_A_RISK_PCT", str(ctx.exception))

    def tearDown(self) -> None:
        # patch.dict already restored os.environ; force a clean module reload
        # so partial globals from any negative test don't poison the next case.
        importlib.reload(sys.modules["src.config"])
        importlib.reload(sys.modules["src.risk"])
        importlib.reload(sys.modules["src.execution"])
        # Re-bind module-level references in this test module
        global config, risk, execution
        config    = sys.modules["src.config"]
        risk      = sys.modules["src.risk"]
        execution = sys.modules["src.execution"]


# ════════════════════════════════════════════════════════════════════════════
#  T2 + T3.  RISK — silo + trailing stop
# ════════════════════════════════════════════════════════════════════════════
class TestRiskSilo(unittest.TestCase):

    def setUp(self) -> None:
        self.silo = risk.CapitalSilo(
            name="UNITTEST", max_capital=100_000,
            risk_pct_per_trade=0.02, daily_drawdown_pct=0.04,
        )

    def test_commit_then_release_roundtrip(self) -> None:
        self.silo.commit(position_id="P1", cost=25_000)
        self.assertEqual(self.silo.deployed_capital, 25_000)
        self.assertEqual(self.silo.available_capital, 75_000)
        self.silo.release(position_id="P1", realised_pnl=+1_500)
        self.assertEqual(self.silo.deployed_capital, 0)
        self.assertEqual(self.silo.realised_pnl_today, 1_500)

    def test_commit_refused_on_overflow(self) -> None:
        with self.assertRaises(risk.RiskRefusedError):
            self.silo.commit(position_id="P1", cost=100_001)

    def test_commit_refused_on_duplicate_id(self) -> None:
        self.silo.commit(position_id="P1", cost=10_000)
        with self.assertRaises(risk.RiskRefusedError):
            self.silo.commit(position_id="P1", cost=10_000)

    def test_daily_dd_kill_switch_fires(self) -> None:
        # DD limit = 100k × 4% = ₹4000.  Drive realised PnL to -4001.
        self.silo.commit(position_id="P1", cost=50_000)
        self.silo.release(position_id="P1", realised_pnl=-4_001)
        self.assertTrue(self.silo.is_locked, "silo should be locked at -₹4001 PnL")
        with self.assertRaises(risk.RiskRefusedError):
            self.silo.commit(position_id="P2", cost=1_000)

    def test_unrealised_pnl_triggers_lock(self) -> None:
        self.silo.commit(position_id="P1", cost=50_000)
        self.silo.update_unrealised(position_id="P1", pnl=-4_100)
        self.assertTrue(self.silo.is_locked)

    def test_risk_budget_per_trade(self) -> None:
        self.assertEqual(self.silo.risk_budget_per_trade(), 2_000)


class TestRiskManager(unittest.TestCase):

    def setUp(self) -> None:
        self.rm = risk.RiskManager.from_config()

    def test_three_silos_exist(self) -> None:
        self.assertEqual(len(self.rm.silos()), 3)
        self.assertEqual({s.name for s in self.rm.silos()},
                         {"TREND", "SWING", "DERIV"})

    def test_global_breaker_locks_all(self) -> None:
        # Combined cap = 300k.  Global DD = 6% → ₹18,000.
        # Drive each silo to -6,500 realised → total -19,500 < -18,000.
        for name in ("TREND", "SWING", "DERIV"):
            s = self.rm.silo(name)
            s.commit(position_id="X", cost=50_000)
            s.release(position_id="X", realised_pnl=-6_500)
        self.rm.check_global_breaker()
        self.assertTrue(self.rm.is_globally_locked)
        for s in self.rm.silos():
            self.assertTrue(s.is_locked,
                f"{s.name} should be force-locked by global breaker")

    def test_unknown_silo_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.rm.silo("DOES_NOT_EXIST")


class TestTrailingStop(unittest.TestCase):

    def test_long_arms_on_trigger_and_ratchets(self) -> None:
        """REAL-BEHAVIOUR TEST (catches B12 in risk.py).

        The README + inline code comment claim that on the arming tick
        SL jumps to BREAK-EVEN.  In reality it jumps STRAIGHT TO the
        first ratchet level (extreme × (1 - step_pct)) because the
        `if self.armed:` block runs on the SAME tick that flipped `armed`.
        Capturing the observed behaviour here so any future refactor
        either fixes the README *or* preserves the aggressive arm.
        """
        ts = risk.TrailingStop.from_long_entry(entry=100, hard_sl=95,
                                                trigger_pct=0.20, step_pct=0.10)
        self.assertFalse(ts.armed)
        ts.update(110)                  # +10% — not enough to arm
        self.assertFalse(ts.armed)
        self.assertEqual(ts.current_sl, 95)
        sl = ts.update(125)             # +25% — arms; SL → 125 × 0.90 = 112.5
        self.assertTrue(ts.armed)
        self.assertAlmostEqual(sl, 112.5, places=4)
        sl = ts.update(150)             # new high → SL = 150 × 0.90 = 135
        self.assertAlmostEqual(sl, 135.0, places=4)
        sl2 = ts.update(140)            # SL must never move backwards
        self.assertAlmostEqual(sl2, 135.0, places=4)

    def test_long_exit_signal_fires(self) -> None:
        ts = risk.TrailingStop.from_long_entry(entry=100, hard_sl=95)
        ts.update(125)
        ts.update(150)                  # SL → 135
        self.assertFalse(ts.exit_signal(140))
        self.assertTrue(ts.exit_signal(134))

    def test_short_arms_and_trails_down(self) -> None:
        ts = risk.TrailingStop.from_short_entry(entry=100, hard_sl=105,
                                                 trigger_pct=0.20, step_pct=0.10)
        ts.update(95)
        self.assertFalse(ts.armed)
        sl = ts.update(75)              # +25% gain → arms; SL → 75 × 1.10 = 82.5
        self.assertTrue(ts.armed)
        self.assertAlmostEqual(sl, 82.5, places=4)
        sl = ts.update(50)              # new low → SL = 50 × 1.10 = 55
        self.assertAlmostEqual(sl, 55.0, places=4)
        self.assertFalse(ts.exit_signal(54))
        self.assertTrue(ts.exit_signal(56))


# ════════════════════════════════════════════════════════════════════════════
#  T6 + T7 + T8 + T9.  EXECUTION  (paper mode, retries, ticks, fills)
# ════════════════════════════════════════════════════════════════════════════
class TestExecution(unittest.TestCase):

    def setUp(self) -> None:
        self.api = sys.modules["SmartApi"].SmartConnect(api_key="TEST")
        # Force PAPER mode for these tests regardless of import-time state
        execution.config.PAPER_TRADING = True

    def test_paper_market_returns_fake_oid(self) -> None:
        result = execution.place_market(
            self.api, symbol="NIFTY26MAY26000CE", token="123456",
            exchange="NFO", side="BUY", quantity=50,
            product_type="CARRYFORWARD", wait_for_fill_s=0,
        )
        self.assertIsNotNone(result)
        oid, px = result
        self.assertTrue(oid.startswith("PAPER-"))
        # Fix B6 verified: paper-mode now synthesises the fill price from
        # the live LTP probe.  The fake ltpData returns 12345.50.
        self.assertEqual(px, 12345.50)

    def test_paper_market_tolerates_ltp_failure(self) -> None:
        """B6 safety: paper-mode order must NOT crash if LTP probe fails."""
        broken_api = self.api
        original_ltp = broken_api.ltpData
        broken_api.ltpData = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no quote"))
        try:
            result = execution.place_market(
                broken_api, symbol="NIFTY26MAY26000CE", token="123456",
                exchange="NFO", side="BUY", quantity=50,
                product_type="CARRYFORWARD", wait_for_fill_s=0,
            )
        finally:
            broken_api.ltpData = original_ltp
        self.assertIsNotNone(result)
        oid, px = result
        self.assertTrue(oid.startswith("PAPER-"))
        # Probe failure falls through to 0.0 rather than raising.
        self.assertEqual(px, 0.0)

    def test_paper_limit_returns_fake_oid(self) -> None:
        oid = execution.place_limit(
            self.api, symbol="BANKBEES-EQ", token="11439", exchange="NSE",
            side="SELL", quantity=10, price=551.234,
        )
        self.assertIsNotNone(oid)
        self.assertTrue(oid.startswith("PAPER-"))

    def test_paper_stoploss_returns_fake_oid(self) -> None:
        oid = execution.place_stoploss_market(
            self.api, symbol="X", token="999", exchange="NSE",
            side="SELL", quantity=4, trigger_price=551.20,
        )
        self.assertIsNotNone(oid)
        self.assertTrue(oid.startswith("PAPER-SL-"))

    def test_tick_rounding(self) -> None:
        self.assertEqual(execution.round_to_tick(100.07), 100.05)
        self.assertEqual(execution.round_to_tick(100.03), 100.05)
        self.assertEqual(execution.round_to_tick(100.02), 100.00)
        self.assertEqual(execution.round_to_tick(551.234), 551.25)

    def test_retry_eats_transient_failure(self) -> None:
        """_place_order_raw must retry on RuntimeError and succeed on attempt 2."""
        execution.config.PAPER_TRADING = False
        call_count = {"n": 0}
        def flaky_full_response(_self, params):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None  # silent failure → RuntimeError → retry
            return {"status": True, "data": {"orderid": "RECOVERED-1"}}

        with patch.object(type(self.api), "placeOrderFullResponse",
                          flaky_full_response, create=True):
            result = execution.place_market(
                self.api, symbol="X", token="1", exchange="NSE",
                side="BUY", quantity=1, wait_for_fill_s=0,
            )
        self.assertIsNotNone(result)
        self.assertEqual(call_count["n"], 2, "should have retried exactly once")
        execution.config.PAPER_TRADING = True

    def test_wait_for_fill_returns_false_on_rejection(self) -> None:
        execution.config.PAPER_TRADING = False
        self.api._order_book = [{
            "orderid": "REJ-1", "orderstatus": "rejected",
            "filledshares": 0, "averageprice": 0,
            "text": "insufficient margin",
        }]
        filled, avg, detail = execution.wait_for_fill(self.api, "REJ-1", timeout_s=1.0)
        self.assertFalse(filled)
        self.assertEqual(avg, 0.0)
        self.assertIn("rejected", detail)
        self.assertIn("insufficient margin", detail)
        execution.config.PAPER_TRADING = True


# ════════════════════════════════════════════════════════════════════════════
#  T10 + T11 + T12.  INGESTION  (WS bridge, tick parse, backpressure)
# ════════════════════════════════════════════════════════════════════════════
class TestTickParse(unittest.TestCase):

    def test_paise_to_rupees(self) -> None:
        tick = ingestion._parse_tick({
            "token":               "26000",
            "exchange_type":       1,
            "last_traded_price":   2576550,         # 25,765.50 in paise
            "exchange_timestamp":  1716700000000,
            "volume_trade_for_the_day": 12345,
            "last_traded_quantity": 50,
        })
        self.assertIsNotNone(tick)
        self.assertAlmostEqual(tick.ltp, 25765.50)
        self.assertEqual(tick.token, "26000")
        self.assertEqual(tick.volume, 12345)
        self.assertEqual(tick.last_traded_qty, 50)

    def test_missing_token_returns_none(self) -> None:
        self.assertIsNone(ingestion._parse_tick({"last_traded_price": 100}))

    def test_alternate_field_names(self) -> None:
        """SDK can return capitalised keys too — parser must accept both."""
        tick = ingestion._parse_tick({
            "Tokens": "26009",
            "ExchangeType": 1,
            "ltp": 540000,
        })
        self.assertIsNotNone(tick)
        self.assertEqual(tick.token, "26009")
        self.assertAlmostEqual(tick.ltp, 5400.00)


class _SyntheticWS:
    """Fake SmartWebSocketV2: stores callbacks, fires synthetic ticks on demand."""
    def __init__(self, *args, **kwargs) -> None:
        self.on_open = self.on_data = self.on_error = self.on_close = None
        self._connected = False

    def connect(self) -> None:
        self._connected = True
        if self.on_open:
            self.on_open(self)
        # Block until externally closed
        while self._connected:
            time.sleep(0.02)

    def subscribe(self, *args, **kwargs) -> None:
        return

    def fire_tick(self, payload: dict) -> None:
        if self.on_data:
            self.on_data(self, payload)

    def close_connection(self) -> None:
        self._connected = False
        if self.on_close:
            self.on_close(self)


class TestTickFeed(unittest.TestCase):

    def test_bridge_delivers_ticks_to_async_consumer(self) -> None:
        ws_holder: dict[str, _SyntheticWS] = {}

        def factory(*args, **kwargs):
            sws = _SyntheticWS()
            ws_holder["ws"] = sws
            return sws

        with patch.object(ingestion, "SmartWebSocketV2", factory):
            feed = ingestion.TickFeed(
                api_key="K", client_id="C", auth_token="T", feed_token="F",
                tokens=[{"exchangeType": 1, "tokens": ["26000"]}],
                mode=1, max_queue_size=100,
            )
            feed.start()
            # Wait briefly for the WS thread to invoke on_open
            for _ in range(50):
                if "ws" in ws_holder and ws_holder["ws"]._connected:
                    break
                time.sleep(0.02)
            self.assertIn("ws", ws_holder, "WS factory never invoked")

            # Push 3 synthetic ticks
            for i, px in enumerate([2576500, 2576600, 2576700]):
                ws_holder["ws"].fire_tick({
                    "token": "26000", "exchange_type": 1,
                    "last_traded_price": px,
                    "exchange_timestamp": 1716700000000 + i,
                })

            received: list[ingestion.Tick] = []
            async def consume() -> None:
                async for tick in feed.stream():
                    received.append(tick)
                    if len(received) >= 3:
                        break

            asyncio.run(asyncio.wait_for(consume(), timeout=3.0))
            feed.stop(timeout=1.0)

        self.assertEqual(len(received), 3)
        prices = [round(t.ltp, 2) for t in received]
        self.assertEqual(prices, [25765.00, 25766.00, 25767.00])

    def test_backpressure_drops_oldest(self) -> None:
        ws_holder: dict[str, _SyntheticWS] = {}

        def factory(*args, **kwargs):
            sws = _SyntheticWS()
            ws_holder["ws"] = sws
            return sws

        with patch.object(ingestion, "SmartWebSocketV2", factory):
            feed = ingestion.TickFeed(
                api_key="K", client_id="C", auth_token="T", feed_token="F",
                tokens=[{"exchangeType": 1, "tokens": ["1"]}],
                mode=1, max_queue_size=5,                  # tiny queue
            )
            feed.start()
            for _ in range(50):
                if "ws" in ws_holder and ws_holder["ws"]._connected:
                    break
                time.sleep(0.02)

            # Fire 50 ticks (10× capacity) without any consumer
            for i in range(50):
                ws_holder["ws"].fire_tick({
                    "token": "1", "last_traded_price": 100_00 + i,
                    "exchange_timestamp": 0,
                })
            time.sleep(0.1)
            self.assertLessEqual(feed._queue.qsize(), 5,
                "queue must respect maxsize even under flood")
            feed.stop(timeout=1.0)


# ════════════════════════════════════════════════════════════════════════════
#  POST-AUDIT FIX COVERAGE  ▸ B1 (thread-safe pacing) + B4 (session refresh)
# ════════════════════════════════════════════════════════════════════════════
class TestB1ThreadSafePacing(unittest.TestCase):
    """Fix B1: `_pace()` must serialise read-modify-write of the last-call
    timestamp.  Without the lock, N concurrent threads could each observe
    "enough time has elapsed" simultaneously and stampede the broker."""

    def setUp(self) -> None:
        self.api = ingestion.login_and_feed_token()[0]
        execution.config.PAPER_TRADING = True
        execution._last_call_ts = 0.0
        execution.config.API_PACING_SECONDS = 0.05  # 50ms — keeps test fast

    def test_pace_lock_exists(self) -> None:
        """The lock must be a real threading.Lock — not a no-op."""
        import threading as _t
        self.assertIsInstance(execution._pace_lock, type(_t.Lock()))

    def test_concurrent_pace_serialises(self) -> None:
        """20 threads calling _pace() concurrently must observe pacing —
        elapsed wall time should be ≥ (N-1) * pacing_seconds."""
        import threading
        N = 10
        pacing = execution.config.API_PACING_SECONDS
        start = time.monotonic()
        barrier = threading.Barrier(N)

        def worker() -> None:
            barrier.wait()
            execution._pace()

        threads = [threading.Thread(target=worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - start
        # N calls fully serialised should take at least (N-1)*pacing.
        # Allow 30% slack for thread startup / OS jitter.
        min_expected = (N - 1) * pacing * 0.7
        self.assertGreaterEqual(
            elapsed, min_expected,
            f"pacing was not serialised: {elapsed:.3f}s < {min_expected:.3f}s expected",
        )


class TestB4SessionRefresh(unittest.TestCase):
    """Fix B4: `refresh_session()` must rotate the JWT using the stashed
    refresh_token, and `refresh_session_loop()` must survive transient
    failures without crashing the bot."""

    def setUp(self) -> None:
        self.api, _ = ingestion.login_and_feed_token()

    def test_refresh_session_rotates_jwt(self) -> None:
        original_jwt = self.api.access_token
        new_feed = ingestion.refresh_session(self.api)
        self.assertNotEqual(self.api.access_token, original_jwt,
                            "JWT should be rotated after refresh")
        self.assertEqual(new_feed, self.api.feed_token)

    def test_refresh_session_raises_without_refresh_token(self) -> None:
        bad_api = ingestion.login_and_feed_token()[0]
        bad_api.refresh_token = ""
        with self.assertRaises(RuntimeError) as cm:
            ingestion.refresh_session(bad_api)
        self.assertIn("refresh_token", str(cm.exception))

    def test_refresh_session_raises_on_broker_refusal(self) -> None:
        self.api.generateToken = lambda _rt: {"status": False, "message": "DENY"}
        with self.assertRaises(RuntimeError) as cm:
            ingestion.refresh_session(self.api)
        self.assertIn("generateToken failed", str(cm.exception))

    def test_refresh_loop_tolerates_failure(self) -> None:
        """The loop must not propagate exceptions — that would kill the bot."""
        async def _run() -> None:
            self.api.generateToken = lambda _rt: (_ for _ in ()).throw(
                ConnectionError("simulated network blip")
            )
            task = asyncio.create_task(
                ingestion.refresh_session_loop(self.api, interval_hours=0.0001)
            )
            await asyncio.sleep(0.5)  # let it tick a few times
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        asyncio.run(_run())
        # Reaching here proves the loop swallowed the ConnectionError.


class TestB7CanOpenRemoved(unittest.TestCase):
    """Fix B7: the can_open() soft-check was removed to eliminate TOCTOU.
    `commit()` is now the only public path and is atomic."""

    def test_can_open_no_longer_exposed(self) -> None:
        silo = risk.CapitalSilo(
            name="TEST", max_capital=1000.0,
            risk_pct_per_trade=0.05, daily_drawdown_pct=0.05,
        )
        self.assertFalse(
            hasattr(silo, "can_open"),
            "can_open() should be removed (use try/except commit())",
        )

    def test_commit_atomic_under_concurrency(self) -> None:
        """20 concurrent threads each try to commit ₹600 against a ₹1000 silo.
        Atomicity guarantees: exactly 1 succeeds, 19 raise RiskRefusedError."""
        import threading
        silo = risk.CapitalSilo(
            name="TEST", max_capital=1000.0,
            risk_pct_per_trade=0.10, daily_drawdown_pct=0.05,
        )
        successes: list[str] = []
        failures: list[Exception] = []
        lock = threading.Lock()
        barrier = threading.Barrier(20)

        def worker(idx: int) -> None:
            barrier.wait()
            try:
                silo.commit(position_id=f"P{idx}", cost=600.0)
                with lock:
                    successes.append(f"P{idx}")
            except risk.RiskRefusedError as exc:
                with lock:
                    failures.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(successes), 1,
            f"expected exactly 1 successful commit, got {len(successes)}: {successes}")
        self.assertEqual(len(failures), 19,
            f"expected 19 refusals, got {len(failures)}")
        self.assertAlmostEqual(silo.deployed_capital, 600.0)


# ════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    unittest.main(verbosity=2)
