import type { User } from '@/features/auth/schemas/auth.schema';

/**
 * Onboarding routing — the single source of truth for "where does this user
 * belong right now".
 *
 * Used by the login/signup mutations, the route guard, and the onboarding
 * pages so they can never disagree. The order matters: a user must connect a
 * broker before linking Telegram before reaching the dashboard.
 */
export const ROUTES = {
  login: '/login',
  signup: '/signup',
  connectBroker: '/onboarding/broker',
  linkTelegram: '/onboarding/telegram',
  dashboard: '/dashboard',
} as const;

export type NextStep = 'connect_broker' | 'link_telegram' | 'dashboard';

/** Map the server's nextStep enum to a concrete path. */
export function pathForNextStep(step: NextStep): string {
  switch (step) {
    case 'connect_broker':
      return ROUTES.connectBroker;
    case 'link_telegram':
      return ROUTES.linkTelegram;
    case 'dashboard':
      return ROUTES.dashboard;
  }
}

/**
 * Derive where an authenticated user should be, from their onboarding flags.
 * Mirrors the backend's `compute_next_step`, so the client can redirect
 * immediately without waiting for a fresh nextStep from the server.
 */
export function destinationForUser(user: User): string {
  if (!user.hasBrokerConnected) return ROUTES.connectBroker;
  if (!user.hasTelegramLinked) return ROUTES.linkTelegram;
  return ROUTES.dashboard;
}
