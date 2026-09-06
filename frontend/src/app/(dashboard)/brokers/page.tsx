import type { Metadata } from 'next';
import { BrokerAccountsView } from '@/features/brokers/components/BrokerAccountsView';

export const metadata: Metadata = {
  title: 'Broker Integrations',
};

/**
 * Broker Integrations screen.
 *
 * Lives under the terminal chrome (banner + header + sidebar) supplied by the
 * (dashboard) layout, and mounts the interactive broker-selection panel. Only
 * the panel is a client component; this route shell is a server component and
 * carries the `id="main"` skip-link target.
 */
export default function BrokersPage() {
  return (
    <main
      id="main"
      className="flex-1 overflow-y-auto bg-obsidian px-4 py-7 sm:px-8"
    >
      <div className="mx-auto max-w-[1100px]">
        <BrokerAccountsView />
      </div>
    </main>
  );
}
