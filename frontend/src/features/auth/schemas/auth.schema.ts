import { z } from 'zod';

/**
 * Validation contracts for authentication.
 *
 * Zod is the single source of truth: form types are *inferred* from these
 * schemas, so a field can never drift out of sync with its validation. The same
 * rules should be enforced server-side — client validation is a UX
 * affordance, never a security boundary.
 */

const email = z
  .string()
  .trim()
  .min(1, 'Email is required')
  .email('Enter a valid email address')
  .max(254, 'Email is too long')
  .toLowerCase();

/**
 * Password policy.
 *
 * Length is weighted over character-class gymnastics, which is current NIST
 * guidance and produces stronger passwords in practice. 12 characters is the
 * floor because this credential ultimately guards an account that can place
 * orders.
 */
const strongPassword = z
  .string()
  .min(12, 'Use at least 12 characters')
  .max(128, 'Password is too long')
  .regex(/[a-z]/, 'Include a lowercase letter')
  .regex(/[A-Z]/, 'Include an uppercase letter')
  .regex(/\d/, 'Include a number');

export const loginSchema = z.object({
  email,
  // Sign-in only checks presence: rejecting a "weak" password here would
  // leak the policy and annoy users with legacy credentials.
  password: z.string().min(1, 'Password is required'),
  rememberDevice: z.boolean().default(false),
});

export const signupSchema = z
  .object({
    fullName: z
      .string()
      .trim()
      .min(2, 'Enter your full name')
      .max(80, 'Name is too long'),
    email,
    password: strongPassword,
    confirmPassword: z.string().min(1, 'Confirm your password'),
    acceptedTerms: z.literal(true, {
      errorMap: () => ({
        message: 'You must accept the terms and risk disclosure',
      }),
    }),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: 'Passwords do not match',
    path: ['confirmPassword'],
  });

export type LoginInput = z.infer<typeof loginSchema>;
export type SignupInput = z.infer<typeof signupSchema>;

// ── Response contracts ────────────────────────────────────────────────────
// Validated at the API boundary so a backend change surfaces as an explicit
// parse failure instead of an undefined field deep in a component.

export const userSchema = z.object({
  id: z.string(),
  email: z.string().email(),
  fullName: z.string(),
  /** Whether the user has connected at least one verified broker account. */
  hasBrokerConnected: z.boolean(),
  /** Whether Telegram notifications are linked. */
  hasTelegramLinked: z.boolean(),
  createdAt: z.string(),
});

export const authResponseSchema = z.object({
  user: userSchema,
  /**
   * Where to send the user next. The server decides, because it knows the
   * onboarding state; the client should not re-derive that logic.
   */
  nextStep: z.enum(['connect_broker', 'link_telegram', 'dashboard']),
});

export type User = z.infer<typeof userSchema>;
export type AuthResponse = z.infer<typeof authResponseSchema>;
