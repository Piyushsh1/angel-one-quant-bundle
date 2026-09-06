'use client';

import { Panel } from '@/features/dashboard/components/Panel';
import { useIntradayPnl } from '@/features/dashboard/hooks/useDashboard';
import { formatInr, formatSignedInr } from '@/features/dashboard/lib/format';

const VIEW_W = 600;
const VIEW_H = 200;
const BASELINE_Y = 130;

const TIME_LABELS = [
  '09:15 Market Open',
  '10:30',
  '11:45',
  '13:00',
  '14:15',
  '15:30 Square-off',
];

/**
 * Project live [minute, pnl] points into the fixed SVG viewBox.
 *
 * X is scaled across the full session width; Y places ₹0 at the baseline and
 * scales the P&L so the curve's extremes fill the panel without clipping. The
 * scale is symmetric around ₹0 so profit and loss read at the same visual
 * weight.
 */
function projectPoints(
  points: ReadonlyArray<readonly [number, number]>,
): Array<[number, number]> {
  if (points.length === 0) return [];

  const minutes = points.map((p) => p[0]);
  const pnls = points.map((p) => p[1]);
  const maxMinute = Math.max(...minutes, 1);
  const maxAbs = Math.max(Math.abs(Math.max(...pnls)), Math.abs(Math.min(...pnls)), 1);

  // Leave headroom so markers/glow aren't clipped at the edges.
  const topSpan = BASELINE_Y - 20;
  const bottomSpan = VIEW_H - BASELINE_Y - 20;
  const span = Math.min(topSpan, bottomSpan);

  return points.map(([minute, pnl]) => {
    const x = (minute / maxMinute) * VIEW_W;
    const y = BASELINE_Y - (pnl / maxAbs) * span;
    return [Number(x.toFixed(2)), Number(y.toFixed(2))];
  });
}

function strokePath(points: ReadonlyArray<readonly [number, number]>): string {
  return points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'} ${x} ${y}`).join(' ');
}

function areaPath(points: ReadonlyArray<readonly [number, number]>): string {
  const first = points[0];
  const last = points[points.length - 1];
  if (!first || !last) return '';
  return `${strokePath(points)} L ${last[0]} ${VIEW_H} L ${first[0]} ${VIEW_H} Z`;
}

/**
 * The intraday P&L curve — the centrepiece of the terminal. Drawn from the live
 * `/dashboard/pnl/intraday` feed. The headline number and stat pills carry the
 * meaning; the gradient, glow and markers are decorative.
 */
