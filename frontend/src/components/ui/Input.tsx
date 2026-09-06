'use client';

import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  /** Validation message. Presence switches the field into its error state. */
  error?: string;
  /** Persistent helper text shown when there is no error. */
  hint?: string;
  /** Decorative icon rendered inside the field, on the left. */
  icon?: ReactNode;
  /** Interactive control on the right, e.g. a show/hide toggle. */
  trailing?: ReactNode;
  /** Use tabular monospace figures. Default true — most inputs here are data. */
  mono?: boolean;
}

/**
 * Accessible text field.
 *
 * Wires up `label`/`id`, and links help and error text via
 * `aria-describedby` so assistive technology reads the reason a field was
 * rejected. The error is `role="alert"` so it is announced on appearance
 * rather than only on focus.
 */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    label,
    error,
    hint,
    icon,
    trailing,
    mono = true,
    className,
    required,
    ...rest
  },
  ref,
) {
  const generatedId = useId();
  const inputId = `input-${generatedId}`;
  const errorId = `${inputId}-error`;
  const hintId = `${inputId}-hint`;
  const hasError = Boolean(error);

  const describedBy =
    [hasError ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ') ||
    undefined;

  return (
    <div className="w-full">
      <label
        htmlFor={inputId}
        className="mb-1 block text-xs font-medium text-slate-300"
      >
        {label}
        {required && (
          <span aria-hidden="true" className="ml-0.5 text-brand-400">
            *
          </span>
        )}
      </label>

      <div className="relative rounded-lg">
        {icon && (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3.5 text-slate-500"
          >
            {icon}
          </div>
        )}

        <input
          ref={ref}
          id={inputId}
          required={required}
          aria-invalid={hasError}
          aria-describedby={describedBy}
          className={cn(
            // Field padding shrinks on short viewports so a multi-field form
            // fits without scrolling. A few px per field compounds across six.
            'w-full rounded-lg border bg-surface-900/90 text-sm text-white',
            'py-2.5 short:py-2 tiny:py-1.5',
            'placeholder-slate-500 transition-all',
            'focus:outline-none focus:ring-2 focus:ring-brand-500/40',
            'disabled:cursor-not-allowed disabled:opacity-60',
            mono && 'font-mono text-xs tabular-nums sm:text-sm',
            icon ? 'pl-10' : 'pl-4',
            trailing ? 'pr-11' : 'pr-4',
            hasError
              ? 'border-loss/70 focus:border-loss focus:ring-loss/30'
              : 'border-surface-700/80 focus:border-brand-500',
            className,
          )}
          {...rest}
        />

        {trailing && (
          <div className="absolute inset-y-0 right-0 flex items-center pr-2">
            {trailing}
          </div>
        )}
      </div>

      {hasError ? (
        <p
          id={errorId}
          role="alert"
          className="mt-1.5 animate-fade-in text-xs text-loss"
        >
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="mt-1.5 text-xs text-slate-500">
          {hint}
        </p>
      ) : null}
    </div>
  );
});
