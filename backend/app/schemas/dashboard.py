"""
================================================================================
schemas/dashboard.py  ▸  Trading terminal wire contracts
================================================================================
Response models for every dashboard panel. Field names are serialised in
camelCase (via ``serialization_alias``) to match the frontend's Zod schemas
exactly — the client parses these shapes, so drift here becomes a client-side
parse error rather than a silent bug.

Money is always in rupees. P&L and change fields are signed: positive is
profit, negative is loss.
================================================================================
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Positions ────────────────────────────────────────────────────────────────

class PositionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    side: Literal["BUY", "SELL"]
    symbol: str
    qty: int
    avg_price: float = Field(serialization_alias="avgPrice")
    ltp: float
    pnl: float
    pnl_pct: float = Field(serialization_alias="pnlPct")


class PositionsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[PositionResponse]
    aggregate_unrealized: float = Field(serialization_alias="aggregateUnrealized")
    #: True when items came from a live broker call; False = broker not
    #: connected or the call failed (UI shows an honest empty/notice state).
    live: bool = True
    #: When set, an explanation the UI can surface (e.g. "reconnect broker").
    notice: str | None = None


# ── Strategies ───────────────────────────────────────────────────────────────

class StrategyResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    name: str
    descriptor: str
    status: Literal["running", "stopped"]
    trades_today: int = Field(serialization_alias="tradesToday")
    pnl: float


class StrategyStatusUpdate(BaseModel):
    status: Literal["running", "stopped"]


# ── Orders ───────────────────────────────────────────────────────────────────

class OrderResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    side: Literal["BUY", "SELL"]
    symbol: str
    time: str
    qty: int
    price: float
    status: Literal["FILLED", "CANCELLED", "PENDING", "REJECTED"]


# ── Market indices (hero strip) ──────────────────────────────────────────────

class IndexQuoteResponse(BaseModel):
    label: str
    value: str
    change_pct: float = Field(serialization_alias="changePct")
    #: Optional inline sparkline path. Live LTP quotes carry no intraday shape,
    #: so this is omitted for live data and the ticker renders without it.
    sparkline: str | None = None


# ── Metric cards ─────────────────────────────────────────────────────────────

class MetricsResponse(BaseModel):
    """The five executive-summary cards + hero greeting data."""

    model_config = ConfigDict(populate_by_name=True)

    # Card 1 — Total P&L (Today)
    total_pnl: float = Field(serialization_alias="totalPnl")
    total_pnl_pct: float = Field(serialization_alias="totalPnlPct")
    gross_pnl: float = Field(serialization_alias="grossPnl")
    charges: float

    # Card 2 — Open Positions
    open_positions_count: int = Field(serialization_alias="openPositionsCount")
    net_exposure: float = Field(serialization_alias="netExposure")
    long_count: int = Field(serialization_alias="longCount")
    short_count: int = Field(serialization_alias="shortCount")

    # Card 3 — Executed Trades
    executed_trades: int = Field(serialization_alias="executedTrades")
    win_rate: float = Field(serialization_alias="winRate")
    wins: int
    losses: int

    # Card 4 — Available Margin
    available_margin: float = Field(serialization_alias="availableMargin")
    total_capital: float = Field(serialization_alias="totalCapital")

    # Card 5 — Execution Gateway
    gateway_name: str = Field(serialization_alias="gatewayName")
    gateway_connected: bool = Field(serialization_alias="gatewayConnected")
    gateway_mode: str = Field(serialization_alias="gatewayMode")
    #: True only when the user has explicitly enabled live trading. Default
    #: false = paper (simulated) — orders never reach the broker.
    live_trading_enabled: bool = Field(serialization_alias="liveTradingEnabled")
    #: True when the background auto-trading engine is running for this user.
    auto_trading_enabled: bool = Field(serialization_alias="autoTradingEnabled")

    # Hero
    active_strategies: int = Field(serialization_alias="activeStrategies")
    engine_online: bool = Field(serialization_alias="engineOnline")


# ── Intraday P&L curve ───────────────────────────────────────────────────────

class IntradayPnlResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    #: Points as (minute-from-open, pnl-in-rupees) pairs.
    points: list[list[float]]
    current_pnl: float = Field(serialization_alias="currentPnl")
    day_high: float = Field(serialization_alias="dayHigh")
    day_low: float = Field(serialization_alias="dayLow")
    realized: float
    unrealized: float


# ── Panic square-off ─────────────────────────────────────────────────────────

class PanicResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    closed_positions: int = Field(serialization_alias="closedPositions")
    cancelled_orders: int = Field(serialization_alias="cancelledOrders")
    message: str


# ── Broker accounts ──────────────────────────────────────────────────────────

class BrokerAccountResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    broker_id: str = Field(serialization_alias="brokerId")
    masked_client_id: str | None = Field(
        default=None, serialization_alias="maskedClientId"
    )
    account_name: str | None = Field(default=None, serialization_alias="accountName")
    status: Literal["connected", "error"]
    connected_at: str = Field(serialization_alias="connectedAt")


class ConnectBrokerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    broker_id: str = Field(alias="brokerId")
    # Free-form per-broker credential map. Never logged or echoed back.
    credentials: dict[str, str]


class ConnectBrokerResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connected: bool
    broker_id: str = Field(serialization_alias="brokerId")
    masked_client_id: str | None = Field(
        default=None, serialization_alias="maskedClientId"
    )
    account_name: str | None = Field(default=None, serialization_alias="accountName")
    available_margin: float | None = Field(
        default=None, serialization_alias="availableMargin"
    )


# ── Quant reports ────────────────────────────────────────────────────────────

class ReportKpi(BaseModel):
    """One headline number on the reports page."""

    model_config = ConfigDict(populate_by_name=True)

    label: str
    value: str
    change_pct: float | None = Field(default=None, serialization_alias="changePct")
    tone: Literal["profit", "loss", "neutral"] = "neutral"


class StrategyBreakdownRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    trades: int
    pnl: float
    win_rate: float = Field(serialization_alias="winRate")


class AlphaAttributionRow(BaseModel):
    """One strategy's contribution to total realised alpha."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    #: Signed rupee P&L attributed to this strategy.
    pnl: float
    #: Share of the total gross contribution, 0-100. Bar width on the UI.
    contribution_pct: float = Field(serialization_alias="contributionPct")


