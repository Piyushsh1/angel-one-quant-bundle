'use client';

import Link from 'next/link';
import { cn } from '@/lib/cn';

interface AuthTabsProps {
  active: 'login' | 'signup';
}

const TABS = [
  { id: 'login', label: 'Sign In', href: '/login' },
  { id: 'signup', label: 'Create Account', href: '/signup' },
] as const;

/**
 * Sign-in / sign-up switcher.
 *
 * Implemented as real navigation links rather than client-side tab state, so
 * each form has its own URL. That makes them deep-linkable, back-button
 * friendly, and separately server-rendered — which the mockup's `role="tab"`
 * buttons would not be.
 *
 * Because these are links, ARIA tab roles are deliberately not used: this is
 * navigation, and pretending otherwise would mislead assistive technology.
 */
export function AuthTabs({ active }: AuthTabsProps) {
  return (
    <nav
      aria-label="Authentication"
      className="mb-7 flex border-b border-surface-700/70 short:mb-4"
    >
      {TABS.map((tab) => {
        const isActive = tab.id === active;
        return (
          <Link
            key={tab.id}
            href={tab.href}
            aria-current={isActive ? 'page' : undefined}
            className={cn(
              'flex flex-1 items-center justify-center border-b-2 pb-3 text-sm transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400',
              isActive
                ? 'border-brand-400 font-semibold text-white'
                : 'border-transparent font-medium text-slate-400 hover:text-slate-200',
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
