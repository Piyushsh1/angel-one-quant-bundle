import { api } from '@/lib/api-client';
import {
  authResponseSchema,
  userSchema,
  type AuthResponse,
  type LoginInput,
  type SignupInput,
  type User,
} from '@/features/auth/schemas/auth.schema';
import { z } from 'zod';

/**
 * Auth data layer.
 *
 * Pure functions with no React coupling, so they are trivially unit-testable
 * and reusable from a server action or a script. React Query wiring lives in
 * the hooks layer, deliberately separate.
 *
 * On success the server sets an httpOnly, Secure, SameSite session cookie. No
 * token is ever returned to or stored by JavaScript.
 */

const ENDPOINTS = {
  login: '/auth/login',
  signup: '/auth/signup',
  logout: '/auth/logout',
  me: '/auth/me',
} as const;

export async function login(
  input: LoginInput,
  signal?: AbortSignal,
): Promise<AuthResponse> {
  return api.post(ENDPOINTS.login, {
    body: {
      email: input.email,
      password: input.password,
      rememberDevice: input.rememberDevice,
    },
    schema: authResponseSchema,
    signal,
  });
}

export async function signup(
  input: SignupInput,
  signal?: AbortSignal,
): Promise<AuthResponse> {
  return api.post(ENDPOINTS.signup, {
    // confirmPassword is a client-side concern only — never transmitted.
    body: {
      fullName: input.fullName,
      email: input.email,
      password: input.password,
      acceptedTerms: input.acceptedTerms,
    },
    schema: authResponseSchema,
    signal,
  });
}

export async function logout(): Promise<void> {
  await api.post(ENDPOINTS.logout, { schema: z.unknown() });
}

/** Returns the signed-in user, or null when there is no valid session. */
export async function getCurrentUser(signal?: AbortSignal): Promise<User> {
  return api.get(ENDPOINTS.me, { schema: userSchema, signal });
}
