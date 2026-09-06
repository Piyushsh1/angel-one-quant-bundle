'use client';

import { useState } from 'react';
import { Panel, PanelHeader } from '@/features/dashboard/components/Panel';
import { useReports } from '@/features/dashboard/hooks/useDashboard';
import { downloadReportsCsv } from '@/features/dashboard/api/dashboard.api';
import {
  formatSignedInr,
  formatInr,
  formatSignedPct,
  pnlColor,
} from '@/features/dashboard/lib/format';
import { cn } from '@/lib/cn';
import type {
  ReportKpi,
  AlphaAttributionRow,
  HeatmapCell,
  ExpectancyRow,
} from '@/features/dashboard/schemas/dashboard.schema';

/* ────────────────────────────────────────────────────────────────────────────
 * Quant Reports & Alpha Telemetry
 *
 * Every figure is computed server-side from the user's own recorded P&L and
 * live positions. When there is no activity yet the panels render honest empty
 * states rather than invented numbers.
 * ──────────────────────────────────────────────────────────────────────────── */

// ── KPI header cards ──────────────────────────────────────────────────────────

function KpiCard({ kpi }: { kpi: ReportKpi }) {
  const toneClass =
    kpi.tone === 'profit'
      ? 'text-profit'
      : kpi.tone === 'loss'
        ? 'text-loss'
        : 'text-slate-100';
  return (
    <div className="rounded-xl border border-surface-700/70 bg-surface-850/70 p-4">
      <p className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
        {kpi.label}
      </p>
      <p className={cn('mt-2 font-mono text-2xl font-bold leading-none', toneClass)}>
        {kpi.value}
      </p>
      {kpi.changePct != null && (
        <p
          className={cn(
            'mt-2 font-mono text-[11px]',
            kpi.changePct >= 0 ? 'text-profit' : 'text-loss',
          )}
        >
          {formatSignedPct(kpi.changePct)} on capital
        </p>
      )}
    </div>
  );
}

// ── Equity curve vs benchmark ─────────────────────────────────────────────────