export function IntradayPnlPanel() {
  const { data, isLoading, isError } = useIntradayPnl();

  const projected = data ? projectPoints(data.points as Array<[number, number]>) : [];
  const positive = (data?.currentPnl ?? 0) >= 0;
  const stroke = positive ? '#10B981' : '#EF4444';

  const highMarker: [number, number] | null = projected.length
    ? projected.reduce<[number, number]>(
        (best, p) => (p[1] < best[1] ? p : best),
        projected[0] as [number, number],
      )
    : null;
  const liveMarker: [number, number] | null =
    projected.length > 0 ? (projected[projected.length - 1] as [number, number]) : null;

  return (
    <Panel className="flex flex-col justify-between lg:col-span-7">
      {/* Header */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-surface-700/60 pb-3">
        <div className="flex items-center gap-2.5">
          <span
            className={`rounded-lg border p-1.5 ${positive ? 'border-profit/20 bg-profit/10 text-profit' : 'border-loss/20 bg-loss/10 text-loss'}`}
          >
            <svg
              aria-hidden="true"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              viewBox="0 0 24 24"
            >
              <path
                d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Intraday P&amp;L Curve
            </h2>
            <div
              className={`font-mono text-lg font-bold leading-tight ${positive ? 'text-profit' : 'text-loss'}`}
            >
              {data ? formatSignedInr(data.currentPnl) : '—'}{' '}
              <span className="font-mono text-xs font-normal text-slate-400">
                (Realtime)
              </span>
            </div>
          </div>
        </div>

        {/* Stat pills */}
        <div className="flex items-center gap-3 rounded-lg border border-surface-700 bg-surface-900 px-3 py-1.5 font-mono text-[11px]">
          <Stat label="Day High" value={data ? formatInr(data.dayHigh) : '—'} tone="text-profit" />
          <Divider />
          <Stat label="Day Low" value={data ? formatSignedInr(data.dayLow) : '—'} tone="text-loss" />
          <Divider />
          <Stat label="Realized" value={data ? formatInr(data.realized) : '—'} tone="text-profit" />
          <Divider />
          <Stat label="Unrealized" value={data ? formatInr(data.unrealized) : '—'} tone="text-brand-400" />
        </div>
      </div>

      {/* Chart */}
      <div className="relative my-1 h-52 w-full">
        {/* Horizontal grid lines */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 flex flex-col justify-between opacity-20"
        >
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="w-full border-b border-dashed border-slate-600" />
          ))}
        </div>

        {(isLoading || isError || projected.length === 0) && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-[11px] text-slate-400">
              {isError
                ? 'Failed to load P&L curve.'
                : isLoading
                  ? 'Loading P&L curve…'
                  : 'No P&L data for today.'}
            </p>
          </div>
        )}

        {projected.length > 0 && (
          <svg
            aria-hidden="true"
            className="h-full w-full overflow-visible"
            preserveAspectRatio="none"
            viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          >
            <defs>
              <linearGradient id="pnl-fill" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity="0.38" />
                <stop offset="60%" stopColor={stroke} stopOpacity="0.08" />
                <stop offset="100%" stopColor={stroke} stopOpacity="0" />
              </linearGradient>
              <filter id="pnl-glow" x="-10%" y="-10%" width="120%" height="120%">
                <feDropShadow
                  dx="0"
                  dy="0"
                  stdDeviation="3"
                  floodColor={stroke}
                  floodOpacity="0.6"
                />
              </filter>
            </defs>

            {/* Zero baseline */}
            <line
              x1="0"
              x2={VIEW_W}
              y1={BASELINE_Y}
              y2={BASELINE_Y}
              stroke="#334155"
              strokeDasharray="4 4"
              strokeWidth={1}
            />

            <path d={areaPath(projected)} fill="url(#pnl-fill)" />
            <path
              d={strokePath(projected)}
              fill="none"
              stroke={stroke}
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              filter="url(#pnl-glow)"
            />

            {/* Intraday high (visually the highest point = lowest y) */}
            {highMarker && (
              <>
                <circle
                  className="animate-ping"
                  cx={highMarker[0]}
                  cy={highMarker[1]}
                  r={4.5}
                  fill={stroke}
                  opacity={0.75}
                />
                <circle
                  cx={highMarker[0]}
                  cy={highMarker[1]}
                  r={4}
                  fill="#FFFFFF"
                  stroke={stroke}
                  strokeWidth={2}
                />
              </>
            )}

            {/* Live endpoint */}
            {liveMarker && (
              <>
                <circle cx={liveMarker[0]} cy={liveMarker[1]} r={4.5} fill={stroke} />
                <circle
                  className="animate-pulse"
                  cx={liveMarker[0]}
                  cy={liveMarker[1]}
                  r={8}
                  fill={stroke}
                  opacity={0.3}
                />
              </>
            )}
          </svg>
        )}
      </div>

      {/* Time axis */}
      <div className="flex justify-between border-t border-surface-700/60 pt-2 font-mono text-[10px] text-slate-400">
        {TIME_LABELS.map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
    </Panel>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: string;
}) {
  return (
    <div>
      <span className="block text-[9px] uppercase text-slate-500">{label}</span>
      <span className={`font-semibold ${tone}`}>{value}</span>
    </div>
  );
}

function Divider() {
  return <div className="h-6 w-px bg-surface-700" />;
}
