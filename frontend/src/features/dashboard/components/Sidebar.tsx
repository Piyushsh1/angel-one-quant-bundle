'use client';

import type { ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/cn';
import {
  useMetrics,
  usePositions,
  useStrategies,
} from '@/features/dashboard/hooks/useDashboard';

/** Compact rupee formatter: 331000 → "₹3.31L", 2500000 → "₹25.0L". */
function formatInr(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e7) return `₹${(value / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `₹${(value / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `₹${(value / 1e3).toFixed(1)}K`;
  return `₹${value.toFixed(0)}`;
}

interface NavItem {
  label: string;
  href: string;
  icon: ReactNode;
  /** Optional trailing badge (e.g. "4 Live", "3"). */
  badge?: { text: string; tone: 'brand' | 'profit' };
}

function Icon({ d }: { d: string }) {
  return (
    <svg
      aria-hidden="true"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      viewBox="0 0 24 24"
    >
      <path d={d} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Navigation is defined without badges here; live counts (running strategies,
 * open positions) are injected at render time from real query data, so the
 * pills reflect the account rather than hard-coded numbers.
 */
const NAV: Omit<NavItem, 'badge'>[] = [
  {
    label: 'Terminal Dashboard',
    href: '/dashboard',
    icon: (
      <Icon d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
    ),
  },
  {
    label: 'Broker Integrations',
    href: '/brokers',
    icon: <Icon d="M13 10V3L4 14h7v7l9-11h-7z" />,
  },
  {
    label: 'Strategy Matrix',
    href: '/dashboard/strategies',
    icon: (
      <Icon d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
    ),
  },
  {
    label: 'Order Ledger',
    href: '/dashboard/orders',
    icon: (
      <Icon d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
    ),
  },
  {
    label: 'Net Positions',
    href: '/dashboard/positions',
    icon: (
      <Icon d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
    ),
  },
  {
    label: 'Quant Reports',
    href: '/dashboard/reports',
    icon: (
      <Icon d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
    ),
  },
  {
    label: 'Risk Parameters',
    href: '/dashboard/risk',
    icon: (
      <Icon d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065zM15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    ),
  },
];

const BADGE_TONE = {
  brand: 'border-brand-500/20 bg-brand-500/10 text-brand-400',
  profit: 'border-profit/20 bg-profit/10 text-profit',
} as const;

/**
 * Left navigation rail. Collapses to a 16-unit icon strip below `lg` and
 * expands to 60 with labels at `lg` and up, mirroring the reference layout.
 *
 * The Strategy Matrix and Net Positions badges show LIVE counts pulled from
 * the same queries the pages use (React Query dedupes the requests), so the
 * pills always match reality instead of a hard-coded number.
 */
export function Sidebar() {
  const pathname = usePathname();
  const { data: strategies } = useStrategies();
  const { data: positions } = usePositions();
  const { data: metrics } = useMetrics();

  const runningCount = (strategies ?? []).filter(
    (s) => s.status === 'running',
  ).length;
  const openCount = positions?.items.length ?? 0;

  // Real RMS margin utilisation from the live broker figures. Utilised =
  // total capital − available margin; utilisation % against total capital.
  const totalCapital = metrics?.totalCapital ?? 0;
  const availableMargin = metrics?.availableMargin ?? 0;
  const brokerConnected = metrics?.gatewayConnected ?? false;
  const utilised = Math.max(totalCapital - availableMargin, 0);
  const marginUsedPct =
    totalCapital > 0 ? Math.min((utilised / totalCapital) * 100, 100) : 0;
  const marginHealthy = marginUsedPct < 80;

  const badgeFor = (href: string): NavItem['badge'] => {
    if (href === '/dashboard/strategies' && runningCount > 0) {
      return { text: `${runningCount} Live`, tone: 'brand' };
    }
    if (href === '/dashboard/positions' && openCount > 0) {
      return { text: String(openCount), tone: 'profit' };
    }
    return undefined;
  };

  return (
    <aside className="flex w-16 flex-shrink-0 flex-col justify-between border-r border-surface-700/60 bg-surface-900 transition-all duration-200 lg:w-60">
      <nav className="space-y-1 p-3" aria-label="Primary">
        {NAV.map((item) => {
          // The dashboard root matches exactly; nested routes match by prefix,
          // so /dashboard/brokers doesn't also light up the root entry.
          const active =
            item.href === '/dashboard'
              ? pathname === item.href
              : pathname.startsWith(item.href);

          const badge = badgeFor(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'group flex items-center gap-3 rounded-lg px-3 py-2 text-xs font-medium transition-all',
                active
                  ? 'border border-profit/25 bg-profit/10 text-profit'
                  : 'text-slate-400 hover:bg-surface-800/60 hover:text-slate-100',
              )}
            >
              {item.icon}
              <span className="hidden lg:inline">{item.label}</span>
              {active && !badge && (
                <span className="ml-auto hidden h-1.5 w-1.5 rounded-full bg-profit shadow-sm lg:inline-block" />
              )}
              {badge && (
                <span
                  className={cn(
                    'ml-auto hidden rounded border px-1.5 py-0.5 font-mono text-[10px] lg:inline-block',
                    BADGE_TONE[badge.tone],
                  )}
                >
                  {badge.text}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* RMS margin health widget — live from the connected broker's RMS */}
      <div className="border-t border-surface-700/60 p-3">
        <div className="hidden rounded-lg border border-surface-700/70 bg-surface-900 p-3 text-xs lg:block">
          {brokerConnected ? (
            <>
              <div className="mb-1.5 flex items-center justify-between text-[11px]">
                <span className="font-medium text-slate-400">RMS Margin Used</span>
                <span className="font-mono font-semibold text-slate-200">
                  {marginUsedPct.toFixed(1)}%
                </span>
              </div>
              <div className="mb-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-800">
                <div
                  className={cn(
                    'h-full',
                    marginHealthy
                      ? 'bg-gradient-to-r from-profit-strong to-brand-400'
                      : 'bg-gradient-to-r from-amber-500 to-loss',
                  )}
                  style={{ width: `${marginUsedPct}%` }}
                />
              </div>
              <div className="flex items-center justify-between font-mono text-[10px] text-slate-400">
                <span>Buffer: {marginHealthy ? 'Healthy' : 'Tight'}</span>
                <span
                  className={cn(
                    'font-semibold',
                    marginHealthy ? 'text-profit' : 'text-loss',
                  )}
                >
                  {formatInr(availableMargin)} Free
                </span>
              </div>
            </>
          ) : (
            <div className="text-[11px] leading-relaxed text-slate-500">
              <span className="font-medium text-slate-400">RMS Margin</span>
              <p className="mt-1">Connect a broker to see live margin.</p>
            </div>
          )}
        </div>

        <div className="flex justify-center py-1 lg:hidden">
          <span
            className={cn(
              'h-2.5 w-2.5 rounded-full',
              !brokerConnected
                ? 'bg-slate-500 ring-4 ring-slate-500/20'
                : marginHealthy
                  ? 'bg-profit ring-4 ring-profit/20'
                  : 'bg-loss ring-4 ring-loss/20',
            )}
            title={
              brokerConnected
                ? `RMS margin used ${marginUsedPct.toFixed(1)}%`
                : 'No broker connected'
            }
          />
        </div>
      </div>
    </aside>
  );
}
