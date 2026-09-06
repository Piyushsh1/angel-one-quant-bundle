'use client';

import { useIntradayPnl, useMetrics } from '@/features/dashboard/hooks/useDashboard';
import { formatInr } from '@/features/dashboard/lib/format';
import { cn } from '@/lib/cn';
import type { RiskParameters } from '@/features/dashboard/schemas/dashboard.schema';

/** One tile in the RMS status strip. */
function StatusTile({
  label,
  children,
  icon,
  tone = 'neutral',
}: {
  label: string;
  children: React.ReactNode;
  icon: React.ReactNode;
  tone?: 'ok' | 'warn' | 'neutral';
}) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-surface-700/70 bg-surface-850 px-4 py-3">
      <div>
        <p className="text-[10px] uppercase tracking-wider text-slate-500">
          {label}
        </p>
        <div className="mt-1">{children}</div>
      </div>
      <span
        className={cn(
          'flex h-8 w-8 items-center justify-center rounded-lg border',
          tone === 'ok' && 'border-profit/25 bg-profit/10 text-profit',
          tone === 'warn' && 'border-loss/25 bg-loss/10 text-loss',
          tone === 'neutral' && 'border-brand-500/25 bg-brand-500/10 text-brand-400',
        )}
      >
        {icon}
      </span>
    </div>
  );
}

/**
 * The engine-health strip beneath the header: is the circuit engine enforcing,
 * the latency-failover status, and today's hard loss cutoff with live drawdown.
 * The armed state reflects the real kill-switch flag; the drawdown is the live
 * intraday P&L when negative.
 */
export function RmsStatusStrip({ form }: { form: RiskParameters }) {
  const { data: pnl } = useIntradayPnl();
  const { data: metrics } = useMetrics();

  const armed = form.killSwitchArmed;
  const currentPnl = pnl?.currentPnl ?? 0;
  const drawdown = currentPnl < 0 ? Math.abs(currentPnl) : 0;
  const brokerConnected = metrics?.gatewayConnected ?? false;
  const brokerName = metrics?.gatewayName ?? 'No broker connected';

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      <StatusTile
        label="RMS Circuit Engine"
        tone={armed ? 'warn' : 'ok'}
        icon={
          <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        }
      >
        <p className="flex items-center gap-2 text-sm font-bold text-white">
          <span
            className={cn(
              'h-2 w-2 rounded-full',
              armed ? 'bg-loss' : 'animate-pulse bg-profit',
            )}
          />
          {armed ? 'HALTED — KILL SWITCH ARMED' : 'ACTIVE & ENFORCING'}
        </p>
      </StatusTile>

      <StatusTile
        label="Execution Gateway"
        tone={brokerConnected ? 'ok' : 'warn'}
        icon={
          <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
        }
      >
        <p className="flex items-center gap-2 text-sm font-bold text-white">
          <span
            className={cn(
              'h-2 w-2 rounded-full',
              brokerConnected ? 'animate-pulse bg-profit' : 'bg-slate-500',
            )}
          />
          <span className="truncate">
            {brokerConnected ? brokerName : 'Not connected'}
          </span>
        </p>
      </StatusTile>

      <StatusTile
        label="Hard Loss Cutoff Today"
        tone={drawdown > 0 ? 'warn' : 'neutral'}
        icon={
          <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 17h8m0 0V9m0 8l-8-8-4 4-6-6" />
          </svg>
        }
      >
        <p className="font-mono text-sm font-bold text-white">
          {formatInr(form.maxDailyLoss)}{' '}
          <span
            className={cn(
              'text-[11px] font-normal',
              drawdown > 0 ? 'text-loss' : 'text-slate-500',
            )}
          >
            (Drawdown: {drawdown > 0 ? `−${formatInr(drawdown)}` : formatInr(0)})
          </span>
        </p>
      </StatusTile>
    </div>
  );
}
