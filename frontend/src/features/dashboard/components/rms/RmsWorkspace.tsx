'use client';

import { useEffect, useMemo, useState } from 'react';
import { Panel } from '@/features/dashboard/components/Panel';
import { Button } from '@/components/ui/Button';
import { Alert } from '@/components/ui/Alert';
import {
  useRiskParameters,
  useUpdateRiskParameters,
} from '@/features/dashboard/hooks/useDashboard';
import type { RiskParameters } from '@/features/dashboard/schemas/dashboard.schema';
import { userFacingMessage } from '@/lib/errors';
import { cn } from '@/lib/cn';

import { RmsStatusStrip } from '@/features/dashboard/components/rms/RmsStatusStrip';
import { IntradayLimitsPanel } from '@/features/dashboard/components/rms/IntradayLimitsPanel';
import { ExecutionControlsPanel } from '@/features/dashboard/components/rms/ExecutionControlsPanel';
import { MarginExposurePanel } from '@/features/dashboard/components/rms/MarginExposurePanel';
import { AlertsPanel } from '@/features/dashboard/components/rms/AlertsPanel';

/**
 * Conservative desk defaults used by "Restore Desk Presets". These are the
 * institutional starting guardrails; the kill switch is deliberately excluded
 * so restoring presets never silently disarms a halted engine.
 */
const DESK_PRESETS: Omit<
  RiskParameters,
  'killSwitchArmed' | 'liveTradingEnabled' | 'autoTradingEnabled'
> = {
  maxDailyLoss: 10_000,
  maxPositionSize: 182_484,
  maxOpenPositions: 6,
  defaultStopLossPct: 2,
  defaultTargetPct: 4,
  maxMarginUtilizationPct: 85,
  maxTrailingDrawdownPct: 25,
  consecutiveLossLimit: 3,
  consecutiveLossBreakerEnabled: true,
  maxSlippagePct: 0.15,
  orderRateLimitPerSec: 10,
  fatFingerMaxLots: 200,
  fatFingerMaxShares: 10_000,
  autoSquareOffTime: '15:15',
  overnightOptionsFreeze: true,
  smsWebhookAlertsEnabled: true,
  auditoryTelemetryEnabled: true,
  customWebhookUri: '',
};

/**
 * The Risk Management System workspace.
 *
 * Owns the edit buffer for every RMS guardrail and the save/deploy lifecycle.
 * Edits are dirty-tracked field-by-field; "Save & Deploy" PATCHes only what
 * changed, so an operator's untouched limits are never rewritten. The master
 * kill switch is a separate, immediate action — it does not wait for a deploy.
 */
