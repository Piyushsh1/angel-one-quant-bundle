import { z } from 'zod';
import { api } from '@/lib/api-client';
import { env } from '@/lib/env';
import {
  intradayPnlSchema,
  indicesResponseSchema,
  metricsSchema,
  ordersResponseSchema,
  panicResponseSchema,
  positionSchema,
  positionsResponseSchema,
  strategiesResponseSchema,
  strategySchema,
  brokerAccountsResponseSchema,
  reportsSchema,
  riskParametersSchema,
  type BrokerAccount,
  type IntradayPnl,
  type IndexQuote,
  type Metrics,
  type Order,
  type PanicResponse,
  type Position,
  type PositionsResponse,
  type Reports,
  type RiskParameters,
  type RiskParametersUpdate,
  type Strategy,
  type StrategyStatus,
} from '@/features/dashboard/schemas/dashboard.schema';

/**
 * Dashboard data layer.
 *
 * Pure functions with no React coupling — React Query wiring lives in the hooks
 * layer. Every request rides the shared api-client, so session cookies and the
 * CSRF double-submit header are handled centrally.
 */

const ENDPOINTS = {
  metrics: '/dashboard/metrics',
  pnlIntraday: '/dashboard/pnl/intraday',
  positions: '/dashboard/positions',
  strategies: '/dashboard/strategies',
  orders: '/dashboard/orders',
  indices: '/dashboard/market/indices',
  reports: '/dashboard/reports',
  risk: '/dashboard/risk',
  brokerAccounts: '/broker/accounts',
  brokerConnect: '/broker/connect',
} as const;

/** Append `?date=YYYY-MM-DD` when a non-today date is selected. */
function withDate(path: string, date?: string): string {
  return date ? `${path}?date=${encodeURIComponent(date)}` : path;
}

export async function getMetrics(
  signal?: AbortSignal,
  date?: string,
): Promise<Metrics> {
  return api.get(withDate(ENDPOINTS.metrics, date), {
    schema: metricsSchema,
    signal,
  });
}

export async function getIntradayPnl(
  signal?: AbortSignal,
  date?: string,
): Promise<IntradayPnl> {
  return api.get(withDate(ENDPOINTS.pnlIntraday, date), {
    schema: intradayPnlSchema,
    signal,
  });
}

export async function getPositions(
  signal?: AbortSignal,
  date?: string,
): Promise<PositionsResponse> {
  return api.get(withDate(ENDPOINTS.positions, date), {
    schema: positionsResponseSchema,
    signal,
  });
}

export async function getStrategies(signal?: AbortSignal): Promise<Strategy[]> {
  return api.get(ENDPOINTS.strategies, { schema: strategiesResponseSchema, signal });
}

export async function getOrders(
  signal?: AbortSignal,
  date?: string,
): Promise<Order[]> {
  return api.get(withDate(ENDPOINTS.orders, date), {
    schema: ordersResponseSchema,
    signal,
  });
}

export async function getIndices(signal?: AbortSignal): Promise<IndexQuote[]> {
  return api.get(ENDPOINTS.indices, { schema: indicesResponseSchema, signal });
}

// ── Mutations ────────────────────────────────────────────────────────────────

export async function closePosition(positionId: string): Promise<Position> {
  return api.post(`${ENDPOINTS.positions}/${encodeURIComponent(positionId)}/close`, {
    schema: positionSchema,
  });
}

export async function panicSquareOff(): Promise<PanicResponse> {
  return api.post(`${ENDPOINTS.positions}/panic`, { schema: panicResponseSchema });
}

export async function setStrategyStatus(
  strategyId: string,
  status: StrategyStatus,
): Promise<Strategy> {
  return api.patch(
    `${ENDPOINTS.strategies}/${encodeURIComponent(strategyId)}/status`,
    { body: { status }, schema: strategySchema },
  );
}

// ── Reports ──────────────────────────────────────────────────────────────────

export async function getReports(
  signal?: AbortSignal,
  date?: string,
): Promise<Reports> {
  return api.get(withDate(ENDPOINTS.reports, date), {
    schema: reportsSchema,
    signal,
  });
}

/**
 * Download the current reports payload as a CSV file. The api-client only
 * speaks JSON, so this hits the export endpoint directly (still with session
 * cookies) and triggers a browser download from the returned blob.
 */
export async function downloadReportsCsv(date?: string): Promise<void> {
  const path = withDate(`${ENDPOINTS.reports}/export.csv`, date);
  const response = await fetch(`${env.NEXT_PUBLIC_API_BASE_URL}${path}`, {
    credentials: 'include',
    headers: { Accept: 'text/csv' },
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`Export failed (${response.status})`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match?.[1] ?? 'quant-report.csv';

  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(href);
}

// ── Risk parameters ──────────────────────────────────────────────────────────

export async function getRiskParameters(
  signal?: AbortSignal,
): Promise<RiskParameters> {
  return api.get(ENDPOINTS.risk, { schema: riskParametersSchema, signal });
}

export async function updateRiskParameters(
  changes: RiskParametersUpdate,
): Promise<RiskParameters> {
  return api.patch(ENDPOINTS.risk, { body: changes, schema: riskParametersSchema });
}

// ── Broker accounts ──────────────────────────────────────────────────────────

export async function getBrokerAccounts(
  signal?: AbortSignal,
): Promise<BrokerAccount[]> {
  return api.get(ENDPOINTS.brokerAccounts, {
    schema: brokerAccountsResponseSchema,
    signal,
  });
}

export async function disconnectBroker(accountId: string): Promise<void> {
  await api.delete(`${ENDPOINTS.brokerAccounts}/${encodeURIComponent(accountId)}`, {
    schema: z.unknown(),
  });
}
