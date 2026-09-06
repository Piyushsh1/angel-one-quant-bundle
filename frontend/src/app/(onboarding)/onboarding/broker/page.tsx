import type { Metadata } from 'next';
import { RouteGuard } from '@/features/auth/components/RouteGuard';
import { OnboardingShell } from '@/features/onboarding/components/OnboardingShell';
import { ConnectBrokerFlow } from '@/features/onboarding/components/ConnectBrokerFlow';

export const metadata: Metadata = {
  title: 'Connect your broker',
};

/**
 * Step 1 of onboarding — the destination the signup flow routes to.
 *
 * Guarded (but not `requireOnboarded`): a signed-in user who has NOT yet
 * connected a broker belongs here, so we only require a valid session. A
 * logged-out visitor is bounced to /login.
 */
export default function ConnectBrokerPage() {
  return (
    <RouteGuard>
      <OnboardingShell
        step="broker"
        title="Connect your broker"
        subtitle="Your trades execute in your own account. Your capital never leaves your broker."
      >
        <ConnectBrokerFlow />
      </OnboardingShell>
    </RouteGuard>
  );
}