export function RmsWorkspace() {
  const { data, isLoading, isError } = useRiskParameters();
  const update = useUpdateRiskParameters();

  const [form, setForm] = useState<RiskParameters | null>(null);

  // Seed the buffer once the server value arrives.
  useEffect(() => {
    if (data && form === null) setForm(data);
  }, [data, form]);

  const dirtyKeys = useMemo(() => {
    if (!form || !data) return [] as (keyof RiskParameters)[];
    return (Object.keys(form) as (keyof RiskParameters)[]).filter(
      (k) => form[k] !== data[k],
    );
  }, [form, data]);

  // The kill switch, paper/live switch, and auto-trading switch are deployed
  // immediately, so they never count as pending "unsaved changes".
  const pendingKeys = dirtyKeys.filter(
    (k) =>
      k !== 'killSwitchArmed' &&
      k !== 'liveTradingEnabled' &&
      k !== 'autoTradingEnabled',
  );
  const isDirty = pendingKeys.length > 0;

  if (isLoading || !form) {
    return (
      <Panel>
        <p className="py-10 text-center text-xs text-slate-400">
          {isError
            ? 'Failed to load risk parameters.'
            : 'Loading risk management system…'}
        </p>
      </Panel>
    );
  }

  function setValue<K extends keyof RiskParameters>(
    key: K,
    value: RiskParameters[K],
  ) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function deploy() {
    if (!form || !data || !isDirty) return;
    const changes = Object.fromEntries(pendingKeys.map((k) => [k, form[k]]));
    update.mutate(changes, { onSuccess: (fresh) => setForm(fresh) });
  }

  function restorePresets() {
    setForm((prev) => (prev ? { ...prev, ...DESK_PRESETS } : prev));
  }

  function discard() {
    if (data) setForm(data);
  }

  function toggleKillSwitch() {
    update.mutate(
      { killSwitchArmed: !form!.killSwitchArmed },
      { onSuccess: (fresh) => setForm((prev) => (prev ? { ...prev, ...fresh } : fresh)) },
    );
  }

  function toggleLiveTrading() {
    const enabling = !form!.liveTradingEnabled;
    // Turning LIVE on places real orders with real money — require an explicit
    // confirmation. Turning it back to paper is always safe, no prompt.
    if (enabling) {
      const ok = window.confirm(
        'Enable LIVE trading?\n\n' +
          'Orders will be placed on your real broker account with real money. ' +
          'Until you enable this, every order is simulated (paper). ' +
          'Are you sure you want to go live?',
      );
      if (!ok) return;
    }
    update.mutate(
      { liveTradingEnabled: enabling },
      { onSuccess: (fresh) => setForm((prev) => (prev ? { ...prev, ...fresh } : fresh)) },
    );
  }

  function toggleAutoTrading() {
    const turningOn = !form!.autoTradingEnabled;
    if (turningOn) {
      const modeNote = form!.liveTradingEnabled
        ? 'You are in LIVE mode — the engine will place REAL orders on your broker account with no per-trade approval.'
        : 'You are in PAPER mode — the engine will place simulated orders only. Switch to Live when you are ready for real trades.';
      const ok = window.confirm(
        'Start automated trading?\n\n' +
          'The engine will evaluate the strategy and open trades on its own ' +
          '(one position at a time). ' +
          modeNote +
          '\n\nProceed?',
      );
      if (!ok) return;
    }
    update.mutate(
      { autoTradingEnabled: turningOn },
      { onSuccess: (fresh) => setForm((prev) => (prev ? { ...prev, ...fresh } : fresh)) },
    );
  }

  return (
    <div className="space-y-4">
      {/* Header actions row */}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {isDirty && (
          <span className="mr-auto flex items-center gap-1.5 font-mono text-[11px] text-paper">
            <span className="h-1.5 w-1.5 rounded-full bg-paper" />
            {pendingKeys.length} unsaved change{pendingKeys.length === 1 ? '' : 's'}
          </span>
        )}
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={restorePresets}
          disabled={update.isPending}
        >
          Restore Desk Presets
        </Button>
        {isDirty && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={discard}
            disabled={update.isPending}
          >
            Discard
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          onClick={deploy}
          isLoading={update.isPending && isDirty}
          loadingText="Deploying…"
          disabled={!isDirty}
        >
          Save &amp; Deploy Changes
        </Button>
      </div>

      {update.isError && (
        <Alert tone="error" title="Could not deploy">
          {userFacingMessage(update.error)}
        </Alert>
      )}
      {update.isSuccess && !isDirty && (
        <Alert tone="success">Risk parameters deployed.</Alert>
      )}

      <RmsStatusStrip form={form} />

      {/* Trading mode — paper (default) vs live. Applied instantly. Enabling
          live requires explicit confirmation; the whole platform defaults to
          paper so no real order can go out until this is deliberately on. */}
      <Panel
        className={cn(
          form.liveTradingEnabled ? 'border-loss/50' : 'border-paper/40',
        )}
      >
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-bold text-slate-100">
              <span
                className={cn(
                  'h-2 w-2 rounded-full',
                  form.liveTradingEnabled ? 'animate-pulse bg-loss' : 'bg-paper',
                )}
              />
              Trading Mode:{' '}
              <span
                className={
                  form.liveTradingEnabled ? 'text-loss' : 'text-paper'
                }
              >
                {form.liveTradingEnabled ? 'LIVE' : 'PAPER'}
              </span>
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              {form.liveTradingEnabled
                ? 'LIVE — orders are placed on your real broker account with real money. Applied instantly.'
                : 'PAPER — every order is simulated and never sent to the broker. This is the safe default. Turn on live trading only when you are ready to trade real money.'}
            </p>
          </div>
          <Button
            type="button"
            variant={form.liveTradingEnabled ? 'secondary' : 'danger'}
            onClick={toggleLiveTrading}
            isLoading={update.isPending && !isDirty}
          >
            {form.liveTradingEnabled ? 'Switch to Paper' : 'Enable Live Trading'}
          </Button>
        </div>
      </Panel>

      {/* Auto-trading engine — runs the strategy in the background and opens
          trades on its own (one at a time). Independent of paper/live: this
          decides WHETHER the engine runs; the mode above decides whether its
          orders are real. Applied instantly. */}
      <Panel
        className={cn(
          form.autoTradingEnabled ? 'border-profit/50' : 'border-surface-700/70',
        )}
      >
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-bold text-slate-100">
              <span
                className={cn(
                  'h-2 w-2 rounded-full',
                  form.autoTradingEnabled ? 'animate-pulse bg-profit' : 'bg-slate-500',
                )}
              />
              Auto-Trading Engine:{' '}
              <span className={form.autoTradingEnabled ? 'text-profit' : 'text-slate-400'}>
                {form.autoTradingEnabled ? 'RUNNING' : 'STOPPED'}
              </span>
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              {form.autoTradingEnabled
                ? `Running — the engine evaluates the strategy every cycle and opens trades automatically (one position at a time), placing ${
                    form.liveTradingEnabled ? 'REAL' : 'simulated (paper)'
                  } orders. Applied instantly.`
                : 'Stopped — no automatic trades. Turn on to let the engine trade on its own within your risk limits and current mode.'}
            </p>
          </div>
          <Button
            type="button"
            variant={form.autoTradingEnabled ? 'secondary' : 'primary'}
            onClick={toggleAutoTrading}
            isLoading={update.isPending && !isDirty}
          >
            {form.autoTradingEnabled ? 'Stop Auto-Trading' : 'Start Auto-Trading'}
          </Button>
        </div>
      </Panel>

      {/* Two-column control grid, mirroring the desk layout. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <IntradayLimitsPanel form={form} saved={data ?? null} setValue={setValue} />
        <ExecutionControlsPanel form={form} saved={data ?? null} setValue={setValue} />
        <MarginExposurePanel form={form} saved={data ?? null} setValue={setValue} />
        <AlertsPanel form={form} saved={data ?? null} setValue={setValue} />
      </div>

      {/* Master kill switch — deployed immediately, separated from staged edits. */}
      <Panel className={cn(form.killSwitchArmed && 'border-loss/50')}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-bold text-slate-100">
              <span
                className={cn(
                  'h-2 w-2 rounded-full',
                  form.killSwitchArmed ? 'bg-loss' : 'bg-profit',
                )}
              />
              Master Kill Switch
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              {form.killSwitchArmed
                ? 'ARMED — the engine is refusing all new entries. Existing positions are still managed. Applied instantly, no deploy required.'
                : 'Disarmed — strategies may open new positions within the limits above. Arming applies instantly.'}
            </p>
          </div>
          <Button
            type="button"
            variant={form.killSwitchArmed ? 'secondary' : 'danger'}
            onClick={toggleKillSwitch}
            isLoading={update.isPending && !isDirty}
          >
            {form.killSwitchArmed ? 'Disarm' : 'Arm Kill Switch'}
          </Button>
        </div>
      </Panel>
    </div>
  );
}
