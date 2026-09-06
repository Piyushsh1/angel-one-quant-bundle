import type { ReactNode } from 'react';
import { BrandMark } from '@/features/marketing/components/BrandMark';
import { OnboardingStepper } from '@/features/onboarding/components/OnboardingStepper';

interface OnboardingShellProps {
  step: 'broker' | 'telegram';
  title: string;
  subtitle: string;
  children: ReactNode;
}

/**
 * Chrome shared by the onboarding steps.
 *
 * Fit-when-it-fits, scroll-when-it-doesn't — the same layout contract as the
 * auth screens, since a credential form can be tall on a short window.
 */
export function OnboardingShell({
  step,
  title,
  subtitle,
  children,
}: OnboardingShellProps) {
  return (
    <div className="flex min-h-dvh flex-col bg-obsidian">
      <header className="sticky top-0 z-50 w-full border-b border-surface-700/60 bg-obsidian/80 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-4 sm:px-6">
          <BrandMark href="/dashboard" />
          <span className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
            Setup
          </span>
        </div>
      </header>

      <main
        id="main"
        className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 py-8 sm:px-6"
      >
        <OnboardingStepper current={step} />

        <div className="mt-8">
          <h1 className="text-2xl font-bold tracking-tight text-white">{title}</h1>
          <p className="mt-1 text-sm text-slate-400">{subtitle}</p>
        </div>

        <div className="mt-6">{children}</div>
      </main>
    </div>
  );
}
