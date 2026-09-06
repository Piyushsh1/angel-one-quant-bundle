'use client';

import { useState, type ReactNode } from 'react';
import {
  QueryClient,
  QueryClientProvider,
  type QueryClientConfig,
} from '@tanstack/react-query';
import { ApiError } from '@/lib/errors';

/**
 * React Query defaults, chosen for a trading UI.
 *
 * The key decision is that 4xx responses are never retried. Retrying a
 * client error is pointless, and for auth endpoints it actively harms the user
 * by burning through rate limits and lockout counters.
 */
const config: QueryClientConfig = {
  defaultOptions: {
    queries: {
      // Market and position data goes stale almost immediately; anything
      // longer would show a price that is no longer true.
      staleTime: 5_000,
      gcTime: 5 * 60_000,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && !error.isRetryable) return false;
        return failureCount < 2;
      },
      retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
      refetchOnWindowFocus: true,
      // Never silently retry a mutation-like refetch while offline.
      networkMode: 'online',
    },
    mutations: {
      // Mutations here place orders or change risk settings. Automatic retries
      // could duplicate an action, so callers opt in explicitly.
      retry: false,
      networkMode: 'online',
    },
  },
};

export function QueryProvider({ children }: { children: ReactNode }) {
  // One client per browser session, created lazily in state so it is not
  // shared across requests during server rendering.
  const [queryClient] = useState(() => new QueryClient(config));

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