class HeatmapCell(BaseModel):
    """One calendar day on the daily alpha heatmap."""

    model_config = ConfigDict(populate_by_name=True)

    date: str                       # YYYY-MM-DD
    day: int                        # day-of-month, for the cell label
    #: Net P&L booked that day. None = no session (weekend/holiday/no data).
    pnl: float | None = None
    #: profit | loss | flat | none — drives the cell colour.
    tone: Literal["profit", "loss", "flat", "none"] = "none"


class ExpectancyRow(BaseModel):
    """One row of the trade-expectancy / execution breakdown table."""

    model_config = ConfigDict(populate_by_name=True)

    metric: str
    value: str
    benchmark: str
    #: Institutional-status label, e.g. OPTIMAL / CONTROLLED / SUPERIOR.
    status: str
    #: Qualitative tone for the status badge.
    tone: Literal["profit", "loss", "neutral"] = "neutral"
    #: Normalised 0-1 risk score for the row (real when computable, else 0).
    risk_score: float = Field(default=0.0, serialization_alias="riskScore")


class ReportsResponse(BaseModel):
    """Aggregate performance analytics for the Quant Reports page.

    Every figure is derived from the user's real recorded intraday P&L points
    and live positions. When there is no activity, series are empty and scalars
    are truthful zeros — the page never renders invented numbers.
    """

    model_config = ConfigDict(populate_by_name=True)

    kpis: list[ReportKpi]
    net_pnl: float = Field(serialization_alias="netPnl")
    gross_pnl: float = Field(serialization_alias="grossPnl")
    total_charges: float = Field(serialization_alias="totalCharges")
    expectancy: float
    win_rate: float = Field(serialization_alias="winRate")
    profit_factor: float = Field(serialization_alias="profitFactor")
    max_drawdown: float = Field(serialization_alias="maxDrawdown")
    trades_total: int = Field(serialization_alias="tradesTotal")
    wins: int
    losses: int
    avg_win: float = Field(serialization_alias="avgWin")
    avg_loss: float = Field(serialization_alias="avgLoss")
    equity_curve: list[list[float]] = Field(serialization_alias="equityCurve")
    by_strategy: list[StrategyBreakdownRow] = Field(serialization_alias="byStrategy")

    # ── Alpha telemetry (richer analytics) ────────────────────────────────────
    #: Risk-adjusted return ratios computed from the intraday P&L increments.
    sharpe: float = 0.0
    sortino: float = 0.0
    #: Peak drawdown as a signed percentage of the running high-water equity.
    max_drawdown_pct: float = Field(default=0.0, serialization_alias="maxDrawdownPct")
    #: Net profit ÷ max drawdown. How many drawdowns the run has "paid back".
    recovery_factor: float = Field(default=0.0, serialization_alias="recoveryFactor")
    #: Return on deployed capital, percent (0 when capital unknown).
    return_on_capital_pct: float = Field(
        default=0.0, serialization_alias="returnOnCapitalPct"
    )
    #: Total modelled brokerage + taxes (0 until the engine records real costs).
    net_yield: float = Field(default=0.0, serialization_alias="netYield")
    #: Benchmark (NIFTY) curve aligned to equity_curve x-axis; empty if no live
    #: benchmark data is available for the window.
    benchmark_curve: list[list[float]] = Field(
        default_factory=list, serialization_alias="benchmarkCurve"
    )
    #: Underwater curve: [x, drawdown_pct(≤0)] points along the equity curve.
    drawdown_curve: list[list[float]] = Field(
        default_factory=list, serialization_alias="drawdownCurve"
    )
    #: Per-strategy contribution to gross alpha.
    alpha_attribution: list[AlphaAttributionRow] = Field(
        default_factory=list, serialization_alias="alphaAttribution"
    )
    #: Average pairwise model correlation across active strategies, 0-1.
    model_correlation: float = Field(
        default=0.0, serialization_alias="modelCorrelation"
    )
    #: Trailing calendar-day heatmap (most recent first day → last).
    daily_heatmap: list[HeatmapCell] = Field(
        default_factory=list, serialization_alias="dailyHeatmap"
    )
    #: Trade-expectancy / execution breakdown rows.
    expectancy_rows: list[ExpectancyRow] = Field(
        default_factory=list, serialization_alias="expectancyRows"
    )

    #: True until there are enough closed trades to read the numbers seriously.
    small_sample: bool = Field(serialization_alias="smallSample")


