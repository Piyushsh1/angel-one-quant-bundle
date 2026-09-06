'use client';

import { Panel, PanelHeader } from '@/features/dashboard/components/Panel';
import {
  NumberField,
  SliderField,
} from '@/features/dashboard/components/rms/RmsControls';
import { isEdited, type RmsPanelProps } from '@/features/dashboard/components/rms/types';

/**
 * Order Execution & Slippage Controls — the OMS-level filters: slippage
 * tolerance (market orders convert to limit at this offset), the order-rate
 * limiter that stops a runaway loop, and the fat-finger quantity caps that hard
 * block an oversized order before it reaches the exchange.
 */
export function ExecutionControlsPanel({ form, saved, setValue }: RmsPanelProps) {
  return (
    <Panel>
      <PanelHeader
        title="Order Execution & Slippage Controls"
        iconClassName="border-brand-500/20 bg-brand-500/10 text-brand-400"
        icon={
          <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
        }
        badge={
          <span className="rounded border border-brand-500/25 bg-brand-500/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-brand-400">
            OMS Filter
          </span>
        }
      />

      <div className="space-y-5">
        <SliderField
          label="Max Slippage Tolerance (%)"
          rightLabel="Auto reject if executed > limit"
          value={form.maxSlippagePct}
          onChange={(v) => setValue('maxSlippagePct', Number(v.toFixed(2)))}
          min={0}
          max={2}
          step={0.05}
          format={(v) => `${v.toFixed(2)}%`}
          edited={isEdited(form, saved, 'maxSlippagePct')}
          hint="Market orders convert to limit orders with this offset to avoid liquidity traps."
        />

        <NumberField
          label="Order Rate Limiter"
          hint="Prevents runaway programmatic execution loops."
          value={form.orderRateLimitPerSec}
          onChange={(v) => setValue('orderRateLimitPerSec', Math.round(v))}
          edited={isEdited(form, saved, 'orderRateLimitPerSec')}
          min={1}
          max={1000}
          step={1}
          trailing="orders / sec"
        />

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-medium text-slate-300">
              Fat-Finger Max Quantity Cap
            </span>
            <span className="font-mono text-[10px] uppercase tracking-wider text-loss">
              BSE/NSE Hard Block
            </span>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <NumberField
              label="Lots"
              value={form.fatFingerMaxLots}
              onChange={(v) => setValue('fatFingerMaxLots', Math.round(v))}
              edited={isEdited(form, saved, 'fatFingerMaxLots')}
              min={1}
              max={1_000_000}
              step={1}
              trailing="LOTS"
            />
            <NumberField
              label="Shares"
              value={form.fatFingerMaxShares}
              onChange={(v) => setValue('fatFingerMaxShares', Math.round(v))}
              edited={isEdited(form, saved, 'fatFingerMaxShares')}
              min={1}
              max={100_000_000}
              step={1}
              trailing="SHARES"
            />
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-surface-700/60 pt-4">
          <div>
            <p className="text-xs font-semibold text-slate-200">
              Execution Desk Architecture
            </p>
            <p className="mt-0.5 text-[11px] text-slate-500">
              Direct Market Access protocol
            </p>
          </div>
          <span className="rounded-md border border-surface-700 bg-surface-900 px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-300">
            Direct FIX / REST DMA
          </span>
        </div>
      </div>
    </Panel>
  );
}
