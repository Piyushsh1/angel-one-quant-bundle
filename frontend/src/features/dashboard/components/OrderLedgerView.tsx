'use client';

import { useMemo, useState } from 'react';
import { useOrders, useMetrics } from '@/features/dashboard/hooks/useDashboard';
import type { Order, OrderStatus } from '@/features/dashboard/schemas/dashboard.schema';
import { formatAmount } from '@/features/dashboard/lib/format';
import { cn } from '@/lib/cn';

/**
 * Full-page Order Ledger — the institutional execution log & audit trail.
 *
 * A denser, filterable counterpart to the compact `OrderLedgerPanel` used on
 * the dashboard. It derives its telemetry ribbon (total / filled / cancelled /
 * rejected / turnover) entirely from the live orders query so the numbers can
 * never drift from the table below them.
 *
 * Filtering and search run client-side over the already-fetched order set —
 * the day's ledger is small and bounded, so there is no need to round-trip the
 * server for a status or side filter.
 */

type StatusFilter = 'all' | Lowercase<OrderStatus>;
type SideFilter = 'all' | 'buy' | 'sell';

const STATUS_META: Record<
  OrderStatus,
  { label: string; dot: string; badge: string }
> = {
  FILLED: {
    label: 'FILLED',
    dot: 'bg-profit',
    badge: 'border-profit/30 bg-profit/10 text-profit',
  },
  PENDING: {
    label: 'PENDING',
    dot: 'bg-brand-400',
    badge: 'border-brand-400/30 bg-brand-400/10 text-brand-400',
  },
  CANCELLED: {
    label: 'CANCELLED',
    dot: 'bg-paper',
    badge: 'border-paper/30 bg-paper/10 text-paper',
  },
  REJECTED: {
    label: 'REJECTED',
    dot: 'bg-loss',
    badge: 'border-loss/30 bg-loss/10 text-loss',
  },
};

/** A single metric tile in the telemetry ribbon. */
function MetricTile({
  label,
  value,
  hint,
  icon,
  accent,
  fill,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: React.ReactNode;
  accent: string;
  fill: number;
}) {
  return (
    <div className="flex flex-col justify-between rounded-lg border border-surface-700/70 bg-surface-850 p-2.5">
      <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-400">
        <span>{label}</span>
        <span className={accent}>{icon}</span>
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className={cn('font-mono text-2xl font-semibold', accent)}>
          {value}
        </span>
        {hint && (
          <span className="font-mono text-[10px] text-slate-400">{hint}</span>
        )}
      </div>
      <div className="mt-2 h-0.5 w-full overflow-hidden rounded bg-surface-700/60">
        <div
          className={cn('h-full rounded', accent.replace('text-', 'bg-'))}
          style={{ width: `${Math.min(100, Math.max(0, fill))}%` }}
        />
      </div>
    </div>
  );
}

/** One order row in the dense audit table. */
function OrderRow({ order }: { order: Order }) {
  const isBuy = order.side === 'BUY';
  const inactive = order.status === 'CANCELLED' || order.status === 'REJECTED';
  const status = STATUS_META[order.status];

  return (
    <tr
      className={cn(
        'h-[34px] border-b border-surface-700/40 transition-colors hover:bg-surface-800/60',
        inactive && 'bg-surface-800/30',
      )}
    >
      <td className="px-3 py-1 font-mono text-[11px] text-slate-400">
        {order.time}
      </td>
      <td className="px-3 py-1 font-mono text-xs font-medium text-brand-400">
        #{order.id}
      </td>
      <td
        className={cn(
          'px-3 py-1 text-xs font-semibold',
          inactive ? 'text-slate-400' : 'text-white',
        )}
      >
        {order.symbol}
      </td>
      <td className="px-2 py-1 text-center">
        <span
          className={cn(
            'rounded border px-2 py-0.5 font-mono text-[10px] font-bold',
            isBuy
              ? 'border-profit/40 bg-profit/10 text-profit'
              : 'border-loss/40 bg-loss/10 text-loss',
          )}
        >
          {order.side}
        </span>
      </td>
      <td className="px-3 py-1 text-right font-mono text-xs text-slate-200">
        {order.qty}
      </td>
      <td
        className={cn(
          'px-3 py-1 text-right font-mono text-xs font-semibold',
          inactive ? 'text-slate-400' : 'text-white',
        )}
      >
        {order.status === 'CANCELLED' || order.status === 'REJECTED'
          ? '—'
          : `₹${formatAmount(order.price)}`}
      </td>
      <td className="px-3 py-1 text-center">
        <span
          className={cn(
            'inline-flex items-center justify-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold',
            status.badge,
          )}
        >
          <span className={cn('h-1.5 w-1.5 rounded-full', status.dot)} />
          {status.label}
        </span>
      </td>
    </tr>
  );
}

