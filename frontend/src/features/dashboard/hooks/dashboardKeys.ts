/**
 * Query-key factory for the trading terminal.
 *
 * Centralised so a mutation can invalidate exactly the queries it affects
 * without magic-string drift. Keys are `as const` so TypeScript catches typos.
 */
/**
 * Date-scoped keys carry the selected trading date so switching dates fetches
 * (and caches) a distinct result rather than showing stale data. `undefined`
 * means "today" (the default) and omits the date segment.
 */
export const dashboardKeys = {
  all: ['dashboard'] as const,
  metrics: (date?: string) => [...dashboardKeys.all, 'metrics', date ?? 'today'] as const,
  pnl: (date?: string) => [...dashboardKeys.all, 'pnl', date ?? 'today'] as const,
  positions: (date?: string) =>
    [...dashboardKeys.all, 'positions', date ?? 'today'] as const,
  strategies: () => [...dashboardKeys.all, 'strategies'] as const,
  orders: (date?: string) => [...dashboardKeys.all, 'orders', date ?? 'today'] as const,
  indices: () => [...dashboardKeys.all, 'indices'] as const,
  reports: (date?: string) => [...dashboardKeys.all, 'reports', date ?? 'today'] as const,
  risk: () => [...dashboardKeys.all, 'risk'] as const,
  brokerAccounts: () => [...dashboardKeys.all, 'brokerAccounts'] as const,
} as const;
