'use client';

import { Panel, PanelHeader } from '@/features/dashboard/components/Panel';
import {
  NumberField,
  StepperPill,
  Toggle,
  ControlRow,
} from '@/features/dashboard/components/rms/RmsControls';
import { formatInr } from '@/features/dashboard/lib/format';
import { isEdited, type RmsPanelProps } from '@/features/dashboard/components/rms/types';

/**
 * Intraday Loss & Drawdown Limits — the terminal-level hard cutoffs: max daily
 * loss (squares off and freezes), trailing profit drawdown, and the
 * consecutive-loss circuit breaker. These are the guardrails that stop a bad
 * session from compounding.
 */
export function IntradayLimitsPanel({ form, saved, setValue }: RmsPanelProps) {
  const breakerOn = form.consecutiveLossBreakerEnabled;

  return (
    <Panel>
      <PanelHeader
        title="Intraday Loss & Drawdown Limits"
        iconClassName="border-loss/20 bg-loss/10 text-loss"
        icon={
          <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 17h8m0 0V9m0 8l-8-8-4 4-6-6" />
          </svg>
        }
        badge={
          <span className="rounded border border-loss/25 bg-loss/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-loss">
            Real-Time Hook
          </span>
        }
      />

      <div className="space-y-5">
        <NumberField
          label="Max Daily Loss Limit (INR)"
          hint="Trading halts and the terminal squares off once the day's realised loss reaches this."
          value={form.maxDailyLoss}
          onChange={(v) => setValue('maxDailyLoss', v)}
          edited={isEdited(form, saved, 'maxDailyLoss')}
          min={0}
          max={10_000_000}
          step={500}
          unit="₹"
          unitPosition="prefix"
        />

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-medium text-slate-300">
              Max Trailing Profit Drawdown
            </span>
            <span className="font-mono text-xs font-semibold text-brand-400">
              {form.maxTrailingDrawdownPct}% from peak MTM
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={form.maxTrailingDrawdownPct}
            onChange={(e) => setValue('maxTrailingDrawdownPct', Number(e.target.value))}
            aria-label="Max trailing profit drawdown percent"
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-surface-700 accent-profit-strong"
          />
          <p className="mt-1.5 text-[11px] text-slate-500">
            Protects intraday gains once MTM crosses a peak. The trailing floor
            activates automatically.
            {isEdited(form, saved, 'maxTrailingDrawdownPct') && (
              <span className="ml-1.5 font-mono text-[10px] uppercase text-brand-400">
                edited
              </span>
            )}
          </p>
        </div>

        <div className="border-t border-surface-700/60 pt-1">
          <ControlRow
            title="Consecutive Loss Circuit Breaker"
            description="Trigger a halt after this many consecutive losing trades."
            control={
              <div className="flex items-center gap-3">
                <StepperPill
                  value={form.consecutiveLossLimit}
                  onChange={(v) => setValue('consecutiveLossLimit', v)}
                  suffix="Trades"
                  min={1}
                  max={20}
                  edited={isEdited(form, saved, 'consecutiveLossLimit')}
                />
                <Toggle
                  checked={breakerOn}
                  onChange={(v) => setValue('consecutiveLossBreakerEnabled', v)}
                  label="Enable consecutive-loss circuit breaker"
                  edited={isEdited(form, saved, 'consecutiveLossBreakerEnabled')}
                />
              </div>
            }
          />
        </div>

        <div className="border-t border-surface-700/60 pt-3">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium text-slate-300">
              Per-Strategy Capital Allocation Cap
            </span>
            <span className="font-mono text-[10px] uppercase tracking-wider text-brand-400">
              Portfolio Margin
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-surface-700/80 bg-surface-900/90 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">
                Max Strategy Exposure
              </p>
              <p className="font-mono text-sm font-semibold text-white">
                {formatInr(form.maxPositionSize)}
              </p>
            </div>
            <div className="rounded-lg border border-surface-700/80 bg-surface-900/90 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">
                Max Open Positions
              </p>
              <p className="font-mono text-sm font-semibold text-white">
                {form.maxOpenPositions} Allowed
              </p>
            </div>
          </div>
          <p className="mt-1.5 text-[11px] text-slate-500">
            Governed by Max Position Size and Max Open Positions in your core limits.
          </p>
        </div>
      </div>
    </Panel>
  );
}
