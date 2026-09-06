'use client';

import { Panel, PanelHeader } from '@/features/dashboard/components/Panel';
import {
  TextField,
  Toggle,
  ControlRow,
} from '@/features/dashboard/components/rms/RmsControls';
import { useCurrentUser } from '@/features/auth/hooks/useCurrentUser';
import { cn } from '@/lib/cn';
import { isEdited, type RmsPanelProps } from '@/features/dashboard/components/rms/types';

/**
 * Alerts, Webhooks & Notifications — where RMS events are dispatched: the
 * Telegram kill-switch channel (linked via onboarding), the SMS/webhook margin
 * broadcast, the execution-bell auditory telemetry, and a custom webhook URI
 * for the operator's own systems.
 */
export function AlertsPanel({ form, saved, setValue }: RmsPanelProps) {
  const { data: user } = useCurrentUser();
  const telegramLinked = user?.hasTelegramLinked ?? false;

  return (
    <Panel>
      <PanelHeader
        title="Alerts, Webhooks & Notifications"
        iconClassName="border-brand-500/20 bg-brand-500/10 text-brand-400"
        icon={
          <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 00-4-5.7V5a2 2 0 10-4 0v.3C7.7 6.2 6 8.4 6 11v3.2c0 .5-.2 1-.6 1.4L4 17h5m6 0v1a3 3 0 11-6 0v-1" />
          </svg>
        }
        badge={
          <span
            className={cn(
              'rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider',
              telegramLinked
                ? 'border-profit/25 bg-profit/10 text-profit'
                : 'border-surface-700 bg-surface-800 text-slate-400',
            )}
          >
            {telegramLinked ? 'Telegram Linked' : 'Telegram Not Linked'}
          </span>
        }
      />

      <div className="divide-y divide-surface-700/60">
        <div className="flex items-start justify-between gap-4 pb-3">
          <div>
            <p className="text-xs font-semibold text-slate-200">
              Telegram Kill-Switch Channel
            </p>
            <p className="mt-0.5 text-[11px] text-slate-500">
              {telegramLinked
                ? 'Linked — high-priority RMS events dispatch to your Telegram.'
                : 'Not linked — connect Telegram in onboarding to enable.'}
            </p>
          </div>
          <span
            className={cn(
              'shrink-0 rounded-md border px-2.5 py-1 font-mono text-[10px]',
              telegramLinked
                ? 'border-profit/25 bg-profit/10 text-profit'
                : 'border-surface-700 bg-surface-800 text-slate-400',
            )}
          >
            {telegramLinked ? 'Active' : 'Not linked'}
          </span>
        </div>

        <ControlRow
          title="SMS & Webhook Margin Call Broadcast"
          description="Dispatches high-priority payload to operator phones."
          control={
            <Toggle
              checked={form.smsWebhookAlertsEnabled}
              onChange={(v) => setValue('smsWebhookAlertsEnabled', v)}
              label="Enable SMS and webhook margin call broadcast"
              edited={isEdited(form, saved, 'smsWebhookAlertsEnabled')}
            />
          }
        />

        <ControlRow
          title="Auditory Telemetry (Execution Bell)"
          description="Synthesizer tone for filled, cancelled, or rejected orders."
          control={
            <Toggle
              checked={form.auditoryTelemetryEnabled}
              onChange={(v) => setValue('auditoryTelemetryEnabled', v)}
              label="Enable execution-bell auditory telemetry"
              edited={isEdited(form, saved, 'auditoryTelemetryEnabled')}
            />
          }
        />

        <div className="pt-4">
          <TextField
            label="Desk Custom Webhook Dispatch URI"
            type="url"
            mono
            placeholder="https://api.your-desk.internal/v1/rms/circuit-breaker"
            value={form.customWebhookUri ?? ''}
            onChange={(v) => setValue('customWebhookUri', v)}
            edited={isEdited(form, saved, 'customWebhookUri')}
          />
        </div>
      </div>
    </Panel>
  );
}
