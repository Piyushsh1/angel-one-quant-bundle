'use client';

import { useState } from 'react';
import type { BrokerId } from '@/config/brokers';
import { BrokerPicker } from '@/features/onboarding/components/BrokerPicker';
import { BrokerCredentialsForm } from '@/features/onboarding/components/BrokerCredentialsForm';

/**
 * Two-phase broker connection: pick a broker, then enter its credentials.
 *
 * The phase is local UI state (not a route), so "Change broker" is instant and
 * the browser back button stays reserved for leaving onboarding entirely.
 */
export function ConnectBrokerFlow({
  onConnected,
}: {
  /** Optional: overrides the default onboarding-advance behavior on success. */
  onConnected?: () => void;
} = {}) {
  const [brokerId, setBrokerId] = useState<BrokerId | null>(null);

  if (brokerId) {
    return (
      <BrokerCredentialsForm
        brokerId={brokerId}
        onBack={() => setBrokerId(null)}
        onConnected={onConnected}
      />
    );
  }

  return (
    <div className="space-y-4">
      <BrokerPicker selected={brokerId} onSelect={setBrokerId} />
      <p className="text-xs text-slate-500">
        Select a broker to continue. Your capital stays in your own account —
        Barbell only places the trades.
      </p>
    </div>
  );
}
