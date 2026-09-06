'use client';

import { useMemo, useState } from 'react';
import { cn } from '@/lib/cn';
import { Panel } from '@/features/dashboard/components/Panel';
import {
  useMetrics,
  useIntradayPnl,
  usePositions,
  useClosePosition,
  useRiskParameters,
} from '@/features/dashboard/hooks/useDashboard';
import type { Position } from '@/features/dashboard/schemas/dashboard.schema';
import {
  formatInr,
  formatSignedInr,
  formatSignedPct,
  pnlColor,
} from '@/features/dashboard/lib/format';

/**
 * Net Positions — the full derivative-risk view.
 *
 * Reproduces the institutional "Net Positions" layout: a bento row of KPI
 * tiles across the top, then a dense, filterable table of every open position
 * with per-row square-off, and an aggregate footer.
 *
 * Everything here reads LIVE terminal data (`useMetrics`, `useIntradayPnl`,
 * `usePositions`) and routes square-off through `useClosePosition`, so the view
 * always reflects real broker state rather than mock rows. The paper-mode
 * banner, header, sidebar, and panic button come from the shared (dashboard)
 * layout, so this component only renders the page body.
 */
export function NetPositionsView() {
  return (
    <div className="space-y-4">
      <KpiTiles />
      <PositionsTable />
    </div>
  );
}

// ── KPI bento tiles ──────────────────────────────────────────────────────────

function KpiTiles() {
  const { data: metrics } = useMetrics();
  const { data: pnl } = useIntradayPnl();

  const dayNet = pnl?.currentPnl ?? metrics?.totalPnl ?? 0;
  const dayNetPct = metrics?.totalPnlPct ?? 0;
  const realized = pnl?.realized ?? 0;
  const unrealized = pnl?.unrealized ?? 0;

  return (
    <section
      aria-label="Position metrics summary"
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
    >
      {/* Tile 1 — Day Net P&L */}
      <Tile accent={dayNet >= 0 ? 'profit' : 'loss'}>
        <TileHead label="Day Net P&L" />
        <div className="my-1.5 flex items-baseline gap-2">
          <span
            className={cn(
              'font-mono text-2xl font-bold tracking-tight',
              pnlColor(dayNet),
            )}
          >
            {formatSignedInr(dayNet)}
          </span>
          <span
            className={cn('font-mono text-[11px] font-semibold', pnlColor(dayNet))}
          >
            {formatSignedPct(dayNetPct)}
          </span>
        </div>
        <TileFoot
          left={
            <>
              Day High:{' '}
              <strong className="font-mono text-white">
                {formatInr(pnl?.dayHigh ?? 0)}
              </strong>
            </>
          }
          right={
            <>
              Day Low:{' '}
              <strong className="font-mono text-white">
                {formatSignedInr(pnl?.dayLow ?? 0)}
              </strong>
            </>
          }
        />
      </Tile>

      {/* Tile 2 — Realized vs Unrealized */}
      <Tile accent="brand">
        <TileHead label="Realized vs Unrealized" />
        <div className="my-1.5 grid grid-cols-2 gap-2">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-400">
              Realized
            </div>
            <div className={cn('font-mono text-sm font-bold', pnlColor(realized))}>
              {formatSignedInr(realized)}
            </div>
          </div>
          <div className="border-l border-surface-700 pl-2">
            <div className="text-[10px] uppercase tracking-wider text-slate-400">
              MTM Float
            </div>
            <div
              className={cn('font-mono text-sm font-bold', pnlColor(unrealized))}
            >
              {formatSignedInr(unrealized)}
            </div>
          </div>
        </div>
        <TileFoot
          left={<>Booked intraday</>}
          right={<span className="text-brand-400">Live mark-to-market</span>}
        />
      </Tile>

      {/* Tile 3 — Gross Exposure */}
      <Tile accent="neutral">
        <TileHead label="Gross Exposure" />
        <div className="my-1.5 flex items-baseline gap-2">
          <span className="font-mono text-2xl font-bold tracking-tight text-white">
            {formatInr(metrics?.netExposure ?? 0)}
          </span>
          <span className="font-mono text-[11px] text-slate-400">
            ({metrics?.openPositionsCount ?? 0} pos)
          </span>
        </div>
        <TileFoot
          left={
            <>
              Margin Used:{' '}
              <strong className="font-mono text-white">
                {formatInr(
                  Math.max(
                    (metrics?.totalCapital ?? 0) -
                      (metrics?.availableMargin ?? 0),
                    0,
                  ),
                )}
              </strong>
            </>
          }
          right={
            <>
              {metrics?.longCount ?? 0}L /{' '}
              <span className="text-loss">{metrics?.shortCount ?? 0}S</span>
            </>
          }
        />
      </Tile>

      {/* Tile 4 — Book composition */}
      <Tile accent="brand">
        <TileHead label="Book Composition" />
        <div className="my-1 grid grid-cols-2 gap-x-2 gap-y-1 font-mono text-[11px]">
          <Row k="Open" v={String(metrics?.openPositionsCount ?? 0)} />
          <Row
            k="Long"
            v={String(metrics?.longCount ?? 0)}
            vClass="text-profit"
          />
          <Row
            k="Executed"
            v={String(metrics?.executedTrades ?? 0)}
          />
          <Row
            k="Short"
            v={String(metrics?.shortCount ?? 0)}
            vClass="text-loss"
          />
        </div>
        <div className="mt-1 flex items-center justify-between border-t border-surface-700/40 pt-1 font-mono text-[10px] text-slate-400">
          <span>
            Win rate:{' '}
            <strong className="text-white">
              {(metrics?.winRate ?? 0).toFixed(1)}%
            </strong>
          </span>
          <span>
            Strategies:{' '}
            <strong className="text-brand-400">
              {metrics?.activeStrategies ?? 0} live
            </strong>
          </span>
        </div>
      </Tile>
    </section>
  );
}