# ── Risk parameters ──────────────────────────────────────────────────────────

class RiskParametersResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    max_daily_loss: float = Field(serialization_alias="maxDailyLoss")
    max_position_size: float = Field(serialization_alias="maxPositionSize")
    max_open_positions: int = Field(serialization_alias="maxOpenPositions")
    default_stop_loss_pct: float = Field(serialization_alias="defaultStopLossPct")
    default_target_pct: float = Field(serialization_alias="defaultTargetPct")
    max_margin_utilization_pct: float = Field(
        serialization_alias="maxMarginUtilizationPct"
    )

    # Intraday loss & drawdown
    max_trailing_drawdown_pct: float = Field(
        serialization_alias="maxTrailingDrawdownPct"
    )
    consecutive_loss_limit: int = Field(serialization_alias="consecutiveLossLimit")
    consecutive_loss_breaker_enabled: bool = Field(
        serialization_alias="consecutiveLossBreakerEnabled"
    )

    # Order execution & slippage
    max_slippage_pct: float = Field(serialization_alias="maxSlippagePct")
    order_rate_limit_per_sec: int = Field(
        serialization_alias="orderRateLimitPerSec"
    )
    fat_finger_max_lots: int = Field(serialization_alias="fatFingerMaxLots")
    fat_finger_max_shares: int = Field(serialization_alias="fatFingerMaxShares")

    # Margin & overnight exposure
    auto_square_off_time: str = Field(serialization_alias="autoSquareOffTime")
    overnight_options_freeze: bool = Field(
        serialization_alias="overnightOptionsFreeze"
    )

    # Alerts, webhooks & notifications
    sms_webhook_alerts_enabled: bool = Field(
        serialization_alias="smsWebhookAlertsEnabled"
    )
    auditory_telemetry_enabled: bool = Field(
        serialization_alias="auditoryTelemetryEnabled"
    )
    custom_webhook_uri: str | None = Field(
        default=None, serialization_alias="customWebhookUri"
    )

    # Live-trading switch. False = paper (default, simulated). True = real
    # orders route to the broker.
    live_trading_enabled: bool = Field(serialization_alias="liveTradingEnabled")

    # Auto-trading switch. False (default) = engine idle. True = engine trades
    # automatically with no per-trade approval.
    auto_trading_enabled: bool = Field(serialization_alias="autoTradingEnabled")

    kill_switch_armed: bool = Field(serialization_alias="killSwitchArmed")


