import type { ReactNode } from 'react';
import { RouteGuard } from '@/features/auth/components/RouteGuard';
import { SimulationBanner } from '@/features/dashboard/components/SimulationBanner';
import { DashboardHeader } from '@/features/dashboard/components/DashboardHeader';
import { Sidebar } from '@/features/dashboard/components/Sidebar';
import { DashboardDateProvider } from '@/features/dashboard/hooks/useDashboardDate';

/**
 * Chrome for the trading terminal: the paper-mode banner, the global header,
 * and the navigation rail. A server component — none of this chrome is
 * interactive beyond plain links, so it ships no client JavaScript.
 *
 * LAYOUT CONTRACT: the whole app is exactly one viewport tall and never
 * scrolls at the page level. The banner and header are fixed-height rows; the
 * body row (`min-h-0 flex-1`) fills the rest and owns the only scroll region,
 * so the header and sidebar stay pinned while the dashboard scrolls beneath.
 */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    // requireOnboarded: a signed-in user who has NOT finished onboarding is
    // redirected to their correct step, so the dashboard never renders for a
    // user who has no broker connected. Logged-out users go to /login.
    <RouteGuard requireOnboarded>
      <DashboardDateProvider>
        <div className="flex h-dvh flex-col bg-obsidian text-slate-200 antialiased">
          <SimulationBanner />
          <DashboardHeader />
          <div className="flex min-h-0 flex-1 overflow-hidden">
            <Sidebar />
            {children}
          </div>
        </div>
      </DashboardDateProvider>
    </RouteGuard>
  );
}
