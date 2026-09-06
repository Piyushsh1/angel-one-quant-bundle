'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { connectBroker } from '@/features/onboarding/api/broker.api';
import { authKeys } from '@/features/auth/hooks/authKeys';
import { ROUTES } from '@/features/auth/lib/routing';

/**
 * Connect + verify a broker account.
 *
 * On success the user's onboarding advances, so the cached current-user is
 * invalidated (its `hasBrokerConnected` flag is now stale) and the user is
 * routed to the next step. Not retried automatically: a failed broker login is
 * a definite answer, and silently retrying a credential submission is the wrong
 * default.
 */
interface UseConnectBrokerOptions {
  /**
   * What to do after a successful connect. Defaults to advancing onboarding
   * (route to the Telegram step). The Broker Integrations page passes its own
   * handler to stay on the page and refresh the accounts list instead.
   */
  onConnected?: () => void;
}

export function useConnectBroker(options: UseConnectBrokerOptions = {}) {
  const router = useRouter();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: connectBroker,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: authKeys.currentUser() });
      if (options.onConnected) {
        options.onConnected();
        return;
      }
      router.replace(ROUTES.linkTelegram);
    },
    retry: false,
  });
}
