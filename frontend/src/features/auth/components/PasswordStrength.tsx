'use client';

import { useMemo } from 'react';
import { cn } from '@/lib/cn';

interface PasswordStrengthProps {
  password: string;
}

interface Assessment {
  score: 0 | 1 | 2 | 3 | 4;
  label: string;
  colorClass: string;
}

/**
 * Heuristic strength meter.
 *
 * Intentionally simple and transparent: it rewards length first, then variety.
 * This is guidance for the user, NOT the validation gate — `signupSchema` is
 * what actually enforces the policy, and the server must enforce it too.
 */
function assess(password: string): Assessment {
  if (!password) {
    return { score: 0, label: '', colorClass: '' };
  }

  let points = 0;
  if (password.length >= 12) points += 2;
  else if (password.length >= 8) points += 1;
  if (password.length >= 16) points += 1;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) points += 1;
  if (/\d/.test(password)) points += 1;
  if (/[^A-Za-z0-9]/.test(password)) points += 1;

  // Obvious repetition or sequences shouldn't score as "strong".
  if (/(.)\1{2,}/.test(password)) points -= 1;

  const score = Math.max(0, Math.min(4, points - 1)) as Assessment['score'];

  const table: Record<Assessment['score'], Omit<Assessment, 'score'>> = {
    0: { label: 'Very weak', colorClass: 'bg-loss-strong' },
    1: { label: 'Weak', colorClass: 'bg-loss' },
    2: { label: 'Fair', colorClass: 'bg-paper' },
    3: { label: 'Strong', colorClass: 'bg-profit' },
    4: { label: 'Very strong', colorClass: 'bg-profit-strong' },
  };

  return { score, ...table[score] };
}

export function PasswordStrength({ password }: PasswordStrengthProps) {
  const { score, label, colorClass } = useMemo(
    () => assess(password),
    [password],
  );

  if (!password) return null;

  return (
    // Bars and label share one row to save vertical space — the signup form
    // has to fit a laptop viewport without scrolling.
    <div className="mt-1.5 flex items-center gap-2">
      <div className="flex flex-1 items-center gap-1.5" aria-hidden="true">
        {[0, 1, 2, 3].map((segment) => (
          <div
            key={segment}
            className={cn(
              'h-1 flex-1 rounded-full transition-colors duration-300',
              segment < score ? colorClass : 'bg-surface-700',
            )}
          />
        ))}
      </div>
      {/* Politeness matters: this updates on every keystroke, so `polite`
          avoids a screen reader interrupting the user as they type. */}
      <p
        aria-live="polite"
        className="w-20 shrink-0 text-right text-[11px] font-medium text-slate-400"
      >
        {label}
      </p>
    </div>
  );
}
