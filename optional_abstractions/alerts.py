"""code-and-capital-offera  ▸  alerts.py
==============================================================================
Production-grade webhook alert layer.

This module is what replaces `execution.py` in the Offer A bundle.  When a
signal fires, the strategy code calls one of the four `build_*()` helpers
to produce a payload, then hands the payload to `dispatch()`.  Dispatch
fans out to:

    1.  Telegram     (if ALERT_TELEGRAM_BOT_TOKEN + _CHAT_ID are set)
    2.  Discord      (if ALERT_DISCORD_WEBHOOK_URL is set)
    3.  stdout/log   (always — colourised when a TTY is attached)

A dispatch failure on ONE channel never blocks the others.  HTTP timeouts
are bounded by `config.ALERT_HTTP_TIMEOUT` so the tick loop can never stall
on a flaky webhook endpoint.

Four canonical event types are defined.  Add more by following the same
shape — every payload is a plain `Alert` dataclass with a deterministic
`render_text()` so the simulator can pretty-print without hitting the wire.

USAGE
─────
    from src import alerts, risk

    # On a risk-engine drawdown breach:
    a = alerts.build_critical_risk(
        silo_id="OPT_SELLING_SILO_02",
        realised_drawdown=-15420.0,
        hard_threshold=-15000.0,
    )
    alerts.dispatch(a)

    # On an alpha-signal trigger from your strategy:
    a = alerts.build_strategy_match(
        asset="ETH/USDT-PERP",
        indicator="WebSocket 5-Min Volume Spike (>3.2x Dev)",
        volatility_pct=4.85,
        position_size="12.5 ETH",
        direction="BREAKOUT LONG",
    )
    alerts.dispatch(a)
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

from . import config

log = logging.getLogger("alerts")

IST = timezone(timedelta(hours=5, minutes=30))


# ════════════════════════════════════════════════════════════════════════════
#  ANSI COLOURS  (terminal-only — webhook payloads use plain text + emoji)
# ════════════════════════════════════════════════════════════════════════════
class _C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    GREY    = "\033[90m"


def _supports_colour() -> bool:
    """Best-effort ANSI capability check."""
    if not hasattr(sys.stdout, "isatty"):
        return False
    if not sys.stdout.isatty():
        return False
    # Windows 10+ supports ANSI when run under Windows Terminal / VS Code.
    # We don't try to detect — the worst case is a stray ESC[31m in output.
    return True


# ════════════════════════════════════════════════════════════════════════════
#  ALERT TYPES
# ════════════════════════════════════════════════════════════════════════════
class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING  = "WARNING"
    INFO     = "INFO"
    SUCCESS  = "SUCCESS"


_SEV_GLYPH = {
    Severity.CRITICAL: ("🚨", _C.RED),
    Severity.WARNING:  ("⚡", _C.YELLOW),
    Severity.INFO:     ("ℹ️ ", _C.CYAN),
    Severity.SUCCESS:  ("✅", _C.GREEN),
}


@dataclass
class Alert:
    """A single webhook-bound event.

    Both the dashboard and the dispatcher consume the same shape, so the
    simulator can replay any event end-to-end without hitting the wire.
    """
    event_type: str
    severity: Severity
    title: str
    fields: dict[str, str] = field(default_factory=dict)
    action_required: str = ""
    timestamp_ist: str = field(
        default_factory=lambda: datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " IST"
    )

    # ── presentation ────────────────────────────────────────────────────
    def render_text(self, *, colour: bool = False) -> str:
        """Render to the canonical Telegram/Discord plain-text format."""
        glyph, ansi = _SEV_GLYPH[self.severity]
        if colour and _supports_colour():
            on = ansi + _C.BOLD
            off = _C.RESET
            dim = _C.GREY
        else:
            on = off = dim = ""

        brand = config.ALERT_BRAND_NAME
        lines = [
            f"{on}{glyph} [{brand}] {self.title}{off}",
            f"{dim}Timestamp:{off} {self.timestamp_ist}",
            f"{dim}Event:{off} {self.event_type}",
            f"{dim}{'-' * 47}{off}",
        ]
        for k, v in self.fields.items():
            lines.append(f"{dim}{k}:{off} {v}")
        if self.action_required:
            lines.append(f"{dim}Action Required:{off} {on}{self.action_required}{off}")
        return "\n".join(lines)

    def to_discord_payload(self) -> dict[str, Any]:
        """Discord uses Markdown in the `content` field."""
        return {"content": "```md\n" + self.render_text(colour=False) + "\n```"}

    def to_telegram_payload(self, chat_id: str) -> dict[str, Any]:
        """Telegram likes Markdown V1 — we wrap in a monospace block."""
        return {
            "chat_id": chat_id,
            "text": "```\n" + self.render_text(colour=False) + "\n```",
            "parse_mode": "Markdown",
        }


# ════════════════════════════════════════════════════════════════════════════
#  THE FOUR CANONICAL EVENTS  (Deliverable 3)
# ════════════════════════════════════════════════════════════════════════════
def build_critical_risk(
    *,
    silo_id: str,
    realised_drawdown: float,
    hard_threshold: float,
) -> Alert:
    """`[CRITICAL RISK]` — capital silo drawdown cap breach."""
    return Alert(
        event_type="Drawdown Cap Violation Alert",
        severity=Severity.CRITICAL,
        title="SYSTEM CRITICAL",
        fields={
            "Silo ID": silo_id,
            "Current Realized Drawdown": f"₹{realised_drawdown:,.2f}",
            "Hard Silo Stop Threshold":  f"₹{hard_threshold:,.2f}",
            "Status": "RISK BREACHED. Capital Allocation Blocked.",
        },
        action_required="Deactivate manual execution terminal immediately.",
    )


def build_strategy_match(
    *,
    asset: str,
    indicator: str,
    volatility_pct: float,
    position_size: str,
    direction: str,
) -> Alert:
    """`[STRATEGY MATCH]` — alpha signal triggered."""
    return Alert(
        event_type="Strategy Alpha Match",
        severity=Severity.WARNING,
        title="STRATEGY ALPHA MATCH",
        fields={
            "Asset Class": asset,
            "Indicator":   indicator,
            "Current Volatility Index":      f"{volatility_pct:.2f}%",
            "Calculated Position Size Matrix": position_size,
            "Target Direction": direction,
        },
        action_required="Webhook dispatched to execution chat. Automated execution bypassed.",
    )


def build_engine_status(
    *,
    reconnect_attempt: int,
    max_reconnects: int,
    backoff_seconds: float,
    reason: str = "WebSocket connection dropped",
) -> Alert:
    """`[ENGINE STATUS]` — WebSocket dropped, exponential backoff initiated."""
    return Alert(
        event_type="WebSocket Reconnection Sequence",
        severity=Severity.INFO,
        title="ENGINE STATUS",
        fields={
            "Reason": reason,
            "Reconnect Attempt": f"{reconnect_attempt} / {max_reconnects}",
            "Exponential Backoff": f"{backoff_seconds:.1f}s",
            "Status": "Awaiting next reconnect window.",
        },
    )


def build_order_filled(
    *,
    side: str,
    symbol: str,
    quantity: int,
    fill_price: float,
    order_id: str,
    mode: str = "LIVE",
    silo: str = "",
) -> Alert:
    """`[ORDER FILLED]` — execution engine confirms a broker fill.

    Full-bundle only.  Fires from `execution.place_market` /
    `place_limit` once `wait_for_fill()` returns success.  In PAPER mode
    the payload is identical except for ``mode='PAPER'`` and the synthetic
    order_id prefix.
    """
    return Alert(
        event_type="Broker Fill Confirmation",
        severity=Severity.SUCCESS,
        title="ORDER FILLED",
        fields={
            "Mode":       mode,
            "Side":       side.upper(),
            "Symbol":     symbol,
            "Quantity":   str(quantity),
            "Fill Price": f"₹{fill_price:,.2f}",
            "Order ID":   order_id,
            "Silo":       silo or "—",
        },
    )


def build_compliance_lock(
    *,
    session_pnl: float,
    locked_silos: list[str],
    reason: str = "End-of-Day Auto-Teardown",
) -> Alert:
    """`[COMPLIANCE LOCK]` — EOD auto-teardown and system safe-lock."""
    return Alert(
        event_type="Compliance / EOD Safe-Lock",
        severity=Severity.SUCCESS,
        title="COMPLIANCE LOCK ACTIVE",
        fields={
            "Reason": reason,
            "Session PnL": f"₹{session_pnl:,.2f}",
            "Silos Locked": ", ".join(locked_silos) if locked_silos else "ALL",
            "Status": "System safe-locked until next session open.",
        },
        action_required="No action required. Logs retained at config.LOG_DIR.",
    )


# ════════════════════════════════════════════════════════════════════════════
#  DISPATCH
# ════════════════════════════════════════════════════════════════════════════
def dispatch(alert: Alert) -> dict[str, bool]:
    """Fan-out an `Alert` to every configured channel.

    Returns
    ──────
    A dict ``{"telegram": bool, "discord": bool, "stdout": bool}`` indicating
    which channels accepted the payload.  This is purely informational —
    callers should NOT branch on it (alerts are fire-and-forget).
    """
    results = {"telegram": False, "discord": False, "stdout": True}

    # ── 1. stdout (always; colourised when TTY) ──────────────────────────
    print(alert.render_text(colour=True))
    print()

    # ── 2. Telegram ──────────────────────────────────────────────────────
    if config.ALERT_TELEGRAM_BOT_TOKEN and config.ALERT_TELEGRAM_CHAT_ID:
        try:
            url = (
                f"https://api.telegram.org/bot"
                f"{config.ALERT_TELEGRAM_BOT_TOKEN}/sendMessage"
            )
            payload = alert.to_telegram_payload(config.ALERT_TELEGRAM_CHAT_ID)
            _post_json(url, payload)
            results["telegram"] = True
        except Exception as exc:                                       # noqa: BLE001
            log.warning("Telegram dispatch failed: %s", exc)

    # ── 3. Discord ───────────────────────────────────────────────────────
    if config.ALERT_DISCORD_WEBHOOK_URL:
        try:
            _post_json(config.ALERT_DISCORD_WEBHOOK_URL, alert.to_discord_payload())
            results["discord"] = True
        except Exception as exc:                                       # noqa: BLE001
            log.warning("Discord dispatch failed: %s", exc)

    return results


def _post_json(url: str, payload: dict[str, Any]) -> None:
    """Minimal stdlib HTTP POST — no `requests` dependency needed."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.ALERT_HTTP_TIMEOUT) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status} from {url}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error to {url}: {exc.reason}") from exc


# ════════════════════════════════════════════════════════════════════════════
#  DRY-RUN
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    """Run with `python -m src.alerts` to smoke-test the four payload shapes
    against stdout (no network calls if webhooks aren't configured)."""
    config.setup_logging()

    dispatch(build_critical_risk(
        silo_id="OPT_SELLING_SILO_02",
        realised_drawdown=-15420.00,
        hard_threshold=-15000.00,
    ))
    dispatch(build_strategy_match(
        asset="ETH/USDT Perpetual Futures",
        indicator="WebSocket 5-Min Volume Spike (>3.2x Dev)",
        volatility_pct=4.85,
        position_size="12.5 ETH",
        direction="BREAKOUT LONG",
    ))
    dispatch(build_engine_status(
        reconnect_attempt=3,
        max_reconnects=20,
        backoff_seconds=12.0,
    ))
    dispatch(build_order_filled(
        side="BUY",
        symbol="NIFTY26MAY26000CE",
        quantity=75,
        fill_price=152.40,
        order_id="251104231234567",
        mode="LIVE",
        silo="DERIV",
    ))
    dispatch(build_compliance_lock(
        session_pnl=+8240.50,
        locked_silos=["TREND", "SWING", "DERIV"],
    ))
