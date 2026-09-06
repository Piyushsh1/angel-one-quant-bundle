import type { Metadata } from 'next';
import { PageContainer } from '@/features/dashboard/components/PageContainer';
import { RmsWorkspace } from '@/features/dashboard/components/rms/RmsWorkspace';

export const metadata: Metadata = {
  title: 'Risk Management System',
};

/**
 * Risk Management System (RMS) & Parameters — algorithmic circuit breakers,
 * automated drawdown kill-switches, and broker execution limits.
 *
 * The guardrails the engine enforces, editable and persisted per user via
 * /dashboard/risk. Deliberately has no floating PanicButton: the master kill
 * switch lives on this page itself.
 */
export default function RiskParametersPage() {
  return (
    <PageContainer
      title="Risk Management System (RMS) & Parameters"
      subtitle="Algorithmic circuit breakers, automated drawdown kill-switches, and broker execution limits."
    >
      <RmsWorkspace />
    </PageContainer>
  );
}