/** A labelled `<select>` styled to match the terminal filter bar. */
function FilterSelect({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="flex h-8 items-center gap-1.5 rounded border border-surface-700 bg-surface-900 px-2 font-mono text-[11px] text-white">
      <span className="text-slate-400">{label}:</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="cursor-pointer border-0 bg-transparent p-0 pr-5 text-[11px] text-white focus:ring-0"
      >
        {children}
      </select>
    </label>
  );
}

export function OrderLedgerView() {
  const { data, isLoading, isError } = useOrders();
  const { data: metrics } = useMetrics();
  const gatewayConnected = metrics?.gatewayConnected ?? false;

  const orders = useMemo(() => data ?? [], [data]);

  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [sideFilter, setSideFilter] = useState<SideFilter>('all');

  // Telemetry derives from the full order set, independent of the active view.
  const stats = useMemo(() => {
    const total = orders.length;
    const filled = orders.filter((o) => o.status === 'FILLED');
    const cancelled = orders.filter((o) => o.status === 'CANCELLED').length;
    const rejected = orders.filter((o) => o.status === 'REJECTED').length;
    const turnover = filled.reduce((sum, o) => sum + o.price * o.qty, 0);
    return {
      total,
      filled: filled.length,
      cancelled,
      rejected,
      turnover,
      fillRate: total ? (filled.length / total) * 100 : 0,
    };
  }, [orders]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return orders.filter((o) => {
      if (statusFilter !== 'all' && o.status.toLowerCase() !== statusFilter) {
        return false;
      }
      if (sideFilter !== 'all' && o.side.toLowerCase() !== sideFilter) {
        return false;
      }
      if (q) {
        const haystack = `${o.id} ${o.symbol}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [orders, query, statusFilter, sideFilter]);

  return (
    <div className="space-y-4">
      {/* Telemetry ribbon */}
      <section
        aria-label="Execution telemetry"
        className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
      >
        <MetricTile
          label="Total Orders"
          value={String(stats.total)}
          hint="Placed"
          accent="text-brand-400"
          fill={100}
          icon={<DocIcon />}
        />
        <MetricTile
          label="Filled"
          value={String(stats.filled)}
          hint={`${stats.fillRate.toFixed(1)}%`}
          accent="text-profit"
          fill={stats.fillRate}
          icon={<CheckIcon />}
        />
        <MetricTile
          label="Cancelled"
          value={String(stats.cancelled)}
          accent="text-paper"
          fill={stats.total ? (stats.cancelled / stats.total) * 100 : 0}
          icon={<MinusIcon />}
        />
        <MetricTile
          label="Rejected"
          value={String(stats.rejected)}
          hint={`${stats.total ? ((stats.rejected / stats.total) * 100).toFixed(1) : '0.0'}% error`}
          accent="text-loss"
          fill={stats.total ? (stats.rejected / stats.total) * 100 : 0}
          icon={<BlockIcon />}
        />
        <MetricTile
          label="Total Turnover"
          value={`₹${formatAmount(stats.turnover)}`}
          accent="text-white"
          fill={60}
          icon={<RupeeIcon />}
        />
      </section>

      {/* Filter & control bar */}
      <section className="flex flex-wrap items-center gap-2 rounded-lg border border-surface-700/70 bg-surface-850 p-2.5">
        <div className="relative min-w-[220px] flex-1">
          <span className="pointer-events-none absolute inset-y-0 left-2 flex items-center text-slate-400">
            <svg
              aria-hidden="true"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              viewBox="0 0 24 24"
            >
              <path
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search order ID or symbol…"
            aria-label="Search orders"
            className="h-8 w-full rounded border border-surface-700 bg-surface-900 pl-8 pr-2 font-mono text-xs text-white placeholder:text-slate-500 focus:border-brand-400 focus:ring-0"
          />
        </div>

        <FilterSelect
          label="Status"
          value={statusFilter}
          onChange={(v) => setStatusFilter(v as StatusFilter)}
        >
          <option value="all">All ({stats.total})</option>
          <option value="filled">Filled ({stats.filled})</option>
          <option value="pending">
            Pending ({orders.filter((o) => o.status === 'PENDING').length})
          </option>
          <option value="cancelled">Cancelled ({stats.cancelled})</option>
          <option value="rejected">Rejected ({stats.rejected})</option>
        </FilterSelect>

        <FilterSelect
          label="Side"
          value={sideFilter}
          onChange={(v) => setSideFilter(v as SideFilter)}
        >
          <option value="all">All Sides</option>
          <option value="buy">BUY</option>
          <option value="sell">SELL</option>
        </FilterSelect>

        <div className="flex h-8 items-center gap-1.5 rounded border border-surface-700 bg-surface-900 px-2.5 font-mono text-[11px] text-slate-300">
          <svg
            aria-hidden="true"
            className="h-3.5 w-3.5 text-slate-400"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            viewBox="0 0 24 24"
          >
            <path
              d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span>Today</span>
        </div>
      </section>

      {/* Dense audit table */}
      <section className="overflow-hidden rounded-xl border border-surface-700/70 bg-surface-850 shadow-elevated">
        <div className="w-full overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="h-8 border-b border-surface-700 bg-surface-900 text-[10px] uppercase tracking-wider text-slate-400">
                <th className="px-3 py-1 font-medium">Time</th>
                <th className="px-3 py-1 font-medium">Order ID</th>
                <th className="px-3 py-1 font-medium">Instrument</th>
                <th className="px-2 py-1 text-center font-medium">Side</th>
                <th className="px-3 py-1 text-right font-medium">Qty</th>
                <th className="px-3 py-1 text-right font-medium">Price</th>
                <th className="px-3 py-1 text-center font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 6 }).map((_, i) => (
                  <tr key={i} className="h-[34px] border-b border-surface-700/40">
                    <td colSpan={7} className="px-3 py-1">
                      <div className="h-4 w-full animate-pulse rounded bg-surface-800" />
                    </td>
                  </tr>
                ))}

              {!isLoading &&
                rows.map((order) => <OrderRow key={order.id} order={order} />)}
            </tbody>
          </table>

          {isError && !isLoading && (
            <p className="border-t border-loss/30 bg-loss/5 p-4 text-center text-xs text-loss">
              Failed to load orders.
            </p>
          )}
          {!isLoading && !isError && rows.length === 0 && (
            <p className="p-6 text-center text-xs text-slate-400">
              No orders match the current filters.
            </p>
          )}
        </div>

        {/* Footer / telemetry status bar */}
        <div className="flex h-9 items-center justify-between border-t border-surface-700 bg-surface-900 px-3 font-mono text-[11px] text-slate-400">
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                'h-2 w-2 rounded-full',
                gatewayConnected ? 'bg-profit' : 'bg-slate-500',
              )}
            />
            {gatewayConnected
              ? 'OMS sync: broker connected'
              : 'OMS sync: no broker connected'}
          </span>
          <span>
            Showing {rows.length} of {orders.length} orders
          </span>
        </div>
      </section>
    </div>
  );
}

// ── Inline icon glyphs (16px, currentColor) ──────────────────────────────────

function DocIcon() {
  return (
    <svg aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path d="M9 12h6m-6 4h6m2 4H7a2 2 0 01-2-2V6a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V18a2 2 0 01-2 2z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function CheckIcon() {
  return (
    <svg aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function MinusIcon() {
  return (
    <svg aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path d="M15 12H9m12 0a9 9 0 11-18 0 9 9 0 0118 0z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function BlockIcon() {
  return (
    <svg aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path d="M18.364 5.636L5.636 18.364M21 12a9 9 0 11-18 0 9 9 0 0118 0z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function RupeeIcon() {
  return (
    <svg aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path d="M6 4h12M6 8h12m-9 0c3 0 5 1.5 5 4s-2 4-5 4H7l6 4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
