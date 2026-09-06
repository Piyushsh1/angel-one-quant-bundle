'use client';

import { useForm, useWatch } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import Link from 'next/link';
import { Input } from '@/components/ui/Input';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { Checkbox } from '@/components/ui/Checkbox';
import { Button } from '@/components/ui/Button';
import { Alert } from '@/components/ui/Alert';
import { PasswordStrength } from '@/features/auth/components/PasswordStrength';
import { useSignup } from '@/features/auth/hooks/useAuthMutations';
import {
  signupSchema,
  type SignupInput,
} from '@/features/auth/schemas/auth.schema';
import { ApiError, userFacingMessage } from '@/lib/errors';

function UserIcon() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
      />
    </svg>
  );
}

function MailIcon() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
      />
    </svg>
  );
}

export function SignupForm() {
  const {
    register,
    handleSubmit,
    setError,
    control,
    formState: { errors, isSubmitting },
  } = useForm<SignupInput>({
    resolver: zodResolver(signupSchema),
    defaultValues: {
      fullName: '',
      email: '',
      password: '',
      confirmPassword: '',
      // `false` is intentionally invalid per the schema (`z.literal(true)`),
      // so the consent gate cannot be bypassed by leaving it untouched.
      acceptedTerms: false as unknown as true,
    },
    mode: 'onBlur',
  });

  const signupMutation = useSignup(setError);

  // Subscribed narrowly so only the strength meter re-renders per keystroke,
  // not the whole form.
  const password = useWatch({ control, name: 'password' }) ?? '';

  const onSubmit = (values: SignupInput) => {
    signupMutation.mutate(values);
  };

  const error = signupMutation.error;
  const hasFieldLevelError =
    error instanceof ApiError && Object.keys(error.fieldErrors).length > 0;
  const showBanner = Boolean(error) && !hasFieldLevelError;

  const isBusy = isSubmitting || signupMutation.isPending;

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      noValidate
      className="space-y-3.5 short:space-y-2.5 tiny:space-y-2"
    >
      {showBanner && (
        <Alert tone="error" title="Could not create your account">
          {userFacingMessage(error)}
        </Alert>
      )}

      <Input
        label="Full name"
        type="text"
        autoComplete="name"
        placeholder="Piyush Sharma"
        icon={<UserIcon />}
        mono={false}
        error={errors.fullName?.message}
        disabled={isBusy}
        required
        {...register('fullName')}
      />

      <Input
        label="Email"
        type="email"
        autoComplete="email"
        placeholder="trader@domain.com"
        icon={<MailIcon />}
        error={errors.email?.message}
        disabled={isBusy}
        required
        {...register('email')}
      />

      <div>
        <PasswordInput
          label="Password"
          autoComplete="new-password"
          placeholder="At least 12 characters, mixed case + a number"
          error={errors.password?.message}
          disabled={isBusy}
          required
          {...register('password')}
        />
        <PasswordStrength password={password} />
      </div>

      <PasswordInput
        label="Confirm password"
        autoComplete="new-password"
        placeholder="Re-enter your password"
        error={errors.confirmPassword?.message}
        disabled={isBusy}
        required
        {...register('confirmPassword')}
      />

      <div className="pt-1">
        <Checkbox
          disabled={isBusy}
          error={errors.acceptedTerms?.message}
          label={
            <>
              I accept the{' '}
              <Link
                href="/legal/terms"
                className="font-medium text-brand-400 hover:underline"
              >
                Terms of Service
              </Link>
              ,{' '}
              <Link
                href="/legal/privacy"
                className="font-medium text-brand-400 hover:underline"
              >
                Privacy Policy
              </Link>{' '}
              and{' '}
              <Link
                href="/legal/risk-disclosure"
                className="font-medium text-brand-400 hover:underline"
              >
                Risk Disclosure
              </Link>
              .
            </>
          }
          {...register('acceptedTerms')}
        />
      </div>

      {/* Set expectations before the account exists: new accounts start in
          paper mode. Kept to one line so the whole form fits a laptop
          viewport without scrolling. */}
      <Alert tone="info">
        You&apos;ll start in{' '}
        <strong className="font-semibold text-paper">paper mode</strong> —
        orders simulated on live data. Going live is a separate step.
      </Alert>

      <Button
        type="submit"
        fullWidth
        size="lg"
        isLoading={isBusy}
        loadingText="Creating account…"
      >
        Create account
      </Button>
    </form>
  );
}
