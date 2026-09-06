import type { Metadata } from 'next';
import { PageContainer } from '@/features/dashboard/components/PageContainer';
import { StrategiesPanel } from '@/features/dashboard/components/StrategiesPanel';
import { PanicButton } from '@/features/dashboard/components/PanicButton';

export const metadata: Metadata = {
  title: 'Strategy Matrix',
};

/**
 * Strategy Matrix — the full-page view of every algorithmic engine, with
 * start/stop controls. Reuses the live StrategiesPanel (which fetches and
 * mutates real strategy state) rather than duplicating the table.
 */
export default function StrategyMatrixPage() {
  return (
    <>
      <PageContainer
        title="Strategy Matrix"
        subtitle="Every algorithmic engine, its live status, and attributed P&L."
      >
        <StrategiesPanel className="w-full" />
      </PageContainer>
      <PanicButton />
    </>
  );
}
