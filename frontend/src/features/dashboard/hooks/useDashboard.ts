'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as dashApi from '@/features/dashboard/api/dashboard.api';
import { dashboardKeys } from '@/features/dashboard/hooks/dashboardKeys';
import { useDashboardDate } from '@/features/dashboard/hooks/useDashboardDate';
import type {
  RiskParametersUpdate,
  StrategyStatus,
} from '@/features/dashboard/schemas/dashboard.schema';

/**
 * Terminal data hooks.
 *
 * Reads are `useQuery`; market and position data goes stale fast, so these
 * refetch on an interval to keep the tape moving. Mutations invalidate the
 * queries they affect so every panel re-derives from the server rather than
 * optimistically guessing (an order or square-off must reflect real broker
 * state).
 */

// Refresh cadences chosen per data volatility.
const FAST_MS = 5_000; // positions, P&L, metrics, watchlist, indices
const SLOW_MS = 15_000; // strategies, orders

export function useMetrics() {
  const { queryDate, isToday } = useDashboardDate();
  return useQuery({
    queryKey: dashboardKeys.metrics(queryDate),
    queryFn: ({ signal }) => dashApi.getMetrics(signal, queryDate),
    // Live data polls; historical is static so no interval.
    refetchInterval: isToday ? FAST_MS : false,
  });
}

export function useIntradayPnl() {
  const { queryDate, isToday } = useDashboardDate();
  return useQuery({
    queryKey: dashboardKeys.pnl(queryDate),
    queryFn: ({ signal }) => dashApi.getIntradayPnl(signal, queryDate),
    refetchInterval: isToday ? FAST_MS : false,
  });
}

export function usePositions() {
  const { queryDate, isToday } = useDashboardDate();
  return useQuery({
    queryKey: dashboardKeys.positions(queryDate),
    queryFn: ({ signal }) => dashApi.getPositions(signal, queryDate),
    refetchInterval: isToday ? FAST_MS : false,
  });
}

export function useStrategies() {
  return useQuery({
    queryKey: dashboardKeys.strategies(),
    queryFn: ({ signal }) => dashApi.getStrategies(signal),
    refetchInterval: SLOW_MS,
  });
}

export function useOrders() {
  const { queryDate, isToday } = useDashboardDate();
  return useQuery({
    queryKey: dashboardKeys.orders(queryDate),
    queryFn: ({ signal }) => dashApi.getOrders(signal, queryDate),
    refetchInterval: isToday ? SLOW_MS : false,
  });
}

export function useIndices() {
  return useQuery({
    queryKey: dashboardKeys.indices(),
    queryFn: ({ signal }) => dashApi.getIndices(signal),
    refetchInterval: FAST_MS,
  });
}

// ── Mutations ────────────────────────────────────────────────────────────────

export function useClosePosition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (positionId: string) => dashApi.closePosition(positionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: dashboardKeys.positions() });
      qc.invalidateQueries({ queryKey: dashboardKeys.metrics() });
      qc.invalidateQueries({ queryKey: dashboardKeys.pnl() });
    },
  });
}

export function useSetStrategyStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: StrategyStatus }) =>
      dashApi.setStrategyStatus(id, status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: dashboardKeys.strategies() });
      qc.invalidateQueries({ queryKey: dashboardKeys.metrics() });
    },
  });
}

export function usePanicSquareOff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => dashApi.panicSquareOff(),
    onSuccess: () => {
      // A kill-switch touches everything: refresh the whole terminal.
      qc.invalidateQueries({ queryKey: dashboardKeys.all });
    },
  });
}

// ── Reports ──────────────────────────────────────────────────────────────────

export function useReports() {
  const { queryDate, isToday } = useDashboardDate();
  return useQuery({
    queryKey: dashboardKeys.reports(queryDate),
    queryFn: ({ signal }) => dashApi.getReports(signal, queryDate),
    refetchInterval: isToday ? SLOW_MS : false,
  });
}

// ── Risk parameters ──────────────────────────────────────────────────────────

export function useRiskParameters() {
  return useQuery({
    queryKey: dashboardKeys.risk(),
    queryFn: ({ signal }) => dashApi.getRiskParameters(signal),
    // Config, not market data — no polling needed.
    staleTime: 60_000,
  });
}

export function useUpdateRiskParameters() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (changes: RiskParametersUpdate) =>
      dashApi.updateRiskParameters(changes),
    onSuccess: (data) => {
      // Seed the cache with the server's authoritative values.
      qc.setQueryData(dashboardKeys.risk(), data);
    },
  });
}

// ── Broker accounts ──────────────────────────────────────────────────────────

export function useBrokerAccounts() {
  return useQuery({
    queryKey: dashboardKeys.brokerAccounts(),
    queryFn: ({ signal }) => dashApi.getBrokerAccounts(signal),
    staleTime: 30_000,
  });
}

export function useDisconnectBroker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (accountId: string) => dashApi.disconnectBroker(accountId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: dashboardKeys.brokerAccounts() });
    },
  });
}
