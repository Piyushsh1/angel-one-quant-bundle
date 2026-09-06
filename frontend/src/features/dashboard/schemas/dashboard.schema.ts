import { z } from 'zod';

/**
 * Response contracts for the trading terminal.
 *
 * These mirror the backend Pydantic models in
 * `backend/app/schemas/dashboard.py` (camelCase on the wire). Validated at the
 * API boundary so a backend change surfaces as an explicit parse failure rather
 * than an undefined field deep in a panel.
 *
 * Money is in rupees; P&L and change fields are signed (positive = profit).
 */

// ── Positions ────────────────────────────────────────────────────────────────

export const positionSchema = z.object({
  id: z.string(),
  side: z.enum(['BUY', 'SELL']),
  symbol: z.string(),
  qty: z.number(),
  avgPrice: z.number(),
  ltp: z.number(),
  pnl: z.number(),
  pnlPct: z.number(),
});

export const positionsResponseSchema = z.object({
  items: z.array(positionSchema),
  aggregateUnrealized: z.number(),
});

export type Position = z.infer<typeof positionSchema>;
export type PositionsResponse = z.infer<typeof positionsResponseSchema>;

// ── Strategies ───────────────────────────────────────────────────────────────

export const strategySchema = z.object({
  id: z.string(),
  name: z.string(),
  descriptor: z.string(),
  status: z.enum(['running', 'stopped']),
  tradesToday: z.number(),
  pnl: z.number(),
});

export const strategiesResponseSchema = z.array(strategySchema);

export type Strategy = z.infer<typeof strategySchema>;
export type StrategyStatus = Strategy['status'];

// ── Orders ───────────────────────────────────────────────────────────────────

export const orderSchema = z.object({
  id: z.string(),
  side: z.enum(['BUY', 'SELL']),
  symbol: z.string(),
  time: z.string(),
  qty: z.number(),
  price: z.number(),
  status: z.enum(['FILLED', 'CANCELLED', 'PENDING', 'REJECTED']),
});

export const ordersResponseSchema = z.array(orderSchema);

export type Order = z.infer<typeof orderSchema>;
export type OrderStatus = Order['status'];

// ── Market indices ─────────────────────────────────────────────────────────

export const indexQuoteSchema = z.object({
  label: z.string(),
  value: z.string(),
  changePct: z.number(),
  sparkline: z.string().nullable().optional(),
});

export const indicesResponseSchema = z.array(indexQuoteSchema);

export type IndexQuote = z.infer<typeof indexQuoteSchema>;

// ── Metrics + hero ─────────────────────────────────────────────────────────

export const metricsSchema = z.object({
  totalPnl: z.number(),
  totalPnlPct: z.number(),
  grossPnl: z.number(),
  charges: z.number(),

  openPositionsCount: z.number(),
  netExposure: z.number(),
  longCount: z.number(),
  shortCount: z.number(),

  executedTrades: z.number(),
  winRate: z.number(),
  wins: z.number(),
  losses: z.number(),

  availableMargin: z.number(),
  totalCapital: z.number(),

  gatewayName: z.string(),
  gatewayConnected: z.boolean(),
  gatewayMode: z.string(),
  liveTradingEnabled: z.boolean(),
  autoTradingEnabled: z.boolean(),

  activeStrategies: z.number(),
  engineOnline: z.boolean(),
});

export type Metrics = z.infer<typeof metricsSchema>;

// ── Intraday P&L curve ───────────────────────────────────────────────────────

export const intradayPnlSchema = z.object({
  points: z.array(z.array(z.number())),
  currentPnl: z.number(),
  dayHigh: z.number(),
  dayLow: z.number(),
  realized: z.number(),
  unrealized: z.number(),
});

export type IntradayPnl = z.infer<typeof intradayPnlSchema>;

// ── Panic ────────────────────────────────────────────────────────────────────

export const panicResponseSchema = z.object({
  closedPositions: z.number(),
  cancelledOrders: z.number(),
  message: z.string(),
});

export type PanicResponse = z.infer<typeof panicResponseSchema>;

// ── Broker accounts ──────────────────────────────────────────────────────────

export const brokerAccountSchema = z.object({
  id: z.string(),
  brokerId: z.string(),
  maskedClientId: z.string().nullable().optional(),
  accountName: z.string().nullable().optional(),
  status: z.enum(['connected', 'error']),
  connectedAt: z.string(),
});

