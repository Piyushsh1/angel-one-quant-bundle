'use client';

import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface CheckboxProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'id'> {
  label: ReactNode;
  error?: string;
}

/**
 * Checkbox with a clickable label.
 *
 * Used for consent gates ("I understand trades will be placed
 * automatically"), so the error state has to be visible and announced — a
 * silently-unchecked acknowledgement would be a compliance problem.
 */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  function Checkbox({ label, error, className, ...rest }, ref) {
    const generatedId = useId();
    const id = `checkbox-${generatedId}`;
    const errorId = `${id}-error`;
    const hasError = Boolean(error);

    return (
      <div>
        <div className="flex items-start gap-2.5">
          <input
            ref={ref}
            id={id}
            type="checkbox"
            aria-invalid={hasError}
            aria-describedby={hasError ? errorId : undefined}
            className={cn(
              'mt-0.5 h-4 w-4 shrink-0 rounded border-surface-600 bg-surface-800',
              'text-brand-500 transition-colors',
              'focus:ring-1 focus:ring-brand-500 focus:ring-offset-0',
              hasError && 'border-loss',
              className,
            )}
            {...rest}
          />
          <label
            htmlFor={id}
            className="cursor-pointer select-none text-xs leading-relaxed text-slate-400 hover:text-slate-300"
          >
            {label}
          </label>
        </div>
        {hasError && (
          <p id={errorId} role="alert" className="mt-1.5 text-xs text-loss">
            {error}
          </p>
        )}
      </div>
    );
  },
);