const ACCENT_BAR = {
  profit: 'bg-profit',
  loss: 'bg-loss',
  brand: 'bg-brand-500',
  neutral: 'bg-surface-600',
} as const;

function Tile({
  accent,
  children,
}: {
  accent: keyof typeof ACCENT_BAR;
  children: React.ReactNode;
}) {
  return (
    <article className="relative flex flex-col justify-between overflow-hidden rounded-xl border border-surface-700/70 bg-surface-850 p-3">
      {children}
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-x-0 bottom-0 h-[2px]',
          ACCENT_BAR[accent],
        )}
      />
    </article>
  );
}

function TileHead({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-between text-[11px] uppercase tracking-wider text-slate-400">
      <span>{label}</span>
    </div>
  );
}

function TileFoot({
  left,
  right,
}: {
  left: React.ReactNode;
  right: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between font-mono text-[10px] text-slate-400">
      <span>{left}</span>
      <span>{right}</span>
    </div>
  );
}

function Row({
  k,
  v,
  vClass,
}: {
  k: string;
  v: string;
  vClass?: string;
}) {
  return (
    <div className="flex justify-between">
      <span className="text-slate-400">{k}:</span>
      <span className={cn('font-semibold text-white', vClass)}>{v}</span>
    </div>
  );
}

// ── Positions table ──────────────────────────────────────────────────────────