export const brokerAccountsResponseSchema = z.array(brokerAccountSchema);

export type BrokerAccount = z.infer<typeof brokerAccountSchema>;

// ── Quant reports ────────────────────────────────────────────────────────────

export const reportKpiSchema = z.object({
  label: z.string(),
  value: z.string(),
  changePct: z.number().nullable().optional(),
  tone: z.enum(['profit', 'loss', 'neutral']),
});

export const strategyBreakdownRowSchema = z.object({
  name: z.string(),
  trades: z.number(),
  pnl: z.number(),
  winRate: z.number(),
});

export const alphaAttributionRowSchema = z.object({
  name: z.string(),
  pnl: z.number(),
  contributionPct: z.number(),
});

export const heatmapCellSchema = z.object({
  date: z.string(),
  day: z.number(),
  pnl: z.number().nullable().optional(),
  tone: z.enum(['profit', 'loss', 'flat', 'none']),
});

export const expectancyRowSchema = z.object({
  metric: z.string(),
  value: z.string(),
  benchmark: z.string(),
  status: z.string(),
  tone: z.enum(['profit', 'loss', 'neutral']),
  riskScore: z.number(),
});

export const reportsSchema = z.object({
  kpis: z.array(reportKpiSchema),
  netPnl: z.number(),
  grossPnl: z.number(),
  totalCharges: z.number(),
  expectancy: z.number(),
  winRate: z.number(),
  profitFactor: z.number(),
  maxDrawdown: z.number(),
  tradesTotal: z.number(),
  wins: z.number(),
  losses: z.number(),
  avgWin: z.number(),
  avgLoss: z.number(),
  equityCurve: z.array(z.array(z.number())),
  byStrategy: z.array(strategyBreakdownRowSchema),

  // Alpha telemetry
  sharpe: z.number(),
  sortino: z.number(),
  maxDrawdownPct: z.number(),
  recoveryFactor: z.number(),
  returnOnCapitalPct: z.number(),
  netYield: z.number(),
  benchmarkCurve: z.array(z.array(z.number())),
  drawdownCurve: z.array(z.array(z.number())),
  alphaAttribution: z.array(alphaAttributionRowSchema),
  modelCorrelation: z.number(),
  dailyHeatmap: z.array(heatmapCellSchema),
  expectancyRows: z.array(expectancyRowSchema),

  smallSample: z.boolean(),
});

export type ReportKpi = z.infer<typeof reportKpiSchema>;
export type AlphaAttributionRow = z.infer<typeof alphaAttributionRowSchema>;
export type HeatmapCell = z.infer<typeof heatmapCellSchema>;
export type ExpectancyRow = z.infer<typeof expectancyRowSchema>;
export type Reports = z.infer<typeof reportsSchema>;

// ── Risk parameters ──────────────────────────────────────────────────────────

export const riskParametersSchema = z.object({
  maxDailyLoss: z.number(),
  maxPositionSize: z.number(),
  maxOpenPositions: z.number(),
  defaultStopLossPct: z.number(),
  defaultTargetPct: z.number(),
  maxMarginUtilizationPct: z.number(),

  // Intraday loss & drawdown
  maxTrailingDrawdownPct: z.number(),
  consecutiveLossLimit: z.number(),
  consecutiveLossBreakerEnabled: z.boolean(),

  // Order execution & slippage
  maxSlippagePct: z.number(),
  orderRateLimitPerSec: z.number(),
  fatFingerMaxLots: z.number(),
  fatFingerMaxShares: z.number(),

  // Margin & overnight exposure
  autoSquareOffTime: z.string(),
  overnightOptionsFreeze: z.boolean(),

  // Alerts, webhooks & notifications
  smsWebhookAlertsEnabled: z.boolean(),
  auditoryTelemetryEnabled: z.boolean(),
  customWebhookUri: z.string().nullable().optional(),

  // Live-trading master switch. false = paper (default, simulated).
  liveTradingEnabled: z.boolean(),
  // Auto-trading engine switch. false (default) = engine idle.
  autoTradingEnabled: z.boolean(),

  killSwitchArmed: z.boolean(),
});

export type RiskParameters = z.infer<typeof riskParametersSchema>;

/** Partial update — every field optional. */
export type RiskParametersUpdate = Partial<RiskParameters>;
