import type { ReactNode } from 'react';
import { BrandMark } from '@/features/marketing/components/BrandMark';
import { HeroPanel } from '@/features/marketing/components/HeroPanel';

/**
 * Shared chrome for /login and /signup.
 *
 * A server component, so the hero panel and page furniture ship zero
 * JavaScript — only the forms themselves are client components.
 *
 * LAYOUT CONTRACT: fit-when-it-fits, scroll-when-it-doesn't.
 *   · When the content fits the viewport it is vertically centered (the common
 *     case on a normal laptop or phone).
 *   · When the viewport is too short — a small/windowed browser — the PAGE
 *     scrolls so every field, the submit button and the footer remain
 *     reachable.
 *
 * This replaces an earlier "always exactly one screen, never scroll" rule that
 * clipped the taller signup form on short windows. Locking content to the
 * viewport and hiding the overflow made the submit button unreachable; letting
 * the page scroll is the correct trade-off.
 *
 * `min-h-dvh` (dynamic viewport height) lets the container grow past one screen
 * when needed, while still filling at least a full screen when the content is
 * short.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-50 w-full border-b border-surface-700/60 bg-obsidian/80 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <BrandMark />
          <a
            href="/support"
            className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-slate-400 transition-colors hover:bg-surface-800 hover:text-slate-200"
          >
            Support
          </a>
        </div>
      </header>

      {/* flex-1 so the main area fills the space between header and footer when
          content is short; it grows beyond the viewport (and the page scrolls)
          when content is tall. `items-center` centers the card only when there
          is room — with overflow, the top stays reachable rather than being
          pushed off-screen the way `justify-center` alone would. */}
      <main
        id="main"
        className="relative flex flex-1 items-center justify-center bg-grid-pattern px-4 py-8 sm:px-6 lg:px-8"
      >
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 glow-radial-overlay"
        />

        <div className="relative z-10 mx-auto grid w-full max-w-7xl grid-cols-1 items-center gap-8 lg:grid-cols-12 lg:gap-16">
          {/* Hidden on small screens: on a phone the form is the whole job,
              and a long marketing column above it just adds scrolling. */}
          <div className="hidden lg:col-span-7 lg:block">
            <HeroPanel />
          </div>

          <div className="flex w-full justify-center lg:col-span-5">
            {children}
          </div>
        </div>
      </main>

      <footer className="w-full border-t border-surface-800 bg-obsidian py-3">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-1 px-4 font-mono text-[11px] text-slate-500 sm:flex-row">
          <p>© {new Date().getFullYear()} Barbell</p>
          <p className="text-center sm:text-right">
            F&amp;O trading carries significant risk of loss. Past results do
            not indicate future returns.
          </p>
        </div>
      </footer>
    </div>
  );
}
