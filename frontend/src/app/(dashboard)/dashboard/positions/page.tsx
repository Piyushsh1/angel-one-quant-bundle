import type { Metadata } from 'next';
import { PageContainer } from '@/features/dashboard/components/PageContainer';
import { NetPositionsView } from '@/features/dashboard/components/NetPositionsView';
import { PanicButton } from '@/features/dashboard/components/PanicButton';

export const metadata: Metadata = {
  title: 'Net Positions',
};

/**
 * Net Positions — the full derivative-risk view: KPI tiles, a dense filterable
 * positions table with per-row square-off, and an aggregate footer. The
 * kill-switch is always available bottom-right.
 */
export default function NetPositionsPage() {
  return (
    <>
      <PageContainer
        title="Net Positions"
        subtitle="Real-time MTM & risk matrix. Close individually or use the kill-switch."
      >
        <NetPositionsView />
      </PageContainer>
      <PanicButton />
    </>
  );
}
