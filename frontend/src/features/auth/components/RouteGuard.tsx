'use client';

import { useEffect, type ReactNode } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useCurrentUser } from '@/features/auth/hooks/useCurrentUser';
import { destinationForUser, ROUTES } from '@/features/auth/lib/routing';
import { Spinner } from '@/components/ui/Spinner';

interface RouteGuardProps {
  children: ReactNode;
  /**
   * When true, the route requires the user to have finished onboarding
   * (broker + telegram). A signed-in but half-onboarded user is bounced to
   * their correct onboarding step instead of seeing the dashboard.
   */
  requireOnboarded?: boolean;
}

/**
 * Client-side gate for authenticated areas.
 *
 * Redirects:
 *   · no session          → /login
 *   · session, wrong step → the user's correct onboarding destination
 *
 * This is a UX guard, not a security boundary — the API independently rejects
 * unauthenticated requests. Its job is to stop a logged-out user from staring
 * at an empty dashboard shell, and to keep users moving through onboarding in
 * order.
 *
 * While the session is resolving it renders a spinner rather than the
 * children, so a protected page never flashes its contents before the redirect
 * decision is made.
 */
export function RouteGuard({ children, requireOnboarded = false }: RouteGuardProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { data: user, isPending, isError } = useCurrentUser();

  useEffect(() => {
    if (isPending) return;

    // Not signed in → send to login, remembering where they were headed.
    if (!user) {
      const next = encodeURIComponent(pathname);
      router.replace(`${ROUTES.login}?next=${next}`);
      return;
    }

    // Signed in but hasn't finished onboarding → push them to their step.
    // A FULLY-onboarded user may browse any protected page (dashboard and all
    // its sub-pages, brokers, etc.), so we only redirect when onboarding is
    // still incomplete — never just because the path isn't exactly /dashboard.
    if (requireOnboarded) {
      const onboardingComplete =
        user.hasBrokerConnected && user.hasTelegramLinked;
      if (!onboardingComplete) {
        const target = destinationForUser(user);
        if (target !== pathname) {
          router.replace(target);
        }
      }
    }
  }, [isPending, user, requireOnboarded, pathname, router]);

  // Backend unreachable: surface it rather than spinning forever.
  if (isError) {
    return (
      <div className="flex min-h-dvh items-center justify-center px-4 text-center">
        <div className="max-w-sm">
          <p className="text-sm font-semibold text-loss">Cannot reach the server</p>
          <p className="mt-1 text-xs text-slate-400">
            We could not verify your session. Check your connection and reload.
          </p>
        </div>
      </div>
    );
  }

  // Resolving, or about to redirect: hold the content back. Onboarded users
  // are allowed on any protected page, so only a still-in-onboarding user on
  // the wrong step (or a logged-out user) triggers the hold.
  const onboardingComplete =
    !!user && user.hasBrokerConnected && user.hasTelegramLinked;
  const willRedirect =
    !user ||
    (requireOnboarded &&
      !onboardingComplete &&
      destinationForUser(user) !== pathname);
  if (isPending || willRedirect) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner className="h-6 w-6 text-brand-400" label="Loading your session" />
      </div>
    );
  }

  return <>{children}</>;
}
