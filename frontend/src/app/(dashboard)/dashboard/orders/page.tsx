import type { Metadata } from 'next';
import { PageContainer } from '@/features/dashboard/components/PageContainer';
import { OrderLedgerView } from '@/features/dashboard/components/OrderLedgerView';
import { PanicButton } from '@/features/dashboard/components/PanicButton';

export const metadata: Metadata = {
  title: 'Order Ledger',
};

/**
 * Order Ledger — the full execution log & audit trail. A telemetry ribbon,
 * client-side filters, and a dense audit table, all derived from the live
 * orders feed.
 */
export default function OrderLedgerPage() {
  return (
    <>
      <PageContainer
        title="Order Ledger"
        subtitle="Institutional order execution log & audit trail, routed via the connected broker."
      >
        <OrderLedgerView />
      </PageContainer>
      <PanicButton />
    </>
  );
}
