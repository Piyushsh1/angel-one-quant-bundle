'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useCurrentUser } from '@/features/auth/hooks/useCurrentUser';
import { useMetrics } from '@/features/dashboard/hooks/useDashboard';
import {
  useDashboardDate,
  todayIst,
} from '@/features/dashboard/hooks/useDashboardDate';
import { dashboardKeys } from '@/features/dashboard/hooks/dashboardKeys';
import { cn } from '@/lib/cn';

/**
 * The greeting row and quick controls at the top of the dashboard body.
 *
 * The greeting resolves the signed-in user's first name; the status line and
 * engine pill reflect live metrics. "Sync Feeds" invalidates every terminal
 * query so a trader can force an immediate refresh.
 */
export function HeroSection() {
  const { data: user } = useCurrentUser();
  const { data: metrics } = useMetrics();
  const queryClient = useQueryClient();
  const { date, isToday, setDate, resetToToday } = useDashboardDate();

  const firstName = user?.fullName?.split(' ')[0] ?? 'Trader';
  const active = metrics?.activeStrategies ?? 0;
  const online = metrics?.engineOnline ?? false;
  const maxDate = todayIst();

  function syncFeeds() {
    queryClient.invalidateQueries({ queryKey: dashboardKeys.all });
  }

  return (
    <section className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-bold tracking-tight text-white">
            Welcome back, {firstName}
          </h1>
          <span aria-hidden="true" className="animate-bounce text-lg">
            ⚡
          </span>
        </div>
        <p className="mt-0.5 text-xs text-slate-400">
          {!isToday ? (
            <>
              Viewing history for {date} •{' '}
              <button
                type="button"
                onClick={resetToToday}
                className="text-brand-400 hover:underline focus-visible:outline-none"
              >
                back to today
              </button>
            </>
          ) : online ? (
            <>
              Intraday algorithmic execution running normal • {active} active{' '}
              {active === 1 ? 'strategy' : 'strategies'} evaluating signals
            </>
          ) : (
            <>
              Algo engine idle • {active} active{' '}
              {active === 1 ? 'strategy' : 'strategies'} evaluating signals
            </>
          )}
        </p>
      </div>

      <div className="flex items-center gap-2.5">
        {/* Date filter — drives the whole terminal. Defaults to today; past
            dates load recorded history. Cannot select the future. */}
        <label className="flex items-center gap-1.5 rounded-lg border border-surface-700 bg-surface-850 px-3 py-1.5 font-mono text-xs text-slate-300 focus-within:ring-2 focus-within:ring-brand-400">
          <svg
            aria-hidden="true"
            className="h-3.5 w-3.5 text-slate-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
            />
          </svg>
          <span className="sr-only">Trading date</span>
          <input
            type="date"
            value={date}
            max={maxDate}
            onChange={(e) => setDate(e.target.value || maxDate)}
            className="bg-transparent text-slate-200 outline-none [color-scheme:dark]"
          />
        </label>
        <button
          type="button"
          onClick={syncFeeds}
          className="flex items-center gap-1.5 rounded-lg border border-surface-700 bg-surface-850 px-3 py-1.5 font-mono text-xs text-slate-300 transition hover:bg-surface-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
        >
          <svg
            aria-hidden="true"
            className="h-3.5 w-3.5 text-slate-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
            />
          </svg>
          Sync Feeds
        </button>
        <div
          className={cn(
            'flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold',
            online
              ? 'border-profit/30 bg-profit/10 text-profit'
              : 'border-surface-700 bg-surface-850 text-slate-400',
          )}
        >
          <span
            className={cn(
              'h-2 w-2 rounded-full',
              online ? 'animate-pulse bg-profit' : 'bg-slate-500',
            )}
          />
          Algo Engine: {online ? 'ONLINE' : 'IDLE'}
        </div>
      </div>
    </section>
  );
}
