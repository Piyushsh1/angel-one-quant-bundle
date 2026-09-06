import type { ReactNode } from 'react';

interface PageContainerProps {
  title: string;
  subtitle?: string;
  /** Optional controls rendered on the right of the header row. */
  actions?: ReactNode;
  children: ReactNode;
}

/**
 * Standard shell for a full terminal page (Strategy Matrix, Order Ledger, …).
 *
 * Owns the single scroll region and the `id="main"` skip-link target the root
 * layout links to, mirroring the dashboard page so every terminal route feels
 * identical. The dashboard chrome (banner, header, sidebar) is supplied by the
 * shared (dashboard) layout, so a page only renders its own title and body.
 */
export function PageContainer({
  title,
  subtitle,
  actions,
  children,
}: PageContainerProps) {
  return (
    <main
      id="main"
      className="flex-1 space-y-5 overflow-y-auto bg-obsidian px-4 py-5 lg:px-6"
    >
      <header className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white">{title}</h1>
          {subtitle && (
            <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </header>

      {children}

      {/* Breathing room so the floating panic button never overlaps content. */}
      <div className="h-10" />
    </main>
  );
}
