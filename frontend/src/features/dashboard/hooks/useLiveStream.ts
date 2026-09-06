'use client';

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { env } from '@/lib/env';
import { dashboardKeys } from '@/features/dashboard/hooks/dashboardKeys';
import { useDashboardDate } from '@/features/dashboard/hooks/useDashboardDate';
import {
  metricsSchema,
  positionsResponseSchema,
} from '@/features/dashboard/schemas/dashboard.schema';
import { z } from 'zod';

const streamPayloadSchema = z.object({
  metrics: metricsSchema,
  positions: positionsResponseSchema,
});

/**
 * Live terminal stream (Server-Sent Events).
 *
 * Opens one EventSource to /dashboard/stream and feeds each snapshot straight
 * into the React Query cache for metrics + today's positions. That's what makes
 * an auto-engine trade appear on the dashboard almost the instant it happens —
 * the panels read the same cache, so they update with no extra polling.
 *
 * Only runs while viewing TODAY (a historical date is static, nothing to
 * stream). The browser's EventSource auto-reconnects on a dropped connection.
 * Session auth rides the cookie automatically (withCredentials).
 */
export function useLiveStream() {
  const queryClient = useQueryClient();
  const { isToday } = useDashboardDate();

  useEffect(() => {
    if (!isToday) return;
    if (typeof window === 'undefined') return;

    const url = `${env.NEXT_PUBLIC_API_BASE_URL}/dashboard/stream`;
    const es = new EventSource(url, { withCredentials: true });

    es.onmessage = (event) => {
      let raw: unknown;
      try {
        raw = JSON.parse(event.data);
      } catch {
        return;
      }
      const parsed = streamPayloadSchema.safeParse(raw);
      if (!parsed.success) return;

      // Seed the cache for "today" so every panel reflects the push instantly.
      queryClient.setQueryData(dashboardKeys.metrics(undefined), parsed.data.metrics);
      queryClient.setQueryData(
        dashboardKeys.positions(undefined),
        parsed.data.positions,
      );
    };

    // On error the browser reconnects automatically; nothing to do but let it.
    return () => es.close();
  }, [isToday, queryClient]);
}
