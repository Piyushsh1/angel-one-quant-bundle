'use client';

import { Panel, PanelHeader } from '@/features/dashboard/components/Panel';
import {
  TextField,
  Toggle,
  ControlRow,
} from '@/features/dashboard/components/rms/RmsControls';
import { useMetrics } from '@/features/dashboard/hooks/useDashboard';
import { formatInr } from '@/features/dashboard/lib/format';
import { cn } from '@/lib/cn';
import { isEdited, type RmsPanelProps } from '@/features/dashboard/components/rms/types';

/**
 * Margin Utilization & Overnight Exposure — the capital guardrails: the max
 * margin utilisation threshold (with a live current-usage read-out pulled from
 * the broker's RMS), the intraday auto square-off time, and the overnight naked
 * options carry freeze.
 */
export function MarginExposurePanel({ form, saved, setValue }: RmsPanelProps) {
  const { data: metrics } = useMetrics();

  // Live utilisation from the broker figures: used = total − available.
  const totalCapital = metrics?.totalCapital ?? 0;
  const availableMargin = metrics?.availableMargin ?? 0;
  const used = Math.max(totalCapital - availableMargin, 0);
  const currentPct =
    totalCapital > 0 ? Math.min((used / totalCapital) * 100, 100) : 0;

  const threshold = form.maxMarginUtilizationPct;
  const softWarn = Math.max(threshold - 15, 0);
  const overThreshold = currentPct >= threshold;
  const overWarn = currentPct >= softWarn;

  return (
    <Panel>
      <PanelHeader
        title="Margin Utilization & Overnight Exposure"
        iconClassName="border-brand-500/20 bg-brand-500/10 text-brand-400"
        icon={
          <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 3v18h18M9 17V9m4 8V5m4 12v-6" />
          </svg>
        }
        badge={
          <span
            className={cn(
              'rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider',
              overThreshold
                ? 'border-loss/30 bg-loss/10 text-loss'
                : overWarn
                  ? 'border-paper/30 bg-paper/10 text-paper'
                  : 'border-profit/25 bg-profit/10 text-profit',
            )}
          >
            {currentPct.toFixed(1)}% used
          </span>
        }
      />

      <div className="space-y-5">
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-medium text-slate-300">
              Max Margin Utilization Threshold
            </span>
            <span className="font-mono text-xs font-semibold text-white">
              {threshold}% (Warn at {softWarn}%)
              {isEdited(form, saved, 'maxMarginUtilizationPct') && (
                <span className="ml-1.5 font-mono text-[10px] uppercase text-brand-400">
                  edited
                </span>
              )}
            </span>
          </div>

          {/* Live usage bar against the configured threshold. */}
          <div className="relative mb-2 h-2 w-full overflow-hidden rounded-full bg-surface-800">
            <div
              className={cn(
                'h-full rounded-full transition-all',
                overThreshold
                  ? 'bg-loss'
                  : overWarn
                    ? 'bg-paper'
                    : 'bg-gradient-to-r from-profit-strong to-brand-400',
              )}
              style={{ width: `${currentPct}%` }}
            />
          </div>

          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={threshold}
            onChange={(e) =>
              setValue('maxMarginUtilizationPct', Number(e.target.value))
            }
            aria-label="Max margin utilisation threshold percent"
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-surface-700 accent-brand-400"
          />

          <div className="mt-1.5 flex items-center justify-between font-mono text-[10px] text-slate-400">
            <span>
              Current: {currentPct.toFixed(0)}%{' '}
              {totalCapital > 0 && `(${formatInr(used)})`}
            </span>
            <span>Soft warn: {softWarn}%</span>
            <span>Halt: {threshold}%</span>
          </div>
        </div>

        <div className="border-t border-surface-700/60 pt-3">
          <ControlRow
            title="Auto Square-Off Time (Intraday MIS)"
            description="Applied minutes prior to the exchange close (15:30 IST)."
            control={
              <div className="w-28">
                <TextField
                  label=""
                  type="time"
                  mono
                  value={form.autoSquareOffTime}
                  onChange={(v) => setValue('autoSquareOffTime', v)}
                  edited={isEdited(form, saved, 'autoSquareOffTime')}
                />
              </div>
            }
          />
        </div>

        <div className="border-t border-surface-700/60 pt-1">
          <ControlRow
            title="Overnight Options Carry Freeze"
            description="Disallows strategies from holding unhedged naked options overnight."
            control={
              <Toggle
                checked={form.overnightOptionsFreeze}
                onChange={(v) => setValue('overnightOptionsFreeze', v)}
                label="Freeze overnight naked options carry"
                edited={isEdited(form, saved, 'overnightOptionsFreeze')}
              />
            }
          />
        </div>
      </div>
    </Panel>
  );
}
