'use client';

import Link from 'next/link';
import { Panel, PanelHeader } from '@/features/dashboard/components/Panel';
import {
  usePositions,
  useClosePosition,
} from '@/features/dashboard/hooks/useDashboard';
import type { Position } from '@/features/dashboard/schemas/dashboard.schema';
import {
  formatInr,
  formatSignedInr,
  formatSignedPct,
  pnlColor,
} from '@/features/dashboard/lib/format';
import { cn } from '@/lib/cn';

function PositionRow({
  position,
  onExit,
  exiting,
}: {
  position: Position;
  onExit: (id: string) => void;
  exiting: boolean;
}) {
  const isBuy = position.side === 'BUY';
  const color = pnlColor(position.pnl);

  return (
    <div className="rounded-lg border border-surface-700/70 bg-surface-900 p-2.5 transition hover:border-surface-600">
      <div className="mb-1.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'rounded border px-1.5 py-0.5 font-mono text-[10px] font-bold',
              isBuy
                ? 'border-profit/30 bg-profit/10 text-profit'
                : 'border-loss/30 bg-loss/10 text-loss',
            )}
          >
            {position.side}
          </span>
          <span className="text-xs font-bold tracking-wide text-white">
            {position.symbol}
          </span>
          <span className="font-mono text-[10px] text-slate-400">
            Qty: {position.qty}
          </span>
        </div>
        <div className="text-right">
          <div className={cn('font-mono text-xs font-bold', color)}>
            {formatSignedInr(position.pnl)}
          </div>
          <span className={cn('font-mono text-[10px] opacity-80', color)}>
            {formatSignedPct(position.pnlPct)}
          </span>
        </div>
      </div>
      <div className="flex items-center justify-between border-t border-surface-700/50 pt-1.5 font-mono text-[11px] text-slate-400">
        <span>Avg: {formatInr(position.avgPrice)}</span>
        <span>
          LTP:{' '}
          <strong className="font-mono text-white">
            {formatInr(position.ltp)}
          </strong>
        </span>
        <button
          type="button"
          onClick={() => onExit(position.id)}
          disabled={exiting}
          className="text-[10px] text-loss hover:text-loss-strong hover:underline disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-loss"
        >
          {exiting ? 'Exiting…' : 'Exit'}
        </button>
      </div>
    </div>
  );
}

/** The active open positions list with an aggregate P&L footer. */
export function OpenPositionsPanel({
  className = 'flex flex-col justify-between lg:col-span-5',
}: { className?: string } = {}) {
  const { data, isLoading, isError } = usePositions();
  const closeMutation = useClosePosition();

  const items = data?.items ?? [];
  const total = data?.aggregateUnrealized ?? 0;

  return (
    <Panel className={className}>
      <div>
        <PanelHeader
          title="Active Open Positions"
          iconClassName="border-brand-500/20 bg-brand-500/10 text-brand-400"
          icon={
            <svg
              aria-hidden="true"
              className="h-3.5 w-3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              viewBox="0 0 24 24"
            >
              <path
                d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          }
          badge={
            <span className="rounded bg-surface-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
              {items.length}
            </span>
          }
          action={
            <Link
              href="/dashboard/positions"
              className="flex items-center gap-1 font-mono text-[11px] text-brand-400 hover:text-brand-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
            >
              View All
              <svg
                aria-hidden="true"
                className="h-3 w-3"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                viewBox="0 0 24 24"
              >
                <path d="M9 5l7 7-7 7" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </Link>
          }
        />

        <div className="space-y-2">
          {isLoading && <PositionsSkeleton />}
          {isError && !isLoading && (
            <p className="rounded border border-loss/30 bg-loss/5 p-3 text-[11px] text-loss">
              Failed to load positions.
            </p>
          )}
          {!isLoading && !isError && items.length === 0 && (
            <p className="rounded border border-surface-700/50 bg-surface-900 p-4 text-center text-[11px] text-slate-400">
              No open positions.
            </p>
          )}
          {items.map((position) => (
            <PositionRow
              key={position.id}
              position={position}
              onExit={(id) => closeMutation.mutate(id)}
              exiting={
                closeMutation.isPending &&
                closeMutation.variables === position.id
              }
            />
          ))}
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between border-t border-surface-700/60 pt-2.5 font-mono text-[11px] text-slate-400">
        <span>Aggregated Unrealized:</span>
        <span className={cn('font-bold', pnlColor(total))}>
          {formatSignedInr(total)}
        </span>
      </div>
    </Panel>
  );
}

function PositionsSkeleton() {
  return (
    <>
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="h-[68px] animate-pulse rounded-lg border border-surface-700/70 bg-surface-900"
        />
      ))}
    </>
  );
}
