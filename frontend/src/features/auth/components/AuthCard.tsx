import type { ReactNode } from 'react';
import { AuthTabs } from '@/features/auth/components/AuthTabs';
import { CustodyNotice } from '@/features/auth/components/CustodyNotice';

interface AuthCardProps {
  /** Which tab to mark as current. */
  active: 'login' | 'signup';
  title: string;
  subtitle: string;
  /** The form itself — the only client component in the tree. */
  children: ReactNode;
}

/**
 * Shell shared by /login and /signup.
 *
 * Owns all the chrome — glass panel, tabs, heading, federated options and the
 * custody disclosure — so the two pages differ only by their form and copy.
 * Keeping this in one place is what stops the panels drifting apart visually
 * as either screen evolves.
 *
 * A server component: it renders no interactive state of its own.
 */
export function AuthCard({
  active,
  title,
  subtitle,
  children,
}: AuthCardProps) {
  return (
    // The card renders at its natural height; the PAGE scrolls when the
    // viewport is too short (see AuthLayout). No internal card scroll — a
    // scrollbar inside a form is worse UX than scrolling the page. Padding
    // still tightens a little on short viewports to reduce how often that
    // scroll is needed.
    <div className="flex w-full max-w-md flex-col">
      <div className="relative flex w-full flex-col rounded-2xl border border-surface-700/80 bg-gradient-to-br from-surface-800/80 to-surface-900/90 p-8 shadow-elevated backdrop-blur-xl short:p-6 tiny:p-5">
        {/* Hairline accent along the top edge. */}
        <div
          aria-hidden="true"
          className="absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-brand-400/50 to-transparent"
        />

        <AuthTabs active={active} />

        <div className="mb-6 short:mb-4">
          <h1 className="text-2xl font-bold tracking-tight text-white short:text-xl">
            {title}
          </h1>
          <p className="mt-1 text-xs text-slate-400">{subtitle}</p>
        </div>

        {children}

        {/* The custody notice is important but not essential to complete the
            form; on very short viewports it is hidden to guarantee the submit
            button is reachable without scrolling. */}
        <div className="mt-6 border-t border-surface-700/50 pt-4 short:mt-4 tiny:hidden">
          <CustodyNotice />
        </div>
      </div>
    </div>
  );
}
