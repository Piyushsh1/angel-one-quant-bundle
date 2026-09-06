import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

type Tone = 'error' | 'warning' | 'info' | 'success';

const TONES: Record<Tone, { container: string; icon: string }> = {
  error: {
    container: 'border-loss/40 bg-loss-dim/20 text-loss',
    icon: 'text-loss',
  },
  warning: {
    container: 'border-paper/40 bg-paper-dim/20 text-paper',
    icon: 'text-paper',
  },
  info: {
    container: 'border-brand-500/40 bg-brand-500/10 text-brand-400',
    icon: 'text-brand-400',
  },
  success: {
    container: 'border-profit/40 bg-profit-dim/20 text-profit',
    icon: 'text-profit',
  },
};

const ICONS: Record<Tone, ReactNode> = {
  error: (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
    />
  ),
  warning: (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      d="M12 9v2m0 4h.01M5.07 19h13.86c1.54 0 2.5-1.67 1.73-3L13.73 4a2 2 0 00-3.46 0L3.34 16c-.77 1.33.19 3 1.73 3z"
    />
  ),
  info: (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
    />
  ),
  success: (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
    />
  ),
};

interface AlertProps {
  tone?: Tone;
  title?: string;
  children: ReactNode;
  className?: string;
}

/**
 * Inline status message.
 *
 * Errors and warnings use `role="alert"` so they are announced immediately;
 * informational tones use `role="status"` to avoid interrupting the user
 * mid-task.
 */
export function Alert({ tone = 'info', title, children, className }: AlertProps) {
  const isUrgent = tone === 'error' || tone === 'warning';

  return (
    <div
      role={isUrgent ? 'alert' : 'status'}
      className={cn(
        'flex animate-fade-in items-start gap-2.5 rounded-lg border p-3 text-xs leading-relaxed',
        TONES[tone].container,
        className,
      )}
    >
      <svg
        aria-hidden="true"
        className={cn('mt-0.5 h-4 w-4 shrink-0', TONES[tone].icon)}
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        {ICONS[tone]}
      </svg>
      <div className="min-w-0">
        {title && <p className="mb-0.5 font-semibold">{title}</p>}
        <div className="text-slate-300">{children}</div>
      </div>
    </div>
  );
}
