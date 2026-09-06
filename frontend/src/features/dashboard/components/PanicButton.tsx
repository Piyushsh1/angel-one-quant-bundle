'use client';

import { useState } from 'react';
import {
  usePositions,
  usePanicSquareOff,
} from '@/features/dashboard/hooks/useDashboard';

/**
 * The emergency square-off control.
 *
 * The highest-consequence button in the product: it cancels every pending
 * order, market-closes every open position, and halts all strategy runners at
 * once. Two safeguards remain:
 *
 *   1. A confirmation step — the first click arms the action and reveals an
 *      explicit "Square off all" control, so a stray click can never flatten a
 *      book.
 *   2. The confirm panel is announced via `role="alertdialog"`, so keyboard and
 *      screen-reader users get the same protection.
 *
 * The action hits POST /dashboard/positions/panic through `usePanicSquareOff`,
 * which invalidates the whole terminal on success so every panel reflects the
 * flattened book.
 */
export function PanicButton() {
  const [armed, setArmed] = useState(false);
  const { data } = usePositions();
  const panic = usePanicSquareOff();

  const openPositions = data?.items.length ?? 0;

  async function execute() {
    try {
      const result = await panic.mutateAsync();
      // eslint-disable-next-line no-alert
      alert(result.message);
    } catch {
      // eslint-disable-next-line no-alert
      alert('Kill-switch failed. Please retry or contact support.');
    } finally {
      setArmed(false);
    }
  }

  return (
    <aside className="fixed bottom-6 right-6 z-50">
      {armed ? (
        <div
          role="alertdialog"
          aria-labelledby="panic-title"
          aria-describedby="panic-desc"
          className="w-72 rounded-2xl border border-loss/50 bg-surface-850 p-4 shadow-elevated"
        >
          <h2 id="panic-title" className="text-sm font-bold text-loss">
            Confirm emergency exit
          </h2>
          <p id="panic-desc" className="mt-1 text-xs text-slate-300">
            This cancels all pending orders, halts every strategy, and
            market-closes all{' '}
            <strong className="text-white">{openPositions}</strong> open
            positions immediately at market price.
          </p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => setArmed(false)}
              disabled={panic.isPending}
              className="flex-1 rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-surface-700 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={execute}
              disabled={panic.isPending}
              className="flex-1 rounded-lg bg-loss-strong px-3 py-2 text-xs font-bold text-white shadow-[0_0_25px_-5px_rgba(239,68,68,0.4)] transition hover:bg-loss disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-loss"
            >
              {panic.isPending ? 'Squaring off…' : 'Square off all'}
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setArmed(true)}
          title="Cancel all pending orders and market-close all open positions"
          className="group relative flex items-center gap-3 rounded-full border border-loss/50 bg-gradient-to-r from-loss-strong via-rose-600 to-red-700 px-4 py-3 text-xs font-bold uppercase tracking-wider text-white shadow-2xl transition-all duration-200 hover:from-loss hover:to-rose-600 active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-loss focus-visible:ring-offset-2 focus-visible:ring-offset-obsidian"
        >
          <span
            aria-hidden="true"
            className="pointer-events-none absolute -inset-1 animate-ping rounded-full bg-loss-strong opacity-30 group-hover:opacity-60"
          />
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/20">
            <svg
              aria-hidden="true"
              className="h-4 w-4 animate-pulse text-white"
              fill="none"
              stroke="currentColor"
              strokeWidth={2.5}
              viewBox="0 0 24 24"
            >
              <path
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span className="text-left">
            <span className="block text-[11px] font-extrabold leading-none tracking-widest">
              PANIC EXIT
            </span>
            <span className="block font-mono text-[9px] font-normal text-red-200">
              Close All ({openPositions}) Positions
            </span>
          </span>
        </button>
      )}
    </aside>
  );
}
