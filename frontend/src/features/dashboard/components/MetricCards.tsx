'use client';

import { useState } from 'react';
import { cn } from '@/lib/cn';
import { useMetrics } from '@/features/dashboard/hooks/useDashboard';
import { api } from '@/lib/api-client';
import { z } from 'zod';
import {
  formatInr,
  formatSignedInr,
  formatSignedPct,
} from '@/features/dashboard/lib/format';

const brokerStatusSchema = z.object({ connected: z.boolean() });

/** Measures a real round-trip to the broker-status endpoint. */
function useBrokerPing() {
  const [result, setResult] = useState<string | null>(null);
  const [pinging, setPinging] = useState(false);
  async function ping() {
    setPinging(true);
    const start = performance.now();
    try {
      await api.get('/broker/status', { schema: brokerStatusSchema });
      setResult(`${Math.round(performance.now() - start)}ms`);
    } catch {
      setResult('failed');
    } finally {
      setPinging(false);
    }
  }
  return { result, pinging, ping };
}

/**
 * The executive summary strip: five at-a-glance cards, driven by live metrics.
 * The first (Total P&L) is visually promoted with a profit/loss-tinted border
 * because it's the number a trader looks at first.
 */
export function MetricCards() {
  const { data, isLoading, isError } = useMetrics();
  const { result: pingResult, pinging, ping } = useBrokerPing();

  if (isLoading || !data) {
    return <MetricCardsSkeleton error={isError} />;
  }

  const pnlPositive = data.totalPnl >= 0;

  return (
    <section className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-5">
      {/* Card 1: Total P&L (promoted) */}
      <article
        className={cn(
          'relative overflow-hidden rounded-xl border bg-surface-850 p-3.5',
          pnlPositive
            ? 'border-profit/30 shadow-[0_0_16px_-2px_rgba(16,185,129,0.18)]'
            : 'border-loss/30 shadow-[0_0_16px_-2px_rgba(239,68,68,0.18)]',
        )}
      >
        <span
          className={cn(
            'pointer-events-none absolute -bottom-6 -right-6 h-24 w-24 rounded-full blur-xl',
            pnlPositive ? 'bg-profit/10' : 'bg-loss/10',
          )}
        />
        <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
          <span className="font-medium">Total P&amp;L (Today)</span>
          <span
            className={cn(
              'rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold',
              pnlPositive ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss',
            )}
          >
            {formatSignedPct(data.totalPnlPct)}
          </span>
        </div>
        <div className="my-0.5 flex items-baseline gap-1.5">
          <span
            className={cn(
              'font-mono text-xl font-bold tracking-tight',
              pnlPositive ? 'text-profit' : 'text-loss',
            )}
          >
            {formatSignedInr(data.totalPnl)}
          </span>
        </div>
        <div className="mt-2 flex items-center justify-between border-t border-surface-700/50 pt-2 font-mono text-[11px] text-slate-400">
          <span>Gross: {formatInr(data.grossPnl)}</span>
          <span className="text-slate-500">Charges: {formatInr(data.charges)}</span>
        </div>
      </article>

      {/* Card 2: Open Positions */}
      <MetricCard
        label="Open Positions"
        badge={{ text: `${data.openPositionsCount} Active`, tone: 'brand' }}
        value={formatInr(data.netExposure)}
        footerLeft="Net Exposure"
        footerRight={
          <span className="text-profit">
            {data.longCount} Long / {data.shortCount} Short
          </span>
        }
      />

      {/* Card 3: Executed Trades */}
      <article className="rounded-xl border border-surface-700/70 bg-surface-850 p-3.5 transition hover:border-surface-600">
        <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
          <span className="font-medium">Executed Trades</span>
          <span className="rounded bg-surface-800 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-slate-300">
            {data.executedTrades} Orders
          </span>
        </div>
        <div className="my-0.5 flex items-center gap-2">
          <span className="font-mono text-xl font-bold text-white">
            {data.winRate.toFixed(1)}%
          </span>
          <span className="font-mono text-xs font-medium text-profit">
            ({data.wins} W / {data.losses} L)
          </span>
        </div>
        <WinLossBar wins={data.wins} losses={data.losses} />
      </article>

      {/* Card 4: Available Margin */}
      <MetricCard
        label="Available Margin"
        badge={
          data.gatewayConnected
            ? { text: 'Live', tone: 'profit' }
            : { text: 'No broker', tone: 'brand' }
        }
        value={formatInr(data.availableMargin)}
        footerLeft="Total Capital"
        footerRight={
          <span className="text-slate-300">{formatInr(data.totalCapital)}</span>
        }
      />

      {/* Card 5: Execution Gateway */}
      <article className="rounded-xl border border-surface-700/70 bg-surface-850 p-3.5 transition hover:border-surface-600">
        <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
          <span className="font-medium">Execution Gateway</span>
          <span
            className={cn(
              'h-2 w-2 rounded-full',
              data.gatewayConnected ? 'animate-pulse bg-profit' : 'bg-loss',
            )}
          />
        </div>
        <div className="my-0.5 flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded border border-brand-500/30 bg-brand-500/10 text-[10px] font-bold text-brand-400">
            {data.gatewayName.charAt(0)}
          </span>
          <div>
            <div className="text-xs font-semibold text-white">
              {data.gatewayName}
            </div>
            <div
              className={cn(
                'font-mono text-[10px]',
                data.gatewayConnected ? 'text-profit' : 'text-loss',
              )}
            >
              {data.gatewayConnected ? 'Connected' : 'Disconnected'}
            </div>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between border-t border-surface-700/50 pt-2 font-mono text-[11px] text-slate-400">
          <span
            className={cn(
              'font-semibold',
              !data.gatewayConnected
                ? 'text-slate-400'
                : data.liveTradingEnabled
                  ? 'text-loss'
                  : 'text-paper',
            )}
          >
            Mode: {data.gatewayMode}
          </span>
          <button
            type="button"
            onClick={ping}
            disabled={pinging}
            className="text-[10px] text-brand-400 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 disabled:opacity-50"
          >
            {pinging ? 'Pinging…' : pingResult ? `Ping: ${pingResult}` : 'Test Ping'}
          </button>
        </div>
      </article>
    </section>
  );
}

