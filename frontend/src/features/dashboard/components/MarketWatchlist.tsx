'use client';

import { Panel } from '@/features/dashboard/components/Panel';
import { useIndices } from '@/features/dashboard/hooks/useDashboard';
import { formatSignedPct } from '@/features/dashboard/lib/format';
import { cn } from '@/lib/cn';

/**
 * Live market watch.
 *
 * Shows real broker quotes for the tracked indices (NIFTY / BANKNIFTY /
 * SENSEX). Only fields the broker actually returns are shown — no fabricated
 * volume/high/low columns. When no broker feed is available the panel says so
 * rather than showing stale rows.
 */
export function MarketWatchlist() {
  const { data, isLoading, isError } = useIndices();
  const rows = data ?? [];

  return (
    <Panel>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 border-b border-surface-700 pb-3">
        <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
          <svg
            aria-hidden="true"
            className="h-4 w-4 text-brand-400"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            viewBox="0 0 24 24"
          >
            <path
              d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Live Market Watch
        </h2>
        <span className="font-mono text-[11px] text-slate-500">
          Live • auto-refresh 5s
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left font-mono text-xs">
          <thead>
            <tr className="border-b border-surface-700/60 text-[11px] text-slate-500">
              <th scope="col" className="pb-2 font-medium">
                Index
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                LTP (₹)
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Change %
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-700/40">
            {rows.map((row) => {
              const up = row.changePct >= 0;
              const color = up ? 'text-profit' : 'text-loss';
              return (
                <tr key={row.label} className="transition hover:bg-surface-800/20">
                  <th
                    scope="row"
                    className="flex items-center gap-2 py-2 text-left font-sans font-bold text-white"
                  >
                    <span
                      className={cn(
                        'h-1.5 w-1.5 rounded-full',
                        up ? 'bg-profit' : 'bg-loss',
                      )}
                    />
                    {row.label}
                  </th>
                  <td className="py-2 text-right font-bold text-white">
                    {row.value}
                  </td>
                  <td className={cn('py-2 text-right font-semibold', color)}>
                    {formatSignedPct(row.changePct)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {isLoading && (
          <p className="py-4 text-center text-[11px] text-slate-400">
            Loading market data…
          </p>
        )}
        {isError && !isLoading && (
          <p className="py-4 text-center text-[11px] text-loss">
            Failed to load market data.
          </p>
        )}
        {!isLoading && !isError && rows.length === 0 && (
          <p className="py-4 text-center text-[11px] text-slate-400">
            Connect your broker to see live market quotes.
          </p>
        )}
      </div>
    </Panel>
  );
}
