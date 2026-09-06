import { cn } from '@/lib/cn';

interface OnboardingStepperProps {
  current: 'broker' | 'telegram';
}

const STEPS = [
  { id: 'broker', label: 'Connect broker' },
  { id: 'telegram', label: 'Notifications' },
  { id: 'done', label: 'Start' },
] as const;

/**
 * Progress indicator across the onboarding steps.
 *
 * Numbers, not just dots, so the user always knows how many steps remain —
 * important the first time someone is being asked to hand over broker
 * credentials, which is the point of highest anxiety.
 */
export function OnboardingStepper({ current }: OnboardingStepperProps) {
  const currentIndex = STEPS.findIndex((s) => s.id === current);

  return (
    <ol className="flex items-center gap-2" aria-label="Setup progress">
      {STEPS.map((step, i) => {
        const state =
          i < currentIndex ? 'done' : i === currentIndex ? 'active' : 'upcoming';
        return (
          <li key={step.id} className="flex flex-1 items-center gap-2">
            <span
              aria-current={state === 'active' ? 'step' : undefined}
              className={cn(
                'flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold',
                state === 'done' && 'border-brand-500 bg-brand-500 text-slate-950',
                state === 'active' &&
                  'border-brand-400 bg-brand-500/15 text-brand-400',
                state === 'upcoming' &&
                  'border-surface-700 bg-surface-850 text-slate-500',
              )}
            >
              {state === 'done' ? (
                <svg
                  className="h-3.5 w-3.5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={3}
                    d="M5 13l4 4L19 7"
                  />
                </svg>
              ) : (
                i + 1
              )}
            </span>
            <span
              className={cn(
                'hidden text-xs font-medium sm:block',
                state === 'active' ? 'text-slate-200' : 'text-slate-500',
              )}
            >
              {step.label}
            </span>
            {i < STEPS.length - 1 && (
              <span
                aria-hidden="true"
                className={cn(
                  'h-px flex-1',
                  i < currentIndex ? 'bg-brand-500/60' : 'bg-surface-700',
                )}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
