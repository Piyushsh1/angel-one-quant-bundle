'use client';

import { useQuery } from '@tanstack/react-query';
import { getCurrentUser } from '@/features/auth/api/auth.api';
import { authKeys } from '@/features/auth/hooks/authKeys';
import { ApiError } from '@/lib/errors';

/**
 * The signed-in user, or `null` when there is no valid session.
 *
 * A 401 is a normal state (logged out), not an error to retry — so it is
 * caught and mapped to `null`. Any other failure propagates so the caller can
 * distinguish "logged out" from "backend is down".
 */
export function useCurrentUser() {
  return useQuery({
    queryKey: authKeys.currentUser(),
    queryFn: async ({ signal }) => {
      try {
        return await getCurrentUser(signal);
      } catch (err) {
        if (err instanceof ApiError && err.kind === 'unauthenticated') {
          return null;
        }
        throw err;
      }
    },
    // The session rarely changes within a visit; avoid refetching on every
    // window focus, which would otherwise fire a /me on each tab switch.
    staleTime: 30_000,
    retry: false,
  });
}
