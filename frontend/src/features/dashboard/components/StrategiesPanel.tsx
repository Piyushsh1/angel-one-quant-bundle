'use client';

import Link from 'next/link';
import { Panel, PanelHeader } from '@/features/dashboard/components/Panel';
import {
  useStrategies,
  useSetStrategyStatus,
} from '@/features/dashboard/hooks/useDashboard';
import type { Strategy } from '@/features/dashboard/schemas/dashboard.schema';
import { formatInr, formatSignedInr, pnlColor } from '@/features/dashboard/lib/format';
import { cn } from '@/lib/cn';

function StatusPill({ status }: { status: Strategy['status'] }) {
  if (status === 'running') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-profit/20 bg-profit/10 px-2 py-0.5 text-[11px] text-profit">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-profit" />
        Running
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-surface-700 bg-surface-800 px-2 py-0.5 text-[11px] text-slate-400">
      <span className="h-1.5 w-1.5 rounded-full bg-slate-500" />
      Stopped
    </span>
  );
}

function StrategyRow({
  strategy,
  onToggle,
  pending,
}: {
  strategy: Strategy;
  onToggle: (s: Strategy) => void;
  pending: boolean;
}) {
  const stopped = strategy.status === 'stopped';
  return (
    <tr className="transition hover:bg-surface-800/30">
      <td className="py-2.5">
        <div className="font-sans font-semibold text-slate-200">
          {strategy.name}
        </div>
        <span className="text-[10px] text-slate-500">{strategy.descriptor}</span>
      </td>
      <td className="py-2.5">
        <StatusPill status={strategy.status} />
      </td>
      <td
        className={cn(
          'py-2.5 text-center',
          stopped ? 'text-slate-500' : 'text-slate-300',
        )}
      >
        {strategy.tradesToday}
      </td>
      <td
        className={cn(
          'py-2.5 text-right font-bold',
          strategy.pnl === 0 ? 'text-slate-400' : pnlColor(strategy.pnl),
        )}
      >
        {strategy.pnl === 0 ? formatInr(0) : formatSignedInr(strategy.pnl)}
      </td>
      <td className="py-2.5 text-right">
        {stopped ? (
          <button
            type="button"
            onClick={() => onToggle(strategy)}
            disabled={pending}
            className="rounded border border-profit/30 bg-profit/10 px-2 py-1 text-[10px] text-profit hover:bg-profit/20 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-profit"
          >
            {pending ? '…' : 'Start'}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onToggle(strategy)}
            disabled={pending}
            className="rounded border border-loss/30 bg-loss/10 px-2 py-1 text-[10px] text-loss hover:bg-loss/20 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-loss"
          >
            {pending ? '…' : 'Stop'}
          </button>
        )}
      </td>
    </tr>
  );
}

/** Algorithmic strategy execution table. */
export function StrategiesPanel({ className = 'lg:col-span-7' }: { className?: string } = {}) {
  const { data, isLoading, isError } = useStrategies();
  const statusMutation = useSetStrategyStatus();

  const strategies = data ?? [];
  const running = strategies.filter((s) => s.status === 'running').length;
  const stopped = strategies.length - running;

  function toggle(s: Strategy) {
    statusMutation.mutate({
      id: s.id,
      status: s.status === 'running' ? 'stopped' : 'running',
    });
  }

  return (
    <Panel className={className}>
      <PanelHeader
        title="Algorithmic Strategy Execution"
        iconClassName="border-purple-500/20 bg-purple-500/10 text-purple-400"
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
              d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        }
        badge={
          <span className="rounded border border-profit/30 bg-profit/10 px-1.5 py-0.5 font-mono text-[10px] text-profit">
            {running} Running / {stopped} Paused
          </span>
        }
        action={
          <Link
            href="/dashboard/strategies"
            className="flex items-center gap-1 font-mono text-[11px] text-brand-400 hover:text-brand-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
          >
            Configure Matrix
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

      <div className="overflow-x-auto">
        <table className="w-full text-left font-mono text-xs">
          <thead>
            <tr className="border-b border-surface-700 text-[11px] text-slate-500">
              <th scope="col" className="pb-2 font-medium">
                Strategy Name
              </th>
              <th scope="col" className="pb-2 font-medium">
                Engine Status
              </th>
              <th scope="col" className="pb-2 text-center font-medium">
                Trades (Today)
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Attributed P&amp;L
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Controls
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-700/50">
            {strategies.map((strategy) => (
              <StrategyRow
                key={strategy.id}
                strategy={strategy}
                onToggle={toggle}
                pending={
                  statusMutation.isPending &&
                  statusMutation.variables?.id === strategy.id
                }
              />
            ))}
          </tbody>
        </table>

        {isLoading && (
          <p className="py-4 text-center text-[11px] text-slate-400">
            Loading strategies…
          </p>
        )}
        {isError && !isLoading && (
          <p className="py-4 text-center text-[11px] text-loss">
            Failed to load strategies.
          </p>
        )}
        {!isLoading && !isError && strategies.length === 0 && (
          <p className="py-4 text-center text-[11px] text-slate-400">
            No strategies configured.
          </p>
        )}
      </div>
    </Panel>
  );
}
