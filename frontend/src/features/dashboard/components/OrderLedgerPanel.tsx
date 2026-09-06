'use client';

import Link from 'next/link';
import { Panel, PanelHeader } from '@/features/dashboard/components/Panel';
import { useOrders } from '@/features/dashboard/hooks/useDashboard';
import type { Order } from '@/features/dashboard/schemas/dashboard.schema';
import { formatInr } from '@/features/dashboard/lib/format';
import { cn } from '@/lib/cn';

/** Colour for an order's terminal state. */
function statusColor(status: Order['status']): string {
  switch (status) {
    case 'FILLED':
      return 'text-profit';
    case 'CANCELLED':
    case 'REJECTED':
      return 'text-loss';
    default:
      return 'text-paper';
  }
}

function OrderRow({ order }: { order: Order }) {
  const isBuy = order.side === 'BUY';
  const inactive = order.status === 'CANCELLED' || order.status === 'REJECTED';

  return (
    <div
      className={cn(
        'flex items-center justify-between rounded border border-surface-700/50 bg-surface-900 p-2',
        inactive && 'opacity-75',
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'rounded border px-1 py-0.5 text-[10px]',
            isBuy
              ? 'border-profit/25 bg-profit/10 text-profit'
              : 'border-loss/25 bg-loss/10 text-loss',
          )}
        >
          {order.side}
        </span>
        <div>
          <div
            className={cn(
              'font-sans text-[11px] font-bold',
              inactive ? 'text-slate-300' : 'text-white',
            )}
          >
            {order.symbol}
          </div>
          <span className="text-[10px] text-slate-400">
            {order.time} • Qty: {order.qty}
          </span>
        </div>
      </div>
      <div className="text-right">
        <div className={cn('font-mono', inactive ? 'text-slate-400' : 'text-white')}>
          {formatInr(order.price)}
        </div>
        <span className={cn('text-[10px]', statusColor(order.status))}>
          {order.status}
        </span>
      </div>
    </div>
  );
}

/** Recent execution order ledger. */
export function OrderLedgerPanel({ className = 'lg:col-span-5' }: { className?: string } = {}) {
  const { data, isLoading, isError } = useOrders();
  const orders = data ?? [];

  return (
    <Panel className={className}>
      <PanelHeader
        title="Live Order Ledger"
        iconClassName="border-paper/20 bg-paper/10 text-paper"
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
              d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        }
        action={
          <Link
            href="/dashboard/orders"
            className="flex items-center gap-1 font-mono text-[11px] text-brand-400 hover:text-brand-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
          >
            Full Ledger
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

      <div className="space-y-2 font-mono text-xs">
        {isLoading &&
          Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="h-[52px] animate-pulse rounded border border-surface-700/50 bg-surface-900"
            />
          ))}
        {isError && !isLoading && (
          <p className="rounded border border-loss/30 bg-loss/5 p-3 text-[11px] text-loss">
            Failed to load orders.
          </p>
        )}
        {!isLoading && !isError && orders.length === 0 && (
          <p className="rounded border border-surface-700/50 bg-surface-900 p-4 text-center text-[11px] text-slate-400">
            No orders yet today.
          </p>
        )}
        {orders.map((order) => (
          <OrderRow key={order.id} order={order} />
        ))}
      </div>
    </Panel>
  );
}