/** Win/loss ratio bar; guards against a divide-by-zero when no trades exist. */
function WinLossBar({ wins, losses }: { wins: number; losses: number }) {
  const total = wins + losses;
  const winPct = total ? (wins / total) * 100 : 0;
  return (
    <div className="mt-2.5 flex h-1.5 w-full overflow-hidden rounded-full bg-surface-800">
      <span
        className="h-full bg-profit-strong"
        style={{ width: `${winPct}%` }}
        title={`${wins} Wins`}
      />
      <span
        className="h-full bg-loss-strong"
        style={{ width: `${100 - winPct}%` }}
        title={`${losses} Losses`}
      />
    </div>
  );
}

const BADGE_TONE = {
  brand: 'bg-surface-800 text-brand-400',
  profit: 'bg-profit/10 text-profit',
} as const;

/** A standard (non-promoted) metric card with a headline value and a footer. */
function MetricCard({
  label,
  badge,
  value,
  footerLeft,
  footerRight,
}: {
  label: string;
  badge: { text: string; tone: keyof typeof BADGE_TONE };
  value: string;
  footerLeft: string;
  footerRight: React.ReactNode;
}) {
  return (
    <article className="rounded-xl border border-surface-700/70 bg-surface-850 p-3.5 transition hover:border-surface-600">
      <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
        <span className="font-medium">{label}</span>
        <span
          className={cn(
            'rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold',
            BADGE_TONE[badge.tone],
          )}
        >
          {badge.text}
        </span>
      </div>
      <div className="my-0.5 flex items-baseline gap-1.5">
        <span className="font-mono text-xl font-bold text-white">{value}</span>
      </div>
      <div className="mt-2 flex items-center justify-between border-t border-surface-700/50 pt-2 font-mono text-[11px] text-slate-400">
        <span>{footerLeft}</span>
        {footerRight}
      </div>
    </article>
  );
}

/** Placeholder strip shown while metrics load or if the request fails. */
function MetricCardsSkeleton({ error }: { error?: boolean }) {
  return (
    <section className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-5">
      {Array.from({ length: 5 }).map((_, i) => (
        <div
          key={i}
          className="h-[104px] animate-pulse rounded-xl border border-surface-700/70 bg-surface-850"
        >
          {error && i === 0 && (
            <p className="p-3.5 text-[11px] text-loss">Failed to load metrics</p>
          )}
        </div>
      ))}
    </section>
  );
}