function EquityCurve({
  points,
  benchmark,
}: {
  points: number[][];
  benchmark: number[][];
}) {
  if (points.length < 2) {
    return (
      <p className="py-16 text-center text-[11px] text-slate-500">
        Not enough recorded P&amp;L yet to plot the alpha equity curve.
      </p>
    );
  }

  const W = 640;
  const H = 220;

  const all = [...points, ...benchmark];
  const ys = all.map((p) => p[1] ?? 0);
  const xs = points.map((p) => p[0] ?? 0);
  const minY = Math.min(...ys, 0);
  const maxY = Math.max(...ys, 0);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const spanY = maxY - minY || 1;
  const spanX = maxX - minX || 1;

  const toPath = (pts: number[][]) =>
    pts
      .map((p, i) => {
        const x = (((p[0] ?? 0) - minX) / spanX) * W;
        const y = H - (((p[1] ?? 0) - minY) / spanY) * H;
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
      })
      .join(' ');

  const path = toPath(points);
  const zeroY = H - ((0 - minY) / spanY) * H;
  const ending = ys[ys.length - 1] ?? 0;
  const areaPath = `${path} L${W} ${H} L0 ${H} Z`;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-56 w-full"
      preserveAspectRatio="none"
      role="img"
      aria-label="Cumulative alpha equity curve versus the NIFTY benchmark"
    >
      <defs>
        <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#34d399" stopOpacity="0.25" />
          <stop offset="100%" stopColor="#34d399" stopOpacity="0" />
        </linearGradient>
      </defs>

      <line
        x1="0"
        x2={W}
        y1={zeroY}
        y2={zeroY}
        stroke="currentColor"
        className="text-surface-700"
        strokeDasharray="4 4"
        strokeWidth={1}
      />

      <path d={areaPath} fill="url(#equityFill)" stroke="none" />

      {benchmark.length >= 2 && (
        <path
          d={toPath(benchmark)}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeDasharray="5 4"
          className="text-slate-500"
          vectorEffect="non-scaling-stroke"
        />
      )}

      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        className={ending >= 0 ? 'text-profit' : 'text-loss'}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

// ── Strategy alpha attribution ────────────────────────────────────────────────

function AlphaAttribution({
  rows,
  correlation,
}: {
  rows: AlphaAttributionRow[];
  correlation: number;
}) {
  if (rows.length === 0) {
    return (
      <p className="py-10 text-center text-[11px] text-slate-500">
        No attributed alpha yet. Contributions appear as strategies book P&amp;L.
      </p>
    );
  }
  return (
    <div className="space-y-3.5">
      {rows.map((row) => (
        <div key={row.name}>
          <div className="mb-1 flex items-baseline justify-between gap-2">
            <span className="truncate text-xs font-medium text-slate-200">
              {row.name}
            </span>
            <span className="flex items-center gap-2 whitespace-nowrap font-mono text-[11px]">
              <span className="text-slate-400">
                {row.contributionPct.toFixed(1)}%
              </span>
              <span className={pnlColor(row.pnl)}>{formatSignedInr(row.pnl)}</span>
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-800">
            <div
              className={cn(
                'h-full rounded-full',
                row.pnl >= 0 ? 'bg-profit' : 'bg-loss',
              )}
              style={{ width: `${Math.min(row.contributionPct, 100)}%` }}
            />
          </div>
        </div>
      ))}
      <div className="mt-4 flex items-center justify-between border-t border-surface-700/60 pt-3 text-[11px]">
        <span className="text-slate-500">Active Model Correlation</span>
        <span className="font-mono font-semibold text-slate-200">
          {correlation.toFixed(2)}{' '}
          <span className="text-slate-500">
            ({correlation < 0.3 ? 'High Orthogonality' : 'Correlated'})
          </span>
        </span>
      </div>
    </div>
  );
}

// ── Daily alpha heatmap ───────────────────────────────────────────────────────

const WEEKDAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'];

function DailyHeatmap({ cells }: { cells: HeatmapCell[] }) {
  if (cells.length === 0) {
    return (
      <p className="py-10 text-center text-[11px] text-slate-500">
        No recorded sessions in the last 30 days.
      </p>
    );
  }

  // Align the grid to weekday columns (Mon-first). getUTCDay: 0=Sun..6=Sat.
  const first = cells[0];
  const firstDow = first ? (new Date(`${first.date}T00:00:00Z`).getUTCDay() + 6) % 7 : 0;
  const lead = Array.from({ length: firstDow }, (_, i) => `lead-${i}`);

  const profitable = cells.filter((c) => c.tone === 'profit').length;
  const losing = cells.filter((c) => c.tone === 'loss').length;
  const flatOrNone = cells.length - profitable - losing;

  const cellClass = (tone: HeatmapCell['tone']) => {
    switch (tone) {
      case 'profit':
        return 'bg-profit-dim/60 border-profit/30 text-profit';
      case 'loss':
        return 'bg-loss-dim/50 border-loss/30 text-loss';
      case 'flat':
        return 'bg-surface-800 border-surface-700 text-slate-400';
      default:
        return 'border-surface-800/60 text-slate-600';
    }
  };

  return (
    <div>
      <div className="mb-2 grid grid-cols-7 gap-1.5">
        {WEEKDAYS.map((d) => (
          <div key={d} className="text-center text-[9px] font-medium text-slate-600">
            {d}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1.5">
        {lead.map((k) => (
          <div key={k} className="aspect-square rounded-md" />
        ))}
        {cells.map((cell) => (
          <div
            key={cell.date}
            title={
              cell.pnl != null
                ? `${cell.date}: ${formatSignedInr(cell.pnl)}`
                : `${cell.date}: no session`
            }
            className={cn(
              'flex aspect-square flex-col justify-between rounded-md border p-1',
              cellClass(cell.tone),
            )}
          >
            <span className="text-[9px] leading-none text-slate-500">{cell.day}</span>
            {cell.pnl != null && (
              <span className="font-mono text-[9px] font-semibold leading-none">
                {compactInr(cell.pnl)}
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t border-surface-700/60 pt-3 text-[11px] text-slate-500">
        <span>
          Profitable Sessions:{' '}
          <span className="font-semibold text-profit">{profitable}</span>
        </span>
        <span>
          Loss Sessions: <span className="font-semibold text-loss">{losing}</span>
        </span>
        <span>
          Flat / Holiday:{' '}
          <span className="font-semibold text-slate-300">{flatOrNone}</span>
        </span>
      </div>
    </div>
  );
}

/** Compact rupee for tight heatmap cells: +₹5.1k / −₹950. */
function compactInr(value: number): string {
  const sign = value < 0 ? '−' : '+';
  const abs = Math.abs(value);
  if (abs >= 1000) return `${sign}₹${(abs / 1000).toFixed(1)}k`;
  return `${sign}₹${Math.round(abs)}`;
}

// ── Drawdown profile (underwater) ─────────────────────────────────────────────

function DrawdownProfile({
  points,
  peakPct,
  recoveryFactor,
}: {
  points: number[][];
  peakPct: number;
  recoveryFactor: number;
}) {
  if (points.length < 2) {
    return (
      <p className="py-16 text-center text-[11px] text-slate-500">
        No drawdown to show yet.
      </p>
    );
  }
  const W = 640;
  const H = 200;
  const xs = points.map((p) => p[0] ?? 0);
  const ys = points.map((p) => p[1] ?? 0); // all ≤ 0
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const spanX = maxX - minX || 1;
  const minY = Math.min(...ys, -1); // most negative
  const spanY = Math.abs(minY) || 1;

  const path = points
    .map((p, i) => {
      const x = (((p[0] ?? 0) - minX) / spanX) * W;
      const y = ((0 - (p[1] ?? 0)) / spanY) * H; // 0 at top, dips downward
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(' ');
  const areaPath = `${path} L${W} 0 L0 0 Z`;

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-48 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label="Drawdown profile — percentage underwater from the high-water mark"
      >
        <defs>
          <linearGradient id="ddFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f87171" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#f87171" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <line
          x1="0"
          x2={W}
          y1="1"
          y2="1"
          stroke="currentColor"
          className="text-surface-600"
          strokeWidth={1}
        />
        <path d={areaPath} fill="url(#ddFill)" stroke="none" />
        <path
          d={path}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          className="text-loss"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="mt-2 flex items-center justify-between border-t border-surface-700/60 pt-3 text-[11px]">
        <span className="text-slate-500">
          Peak Drawdown:{' '}
          <span className="font-mono font-semibold text-loss">
            {peakPct.toFixed(1)}%
          </span>
        </span>
        <span className="text-slate-500">
          Recovery Factor:{' '}
          <span className="font-mono font-semibold text-slate-200">
            {recoveryFactor.toFixed(2)}x
          </span>
        </span>
      </div>
    </div>
  );
}

// ── Trade expectancy / execution breakdown ────────────────────────────────────

function statusToneClass(tone: ExpectancyRow['tone']): string {
  return tone === 'profit'
    ? 'border-profit/30 bg-profit/10 text-profit'
    : tone === 'loss'
      ? 'border-loss/30 bg-loss/10 text-loss'
      : 'border-brand-500/30 bg-brand-500/10 text-brand-400';
}

function ExpectancyTable({ rows }: { rows: ExpectancyRow[] }) {
  if (rows.length === 0) {
    return (
      <p className="py-8 text-center text-[11px] text-slate-500">
        Execution breakdown appears once trades are booked.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left font-mono text-xs">
        <thead>
          <tr className="border-b border-surface-700 text-[10px] uppercase tracking-wider text-slate-500">
            <th className="pb-2 font-medium">Metric Parameter</th>
            <th className="pb-2 text-right font-medium">Value / Ratio</th>
            <th className="hidden pb-2 font-medium sm:table-cell">Benchmark</th>
            <th className="pb-2 text-center font-medium">Status</th>
            <th className="pb-2 text-right font-medium">Risk</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-surface-700/50">
          {rows.map((row) => (
            <tr key={row.metric} className="hover:bg-surface-800/30">
              <td className="py-3 font-sans font-medium text-slate-200">
                {row.metric}
              </td>
              <td
                className={cn(
                  'py-3 text-right font-bold',
                  row.tone === 'profit'
                    ? 'text-profit'
                    : row.tone === 'loss'
                      ? 'text-loss'
                      : 'text-slate-200',
                )}
              >
                {row.value}
              </td>
              <td className="hidden py-3 text-slate-500 sm:table-cell">
                {row.benchmark}
              </td>
              <td className="py-3 text-center">
                <span
                  className={cn(
                    'inline-block rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
                    statusToneClass(row.tone),
                  )}
                >
                  {row.status}
                </span>
              </td>
              <td className="py-3 text-right text-slate-400">
                {row.riskScore.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Icons ─────────────────────────────────────────────────────────────────────

const iconEquity = (
  <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
    <path d="M3 3v18h18M7 14l4-4 3 3 5-6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
const iconAttribution = (
  <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
    <path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
const iconCalendar = (
  <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
    <path d="M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
const iconWave = (
  <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
    <path d="M3 12c2 0 3 5 5 5s3-10 5-10 3 8 5 8 2-3 3-3" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
const iconTable = (
  <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
    <path d="M4 5h16M4 12h16M4 19h16" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

// ── Export actions (also used by the page header) ─────────────────────────────

export function ReportsExportActions() {
  const [busy, setBusy] = useState(false);

  const onCsv = async () => {
    setBusy(true);
    try {
      await downloadReportsCsv();
    } catch {
      // Non-fatal — the button simply re-enables.
    } finally {
      setBusy(false);
    }
  };

  const btn =
    'inline-flex items-center gap-1.5 rounded-lg border border-surface-700 bg-surface-850 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-brand-500/40 hover:text-white disabled:opacity-50';

  return (
    <div className="flex items-center gap-2 print:hidden">
      <button type="button" onClick={() => window.print()} className={btn}>
        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path d="M6 9V2h12v7M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2M6 14h12v8H6z" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        PDF
      </button>
      <button type="button" onClick={onCsv} disabled={busy} className={btn}>
        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path d="M12 3v12m0 0l-4-4m4 4l4-4M4 21h16" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {busy ? 'Exporting…' : 'CSV'}
      </button>
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export function ReportsView() {
  const { data, isLoading, isError } = useReports();

  if (isLoading) {
    return (
      <Panel>
        <p className="py-8 text-center text-xs text-slate-400">
          Computing alpha telemetry…
        </p>
      </Panel>
    );
  }
  if (isError || !data) {
    return (
      <Panel>
        <p className="py-8 text-center text-xs text-loss">Failed to load reports.</p>
      </Panel>
    );
  }

  const ending = data.equityCurve.at(-1)?.[1] ?? 0;

  return (
    <div className="space-y-5">
      {data.smallSample && (
        <div className="rounded-lg border border-paper/40 bg-paper-dim/20 p-3 text-xs text-paper">
          Only {data.tradesTotal} closed trade(s) so far. Below 30, these figures
          are mostly luck — keep running before drawing conclusions.
        </div>
      )}

      {/* KPI header row */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {data.kpis.map((kpi) => (
          <KpiCard key={kpi.label} kpi={kpi} />
        ))}
      </div>

      {/* Equity curve + alpha attribution */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <Panel className="lg:col-span-8">
          <PanelHeader
            title="Cumulative Alpha Equity Curve vs NIFTY Benchmark"
            iconClassName="border-profit/20 bg-profit/10 text-profit"
            icon={iconEquity}
            badge={
              <span className="ml-2 flex items-center gap-3 text-[10px] text-slate-500">
                <span className="flex items-center gap-1">
                  <span className="h-1.5 w-3 rounded-full bg-profit" /> Barbell
                </span>
                <span className="flex items-center gap-1">
                  <span className="h-0 w-3 border-t border-dashed border-slate-500" />{' '}
                  NIFTY
                </span>
              </span>
            }
          />
          <EquityCurve points={data.equityCurve} benchmark={data.benchmarkCurve} />
          <dl className="mt-3 grid grid-cols-2 gap-3 font-mono text-xs sm:grid-cols-4">
            <Stat label="Gross P&L" value={formatSignedInr(data.grossPnl)} tone={data.grossPnl} />
            <Stat label="Charges" value={formatInr(data.totalCharges)} />
            <Stat label="Net P&L" value={formatSignedInr(data.netPnl)} tone={data.netPnl} />
            <Stat
              label="Ending Equity"
              value={formatSignedInr(ending)}
              tone={ending}
            />
          </dl>
        </Panel>

        <Panel className="lg:col-span-4">
          <PanelHeader
            title="Strategy Alpha Attribution"
            iconClassName="border-brand-500/20 bg-brand-500/10 text-brand-400"
            icon={iconAttribution}
          />
          <AlphaAttribution
            rows={data.alphaAttribution}
            correlation={data.modelCorrelation}
          />
        </Panel>
      </div>

      {/* Daily heatmap + drawdown profile */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <Panel className="lg:col-span-6">
          <PanelHeader
            title="Daily Alpha Heatmap (Last 30 Days)"
            iconClassName="border-purple-500/20 bg-purple-500/10 text-purple-400"
            icon={iconCalendar}
          />
          <DailyHeatmap cells={data.dailyHeatmap} />
        </Panel>

        <Panel className="lg:col-span-6">
          <PanelHeader
            title="Drawdown Profile (Underwater %)"
            iconClassName="border-loss/20 bg-loss/10 text-loss"
            icon={iconWave}
          />
          <DrawdownProfile
            points={data.drawdownCurve}
            peakPct={data.maxDrawdownPct}
            recoveryFactor={data.recoveryFactor}
          />
        </Panel>
      </div>

      {/* Trade expectancy / execution breakdown */}
      <Panel>
        <PanelHeader
          title="Trade Expectancy & Algorithmic Execution Breakdown"
          iconClassName="border-brand-500/20 bg-brand-500/10 text-brand-400"
          icon={iconTable}
          action={
            <span className="text-[10px] uppercase tracking-wider text-slate-500">
              {data.tradesTotal} executions logged
            </span>
          }
        />
        <ExpectancyTable rows={data.expectancyRows} />
      </Panel>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: number }) {
  const color =
    tone === undefined
      ? 'text-slate-300'
      : tone > 0
        ? 'text-profit'
        : tone < 0
          ? 'text-loss'
          : 'text-slate-300';
  return (
    <div>
      <dt className="text-[10px] text-slate-500">{label}</dt>
      <dd className={cn('font-semibold', color)}>{value}</dd>
    </div>
  );
}