function PositionsTable() {
  const { data, isLoading, isError } = usePositions();
  const { data: risk } = useRiskParameters();
  const { data: pnl } = useIntradayPnl();
  const closeMutation = useClosePosition();
  const [filter, setFilter] = useState('');

  const items = useMemo(() => data?.items ?? [], [data?.items]);
  const total = data?.aggregateUnrealized ?? 0;

  // Real risk-limit compliance: open-position count vs the configured cap, and
  // today's loss vs the daily loss limit. Computed from live data + the user's
  // own risk parameters — never a hardcoded "compliant" badge.
  const currentPnl = pnl?.currentPnl ?? 0;
  const overPositionCap =
    risk != null && items.length > risk.maxOpenPositions;
  const overLossLimit =
    risk != null && currentPnl < 0 && Math.abs(currentPnl) > risk.maxDailyLoss;
  const breached = overPositionCap || overLossLimit;
  const complianceText = !risk
    ? 'Risk limits loading…'
    : overLossLimit
      ? 'Daily loss limit breached'
      : overPositionCap
        ? 'Open-position limit exceeded'
        : 'All risk limits compliant';

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return items;
    return items.filter((p) => p.symbol.toLowerCase().includes(q));
  }, [items, filter]);

  const grossValue = useMemo(
    () => filtered.reduce((sum, p) => sum + Math.abs(p.qty) * p.ltp, 0),
    [filtered],
  );

  return (
    <Panel className="overflow-hidden p-0">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-surface-700/70 bg-surface-900 px-4 py-2">
        <div className="flex items-center gap-4 font-mono text-[11px]">
          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="h-2 w-2 animate-ping rounded-full bg-profit"
            />
            <span className="font-bold uppercase tracking-wider text-white">
              Active Open Positions ({items.length})
            </span>
          </div>
          <span aria-hidden="true" className="h-3 w-px bg-surface-700" />
          <span className="text-slate-400">
            Auto-refresh:{' '}
            <span className="font-semibold text-brand-400">5s tick stream</span>
          </span>
        </div>

        <div className="relative">
          <label htmlFor="position-filter" className="sr-only">
            Filter by symbol
          </label>
          <input
            id="position-filter"
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter symbol…"
            className="h-7 w-48 rounded border border-surface-700 bg-surface-900 px-2 pr-7 font-mono text-[11px] text-white placeholder:text-slate-500 focus:border-brand-400 focus:outline-none focus:ring-0"
          />
          <svg
            aria-hidden="true"
            className="pointer-events-none absolute right-2 top-1.5 h-3.5 w-3.5 text-slate-500"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            viewBox="0 0 24 24"
          >
            <path
              d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
      </div>

      {/* Viewport */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="h-7 select-none border-b border-surface-700/70 bg-surface-900 text-[10px] uppercase tracking-wider text-slate-400">
              <th className="px-3 font-semibold">Instrument</th>
              <th className="px-3 text-center font-semibold">Side</th>
              <th className="px-3 text-right font-semibold">Net Qty</th>
              <th className="px-3 text-right font-semibold">Avg Entry</th>
              <th className="px-3 text-right font-semibold">LTP (₹)</th>
              <th className="px-3 text-right font-semibold">P&L (MTM)</th>
              <th className="px-3 text-right font-semibold">Value (₹)</th>
              <th className="px-3 text-center font-semibold">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-700/40 font-mono text-xs">
            {isLoading && <SkeletonRows />}

            {isError && !isLoading && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-[11px] text-loss">
                  Failed to load positions.
                </td>
              </tr>
            )}

            {!isLoading && !isError && filtered.length === 0 && (
              <tr>
                <td
                  colSpan={8}
                  className="px-3 py-8 text-center text-[11px] text-slate-400"
                >
                  {items.length === 0
                    ? 'No open positions.'
                    : 'No positions match that filter.'}
                </td>
              </tr>
            )}

            {filtered.map((position) => (
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
          </tbody>
        </table>
      </div>

      {/* Aggregate footer */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-surface-700/70 bg-surface-900 px-4 py-2 font-mono text-[11px]">
        <div className="flex items-center gap-4 text-slate-400">
          <span>Showing {filtered.length} of {items.length} positions</span>
          <span className={breached ? 'text-loss' : 'text-profit'}>
            {complianceText}
          </span>
        </div>
        <div className="flex items-center gap-6">
          <span className="text-slate-400">
            Active exposure:{' '}
            <strong className="text-white">{formatInr(grossValue)}</strong>
          </span>
          <span className="text-slate-400">
            Unrealized net:{' '}
            <strong className={cn('font-bold', pnlColor(total))}>
              {formatSignedInr(total)}
            </strong>
          </span>
        </div>
      </div>
    </Panel>
  );
}

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
  const value = Math.abs(position.qty) * position.ltp;

  return (
    <tr className="h-9 transition-colors hover:bg-surface-800/60">
      <td className="px-3 py-1">
        <span className="text-[13px] font-bold text-white">
          {position.symbol}
        </span>
      </td>
      <td className="px-3 py-1 text-center">
        <span
          className={cn(
            'rounded border px-1.5 py-0.5 text-[10px] font-bold tracking-wider',
            isBuy
              ? 'border-profit/30 bg-profit/10 text-profit'
              : 'border-loss/30 bg-loss/10 text-loss',
          )}
        >
          {isBuy ? 'LONG' : 'SHORT'}
        </span>
      </td>
      <td
        className={cn(
          'px-3 py-1 text-right font-semibold',
          isBuy ? 'text-white' : 'text-loss',
        )}
      >
        {position.qty > 0 ? `+${position.qty}` : position.qty}
      </td>
      <td className="px-3 py-1 text-right text-slate-400">
        {formatInr(position.avgPrice)}
      </td>
      <td className="bg-brand-500/5 px-3 py-1 text-right font-bold text-white">
        {formatInr(position.ltp)}
      </td>
      <td className="px-3 py-1 text-right">
        <div className={cn('font-bold', pnlColor(position.pnl))}>
          {formatSignedInr(position.pnl)}
        </div>
        <div className={cn('text-[10px] font-semibold', pnlColor(position.pnl))}>
          {formatSignedPct(position.pnlPct)}
        </div>
      </td>
      <td className="px-3 py-1 text-right text-white">{formatInr(value)}</td>
      <td className="px-3 py-1 text-center">
        <button
          type="button"
          onClick={() => onExit(position.id)}
          disabled={exiting}
          title="Square off position at market"
          className="h-6 rounded border border-loss/30 bg-loss/10 px-2 text-[10px] font-semibold text-loss transition-colors hover:bg-loss/20 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-loss"
        >
          {exiting ? 'Exiting…' : 'Square Off'}
        </button>
      </td>
    </tr>
  );
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 3 }).map((_, i) => (
        <tr key={i} className="h-9">
          <td colSpan={8} className="px-3 py-1">
            <div className="h-5 animate-pulse rounded bg-surface-800" />
          </td>
        </tr>
      ))}
    </>
  );
}
