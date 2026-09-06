import type { Metadata } from 'next';
import { RouteGuard } from '@/features/auth/components/RouteGuard';
import { OnboardingShell } from '@/features/onboarding/components/OnboardingShell';
import { LinkTelegramFlow } from '@/features/onboarding/components/LinkTelegramFlow';

export const metadata: Metadata = {
  title: 'Link Telegram',
};

/**
 * Step 2 of onboarding — trade notifications on Telegram.
 *
 * Reachable only after a broker is connected; the guard sends a user who has
 * not connected a broker back to step 1.
 */
export default function LinkTelegramPage() {
  return (
    <RouteGuard>
      <OnboardingShell
        step="telegram"
        title="Get every trade on Telegram"
        subtitle="In Auto mode we place trades for you — Telegram is how you stay informed of every action."
      >
        <LinkTelegramFlow />
      </OnboardingShell>
    </RouteGuard>
  );
}
