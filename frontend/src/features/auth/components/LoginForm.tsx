'use client';

import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import Link from 'next/link';
import { Input } from '@/components/ui/Input';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { Checkbox } from '@/components/ui/Checkbox';
import { Button } from '@/components/ui/Button';
import { Alert } from '@/components/ui/Alert';
import { useLogin } from '@/features/auth/hooks/useAuthMutations';
import {
  loginSchema,
  type LoginInput,
} from '@/features/auth/schemas/auth.schema';
import { userFacingMessage } from '@/lib/errors';
import { ApiError } from '@/lib/errors';

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

export function LoginForm() {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<LoginInput>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '', rememberDevice: false },
    // Validate on blur: validating per-keystroke shows "invalid email" while
    // the user is still typing the first character.
    mode: 'onBlur',
  });

  const loginMutation = useLogin(setError);

  const onSubmit = (values: LoginInput) => {
    loginMutation.mutate(values);
  };

  // Show a banner only for failures that are not already attached to a field,
  // so the user never sees the same message twice.
  const error = loginMutation.error;
  const hasFieldLevelError =
    error instanceof ApiError && Object.keys(error.fieldErrors).length > 0;
  const showBanner = Boolean(error) && !hasFieldLevelError;

  const isBusy = isSubmitting || loginMutation.isPending;

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-4">
      {showBanner && (
        <Alert tone="error" title="Could not sign you in">
          {userFacingMessage(error)}
        </Alert>
      )}

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
          autoComplete="current-password"
          placeholder="••••••••••••"
          error={errors.password?.message}
          disabled={isBusy}
          required
          {...register('password')}
        />
      </div>

      <div className="flex items-center justify-between pt-1">
        <Checkbox
          label="Remember this device"
          disabled={isBusy}
          {...register('rememberDevice')}
        />
        <Link
          href="/forgot-password"
          className="rounded text-xs font-medium text-brand-400 transition-colors hover:text-brand-hover hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
        >
          Forgot password?
        </Link>
      </div>

      <Button
        type="submit"
        fullWidth
        size="lg"
        className="mt-2"
        isLoading={isBusy}
        loadingText="Signing in…"
      >
        Sign in
      </Button>
    </form>
  );
}
