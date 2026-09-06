import type { Metadata } from 'next';
import { HeroSection } from '@/features/dashboard/components/HeroSection';
import { MetricCards } from '@/features/dashboard/components/MetricCards';
import { IntradayPnlPanel } from '@/features/dashboard/components/IntradayPnlPanel';
import { OpenPositionsPanel } from '@/features/dashboard/components/OpenPositionsPanel';
import { StrategiesPanel } from '@/features/dashboard/components/StrategiesPanel';
import { OrderLedgerPanel } from '@/features/dashboard/components/OrderLedgerPanel';
import { MarketWatchlist } from '@/features/dashboard/components/MarketWatchlist';
import { PanicButton } from '@/features/dashboard/components/PanicButton';

export const metadata: Metadata = {
  title: 'Terminal Dashboard',
};

/**
 * The quant execution terminal.
 *
 * A server component that composes the dashboard from focused feature blocks.
 * Each panel is a client island that fetches its own slice from the backend via
 * React Query, so the page ships no data of its own and every widget stays live
 * on its own refresh cadence. `main` is the single scroll region (the
 * surrounding layout is fixed-height), and it carries the `id="main"` skip-link
 * target the root layout links to.
 */
export default function DashboardPage() {
  return (
    <>
      <main
        id="main"
        className="flex-1 space-y-5 overflow-y-auto bg-obsidian px-4 py-5 lg:px-6"
      >
        <HeroSection />
        <MetricCards />

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <IntradayPnlPanel />
          <OpenPositionsPanel />
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <StrategiesPanel />
          <OrderLedgerPanel />
        </div>

        <MarketWatchlist />

        {/* Breathing room so the floating panic button never overlaps the last
            row of content when scrolled to the bottom. */}
        <div className="h-10" />
      </main>

      <PanicButton />
    </>
  );
}
