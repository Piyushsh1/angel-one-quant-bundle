import type { Metadata } from 'next';
import { PageContainer } from '@/features/dashboard/components/PageContainer';
import {
  ReportsView,
  ReportsExportActions,
} from '@/features/dashboard/components/ReportsView';
import { PanicButton } from '@/features/dashboard/components/PanicButton';

export const metadata: Metadata = {
  title: 'Quant Reports & Alpha Telemetry',
};

/**
 * Quant Reports & Alpha Telemetry — performance attribution, trade
 * distribution and historical drawdowns, computed server-side from the user's
 * own strategies and orders. P&L is net of modelled charges, so the numbers are
 * comparable to a real brokerage statement.
 */
export default function QuantReportsPage() {
  return (
    <>
      <PageContainer
        title="Quant Reports & Alpha Telemetry"
        subtitle="Algorithmic performance attribution, trade distribution & historical drawdowns."
        actions={<ReportsExportActions />}
      >
        <ReportsView />
      </PageContainer>
      <PanicButton />
    </>
  );
}
