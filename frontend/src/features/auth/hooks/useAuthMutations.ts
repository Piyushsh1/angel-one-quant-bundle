'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import type { UseFormSetError } from 'react-hook-form';
import * as authApi from '@/features/auth/api/auth.api';
import type {
  LoginInput,
  SignupInput,
} from '@/features/auth/schemas/auth.schema';
import { ApiError } from '@/lib/errors';
import { authKeys } from '@/features/auth/hooks/authKeys';
import { pathForNextStep, type NextStep } from '@/features/auth/lib/routing';

/**
 * Map server-reported field errors onto the form.
 *
 * Lets the backend own rules the client cannot know — "this email is already
 * registered" — and still have them appear beside the right input rather than
 * as a generic banner.
 *
 * Returns true when at least one error was attached, so the caller can decide
 * whether a form-level banner is still needed.
 */
function applyFieldErrors<TFieldValues extends Record<string, unknown>>(
  error: unknown,
  setError: UseFormSetError<TFieldValues>,
  knownFields: readonly string[],
): boolean {
  if (!(error instanceof ApiError)) return false;

  let attached = false;
  for (const [field, messages] of Object.entries(error.fieldErrors)) {
    const message = messages[0];
    if (!message) continue;
    if (!knownFields.includes(field)) continue;
    // Cast is contained here: we have verified the field exists on the form.
    setError(field as Parameters<UseFormSetError<TFieldValues>>[0], {
      type: 'server',
      message,
    });
    attached = true;
  }
  return attached;
}

const LOGIN_FIELDS = ['email', 'password'] as const;
const SIGNUP_FIELDS = [
  'fullName',
  'email',
  'password',
  'confirmPassword',
] as const;

export function useLogin(setError: UseFormSetError<LoginInput>) {
  const router = useRouter();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: LoginInput) => authApi.login(input),
    onSuccess: (data) => {
      // Seed the cache so the destination route renders without a refetch.
      queryClient.setQueryData(authKeys.currentUser(), data.user);
      router.replace(pathForNextStep(data.nextStep as NextStep));
    },
    onError: (error) => {
      applyFieldErrors(error, setError, LOGIN_FIELDS);
    },
    // Credentials must never be retried automatically: a retry against a
    // rate-limited or lockout-protected endpoint makes the situation worse.
    retry: false,
  });
}

export function useSignup(setError: UseFormSetError<SignupInput>) {
  const router = useRouter();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: SignupInput) => authApi.signup(input),
    onSuccess: (data) => {
      queryClient.setQueryData(authKeys.currentUser(), data.user);
      router.replace(pathForNextStep(data.nextStep as NextStep));
    },
    onError: (error) => {
      applyFieldErrors(error, setError, SIGNUP_FIELDS);
    },
    retry: false,
  });
}

export function useLogout() {
  const router = useRouter();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: authApi.logout,
    onSettled: () => {
      // Clear everything: cached positions or P&L must not survive a sign-out.
      queryClient.clear();
      router.replace('/login');
    },
  });
}
