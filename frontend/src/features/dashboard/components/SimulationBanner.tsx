'use client';

import { useMetrics } from '@/features/dashboard/hooks/useDashboard';
import { useLiveStream } from '@/features/dashboard/hooks/useLiveStream';
import { cn } from '@/lib/cn';

/**
 * The environment/mode banner.
 *
 * It reflects the REAL connection state from live metrics — never a fixed
 * label. When a broker is connected the terminal is acting on the user's real
 * account, so it says LIVE (red) and never claims "no capital at risk". When no
 * broker is connected it says so plainly. There is no fabricated latency figure.
 */
export function SimulationBanner() {
  // Activate the live SSE stream for the whole terminal (mounted once here,
  // on every dashboard page). Pushes metrics + positions into the query cache.
  useLiveStream();

  const { data: metrics } = useMetrics();
  const connected = metrics?.gatewayConnected ?? false;
  const live = metrics?.liveTradingEnabled ?? false;
  const gateway = metrics?.gatewayName ?? 'No broker connected';

  // Three honest states: not connected (grey), paper (amber — the safe
  // default), and live (red — real money). Live requires BOTH a connected
  // broker and the live-trading switch on.
  const liveActive = connected && live;
  const tone = !connected ? 'off' : liveActive ? 'live' : 'paper';

  const styles = {
    off: 'border-surface-700 bg-surface-850 text-slate-400',
    paper: 'border-paper/30 bg-paper/10 text-paper',
    live: 'border-loss/30 bg-loss/10 text-loss',
  }[tone];

  const dotBg = { off: 'bg-surface-700', paper: 'bg-paper/20', live: 'bg-loss/20' }[
    tone
  ];

  const label = {
    off: 'Broker Not Connected',
    paper: 'Paper Trading',
    live: 'Live Trading',
  }[tone];

  const detail = {
    off: 'Connect your broker to trade and see live data',
    paper: 'Orders are simulated — no real capital at risk',
    live: 'Orders route to your real broker account • real capital at risk',
  }[tone];

  return (
    <div
      role="status"
      className={cn(
        'flex w-full items-center justify-between border-b px-4 py-1.5 font-mono text-xs tracking-wide',
        styles,
      )}
    >
      <div className="mx-auto flex items-center gap-2 sm:mx-0">
        <span
          className={cn(
            'inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold',
            dotBg,
          )}
        >
          {tone === 'off' ? '!' : '●'}
        </span>
        <span className="text-[11px] font-semibold uppercase tracking-wider">
          {label}
        </span>
        <span className="hidden opacity-50 md:inline">—</span>
        <span className="hidden text-xs opacity-90 md:inline">{detail}</span>
      </div>

      <div className="hidden items-center gap-3 sm:flex">
        <span className="text-[11px] opacity-80">Gateway: {gateway}</span>
      </div>
    </div>
  );
}
