'use client';

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  useBrokerAccounts,
  useDisconnectBroker,
} from '@/features/dashboard/hooks/useDashboard';
import { dashboardKeys } from '@/features/dashboard/hooks/dashboardKeys';
import { ConnectBrokerFlow } from '@/features/onboarding/components/ConnectBrokerFlow';
import { BROKER_DISPLAY } from '@/features/brokers/data/display';
import type { BrokerId } from '@/config/brokers';
import { cn } from '@/lib/cn';

/**
 * Broker Integrations — the real, functional management screen.
 *
 * Lists the caller's genuinely-connected broker accounts (from the live
 * `/broker/accounts`, backed by verified BrokerConnection rows), lets them
 * disconnect one, and lets them connect another via the same real credential
 * flow used in onboarding (which performs an actual broker login). Nothing here
 * is cosmetic: every account shown was accepted by the broker's own API.
 */
export function BrokerAccountsView() {
  const { data: accounts, isLoading, isError } = useBrokerAccounts();
  const disconnect = useDisconnectBroker();
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);

  function onConnected() {
    setAdding(false);
    // Refresh the accounts list + terminal so the new connection shows live.
    queryClient.invalidateQueries({ queryKey: dashboardKeys.brokerAccounts() });
    queryClient.invalidateQueries({ queryKey: dashboardKeys.all });
  }

  const list = accounts ?? [];
  const hasAny = list.length > 0;

  function label(brokerId: string): string {
    return BROKER_DISPLAY[brokerId as BrokerId]?.apiLabel ?? brokerId;
  }

  function onDisconnect(id: string) {
    setPendingId(id);
    disconnect.mutate(id, { onSettled: () => setPendingId(null) });
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white">
            Broker Integrations
          </h1>
          <p className="mt-1 text-xs text-slate-400">
            Connect your own broker account. Your capital never leaves it —
            Barbell only places trades on your behalf.
          </p>
        </div>
        {hasAny && !adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="rounded-lg border border-brand-500/40 bg-brand-500/10 px-3 py-1.5 text-xs font-semibold text-brand-300 transition hover:bg-brand-500/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
          >
            + Connect another
          </button>
        )}
      </div>

      {/* Connected accounts */}
      {isLoading && (
        <p className="text-xs text-slate-400">Loading your broker connections…</p>
      )}
      {isError && !isLoading && (
        <p className="text-xs text-loss">Failed to load broker connections.</p>
      )}

      {!isLoading && !isError && hasAny && (
        <ul className="space-y-3">
          {list.map((acct) => (
            <li
              key={acct.id}
              className="flex items-center justify-between rounded-xl border border-surface-700/70 bg-surface-850 p-4"
            >
              <div className="flex items-center gap-3">
                <span
                  className={cn(
                    'flex h-9 w-9 items-center justify-center rounded-lg border text-xs font-bold',
                    acct.status === 'connected'
                      ? 'border-profit/30 bg-profit/10 text-profit'
                      : 'border-loss/30 bg-loss/10 text-loss',
                  )}
                >
                  {(acct.accountName ?? acct.brokerId).charAt(0).toUpperCase()}
                </span>
                <div>
                  <p className="text-sm font-semibold text-white">
                    {acct.accountName ?? label(acct.brokerId)}
                  </p>
                  <p className="font-mono text-[11px] text-slate-400">
                    {label(acct.brokerId)}
                    {acct.maskedClientId ? ` • ${acct.maskedClientId}` : ''}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-4">
                <span
                  className={cn(
                    'flex items-center gap-1.5 font-mono text-[11px]',
                    acct.status === 'connected' ? 'text-profit' : 'text-loss',
                  )}
                >
                  <span
                    className={cn(
                      'h-2 w-2 rounded-full',
                      acct.status === 'connected'
                        ? 'animate-pulse bg-profit'
                        : 'bg-loss',
                    )}
                  />
                  {acct.status === 'connected' ? 'Connected' : 'Error'}
                </span>
                <button
                  type="button"
                  onClick={() => onDisconnect(acct.id)}
                  disabled={pendingId === acct.id}
                  className="rounded-lg border border-loss/30 px-3 py-1.5 text-xs font-semibold text-loss transition hover:bg-loss/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-loss disabled:opacity-50"
                >
                  {pendingId === acct.id ? 'Disconnecting…' : 'Disconnect'}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Connect flow: shown when there are no accounts, or the user chose to
          add another. Uses the real credential flow (live broker login). */}
      {!isLoading && (adding || !hasAny) && (
        <div className="rounded-xl border border-surface-700/70 bg-surface-900 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white">
              {hasAny ? 'Connect another broker' : 'Connect your broker'}
            </h2>
            {hasAny && (
              <button
                type="button"
                onClick={() => setAdding(false)}
                className="text-xs text-slate-400 hover:text-slate-200 focus-visible:outline-none"
              >
                Cancel
              </button>
            )}
          </div>
          <ConnectBrokerFlow onConnected={onConnected} />
        </div>
      )}
    </div>
  );
}
