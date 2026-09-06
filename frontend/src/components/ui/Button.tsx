'use client';

import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';
import { Spinner } from '@/components/ui/Spinner';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-gradient-to-r from-brand-500 via-brand-400 to-teal-400 text-slate-950 font-bold ' +
    'shadow-glow-brand hover:shadow-glow-brand-lg hover:from-brand-400 hover:to-teal-300',
  secondary:
    'border border-surface-700/80 bg-surface-850/60 text-slate-200 font-semibold ' +
    'hover:bg-surface-800 hover:border-surface-600',
  ghost: 'text-slate-300 font-medium hover:bg-surface-800 hover:text-slate-100',
  // Reserved for irreversible actions (flatten, revoke, disable trading).
  danger:
    'bg-loss-strong text-white font-bold hover:bg-loss shadow-[0_0_25px_-5px_rgba(239,68,68,0.4)]',
};

const SIZES: Record<Size, string> = {
  sm: 'h-9 px-3 text-xs',
  md: 'h-11 px-4 text-sm',
  lg: 'h-12 px-5 text-sm',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  isLoading?: boolean;
  fullWidth?: boolean;
  /** Replaces the label while loading, e.g. "Signing in…". */
  loadingText?: string;
}

/**
 * The only button in the app.
 *
 * While `isLoading` the button is disabled and `aria-busy` — this is what stops
 * a double-tap from submitting an order twice, which matters more here than in
 * most apps.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = 'primary',
      size = 'md',
      isLoading = false,
      fullWidth = false,
      loadingText,
      className,
      children,
      disabled,
      type = 'button',
      ...rest
    },
    ref,
  ) {
    const isDisabled = disabled === true || isLoading;

    return (
      <button
        ref={ref}
        type={type}
        disabled={isDisabled}
        aria-busy={isLoading}
        className={cn(
          'inline-flex items-center justify-center gap-2 rounded-lg tracking-wide',
          'transition-all duration-200',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400',
          'focus-visible:ring-offset-2 focus-visible:ring-offset-obsidian',
          'disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none',
          // Lift on hover only when actionable, so a disabled button feels inert.
          !isDisabled && variant === 'primary' && 'hover:-translate-y-0.5 active:translate-y-0',
          VARIANTS[variant],
          SIZES[size],
          fullWidth && 'w-full',
          className,
        )}
        {...rest}
      >
        {isLoading ? (
          <>
            <Spinner label="" />
            <span>{loadingText ?? children}</span>
          </>
        ) : (
          children
        )}
      </button>
    );
  },
);