class RiskParametersUpdate(BaseModel):
    """All fields optional — the client PATCHes only what changed."""

    model_config = ConfigDict(populate_by_name=True)

    max_daily_loss: float | None = Field(default=None, alias="maxDailyLoss", ge=0)
    max_position_size: float | None = Field(
        default=None, alias="maxPositionSize", ge=0
    )
    max_open_positions: int | None = Field(
        default=None, alias="maxOpenPositions", ge=0, le=100
    )
    default_stop_loss_pct: float | None = Field(
        default=None, alias="defaultStopLossPct", ge=0, le=100
    )
    default_target_pct: float | None = Field(
        default=None, alias="defaultTargetPct", ge=0, le=1000
    )
    max_margin_utilization_pct: float | None = Field(
        default=None, alias="maxMarginUtilizationPct", ge=0, le=100
    )

    # Intraday loss & drawdown
    max_trailing_drawdown_pct: float | None = Field(
        default=None, alias="maxTrailingDrawdownPct", ge=0, le=100
    )
    consecutive_loss_limit: int | None = Field(
        default=None, alias="consecutiveLossLimit", ge=1, le=50
    )
    consecutive_loss_breaker_enabled: bool | None = Field(
        default=None, alias="consecutiveLossBreakerEnabled"
    )

    # Order execution & slippage
    max_slippage_pct: float | None = Field(
        default=None, alias="maxSlippagePct", ge=0, le=100
    )
    order_rate_limit_per_sec: int | None = Field(
        default=None, alias="orderRateLimitPerSec", ge=1, le=1000
    )
    fat_finger_max_lots: int | None = Field(
        default=None, alias="fatFingerMaxLots", ge=1, le=1_000_000
    )
    fat_finger_max_shares: int | None = Field(
        default=None, alias="fatFingerMaxShares", ge=1, le=100_000_000
    )

    # Margin & overnight exposure
    auto_square_off_time: str | None = Field(
        default=None,
        alias="autoSquareOffTime",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",  # HH:MM 24-hour
    )
    overnight_options_freeze: bool | None = Field(
        default=None, alias="overnightOptionsFreeze"
    )

    # Alerts, webhooks & notifications
    sms_webhook_alerts_enabled: bool | None = Field(
        default=None, alias="smsWebhookAlertsEnabled"
    )
    auditory_telemetry_enabled: bool | None = Field(
        default=None, alias="auditoryTelemetryEnabled"
    )
    custom_webhook_uri: str | None = Field(
        default=None, alias="customWebhookUri", max_length=500
    )

    live_trading_enabled: bool | None = Field(
        default=None, alias="liveTradingEnabled"
    )

    auto_trading_enabled: bool | None = Field(
        default=None, alias="autoTradingEnabled"
    )

    kill_switch_armed: bool | None = Field(default=None, alias="killSwitchArmed")
