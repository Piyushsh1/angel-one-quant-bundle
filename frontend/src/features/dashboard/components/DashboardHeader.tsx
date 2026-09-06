'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { formatSignedPct, pnlColor } from '@/features/dashboard/lib/format';
import { Sparkline } from '@/features/dashboard/components/Sparkline';
import { useIndices, useMetrics } from '@/features/dashboard/hooks/useDashboard';
import { useCurrentUser } from '@/features/auth/hooks/useCurrentUser';
import { useLogout } from '@/features/auth/hooks/useAuthMutations';
import { cn } from '@/lib/cn';

/** Broker display labels, keyed by the broker id the backend reports. */
const BROKER_LABELS: Record<string, string> = {
  angelone: 'Angel One SmartAPI',
  zerodha: 'Zerodha Kite',
  upstox: 'Upstox',
  groww: 'Groww',
  dhan: 'Dhan',
  fyers: 'Fyers',
};

/** A live IST clock string, updated every second on the client. */
function useIstClock(): string {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!now) return ''; // avoid SSR/client hydration mismatch
  return new Intl.DateTimeFormat('en-IN', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'Asia/Kolkata',
  }).format(now);
}

/** True during NSE/BSE regular session (Mon–Fri, 09:15–15:30 IST). */
function useMarketOpen(): boolean {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const check = () => {
      const parts = new Intl.DateTimeFormat('en-US', {
        weekday: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
        timeZone: 'Asia/Kolkata',
      }).formatToParts(new Date());
      const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
      const weekday = get('weekday');
      const mins = Number(get('hour')) * 60 + Number(get('minute'));
      const isWeekday = !['Sat', 'Sun'].includes(weekday);
      setOpen(isWeekday && mins >= 555 && mins <= 930); // 09:15–15:30
    };
    check();
    const t = setInterval(check, 15_000);
    return () => clearInterval(t);
  }, []);
  return open;
}

/** First+last initials from a full name, e.g. "Piyush Sharma" → "PS". */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0];
  if (!first) return 'U';
  if (parts.length === 1) return first.slice(0, 2).toUpperCase();
  const last = parts[parts.length - 1] ?? first;
  return ((first[0] ?? '') + (last[0] ?? '')).toUpperCase();
}

/** The barbell glyph, matching the marketing BrandMark but sized for the bar. */
function LogoGlyph() {
  return (
    <span
      aria-hidden="true"
      className="flex h-8 w-8 items-center justify-center rounded-lg border border-profit/40 bg-gradient-to-br from-brand-500/20 to-profit/20 shadow-inner"
    >
      <svg
        className="h-5 w-5 text-profit"
        fill="none"
        stroke="currentColor"
        strokeWidth={2.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        viewBox="0 0 24 24"
      >
        <path d="M6 5v14M18 5v14M2 9v6M22 9v6M6 12h12" />
      </svg>
    </span>
  );
}

function ArrowGlyph({ direction }: { direction: 'up' | 'down' }) {
  return (
    <svg
      aria-hidden="true"
      className="inline h-3 w-3"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      viewBox="0 0 24 24"
    >
      {direction === 'up' ? (
        <path
          d="M5 10l7-7m0 0l7 7m-7-7v18"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : (
        <path
          d="M19 14l-7 7m0 0l-7-7m7 7V3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}

/** One entry in the indices micro-ticker strip. */
function IndexTicker({
  label,
  value,
  changePct,
  sparkline,
  divider,
}: {
  label: string;
  value: string;
  changePct: number;
  sparkline?: string | null;
  divider: boolean;
}) {
  const up = changePct >= 0;
  const color = pnlColor(changePct);
  return (
    <div
      className={cn(
        'flex items-center gap-2',
        divider && 'border-r border-surface-700/60 pr-3',
      )}
    >
      <span className="text-[11px] font-medium text-slate-400">{label}</span>
      <span className="font-mono text-xs font-semibold text-white">{value}</span>
      <span
        className={cn('flex items-center font-mono text-[10px] font-medium', color)}
      >
        <ArrowGlyph direction={up ? 'up' : 'down'} />
        {formatSignedPct(changePct)}
      </span>
      {sparkline ? (
        <Sparkline path={sparkline} className={cn('h-4 w-12', color)} />
      ) : null}
    </div>
  );
}

/**
 * The global header: brand, live market clock, indices ticker, broker sync
 * badge, alerts, and profile. A client component — the indices ticker and the
 * profile/broker badges read live data via React Query.
 */
export function DashboardHeader() {
  const { data: indices = [] } = useIndices();
  const { data: user } = useCurrentUser();
  const { data: metrics } = useMetrics();
  const clock = useIstClock();
  const marketOpen = useMarketOpen();

  const logout = useLogout();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const displayName = user?.fullName ?? 'Trader';
  const userEmail = user?.email ?? '';
  const brokerConnected = user?.hasBrokerConnected ?? false;
  const telegramLinked = user?.hasTelegramLinked ?? false;
  const brokerLabel = metrics?.gatewayConnected
    ? metrics.gatewayName
    : BROKER_LABELS.angelone;

  // Close the profile menu on an outside click.
  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [menuOpen]);

  return (
    <header className="sticky top-0 z-40 flex items-center justify-between border-b border-surface-700/60 bg-surface-900 px-4 py-2.5 lg:px-6">
      {/* Left: brand + market status */}
      <div className="flex items-center gap-6">
        <div className="flex select-none items-center gap-2.5">
          <LogoGlyph />
          <div>
            <div className="flex items-center gap-1.5">
              <span className="text-base font-bold uppercase tracking-tight text-white">
                Barbell
              </span>
              <span className="rounded border border-profit/30 bg-profit/10 px-1.5 py-0.5 font-mono text-[9px] tracking-wider text-profit">
                PRO v3.4
              </span>
            </div>
            <p className="text-[10px] font-medium tracking-wide text-slate-400">
              Quant Execution Suite
            </p>
          </div>
        </div>

        <div className="hidden items-center gap-2 border-l border-surface-700/60 pl-4 text-xs md:flex">
          <div
            className={cn(
              'flex items-center gap-1.5 rounded-full border px-2.5 py-1',
              marketOpen
                ? 'border-profit/25 bg-profit/10 text-profit'
                : 'border-surface-700 bg-surface-800 text-slate-400',
            )}
          >
            <span
              className={cn(
                'h-2 w-2 rounded-full',
                marketOpen ? 'animate-pulse bg-profit' : 'bg-slate-500',
              )}
            />
            <span className="text-[11px] font-semibold tracking-wider">
              {marketOpen ? 'NSE/BSE LIVE' : 'MARKET CLOSED'}
            </span>
          </div>
          <span className="font-mono text-[11px] text-slate-400">
            {clock ? `${clock} IST` : '—'}
          </span>
        </div>
      </div>

      {/* Center: indices ticker — live quotes, empty when no broker/feed */}
      <div className="hidden items-center gap-4 rounded-lg border border-surface-700 bg-surface-900 px-3 py-1 xl:flex">
        {indices.length > 0 ? (
          indices.map((idx, i) => (
            <IndexTicker
              key={idx.label}
              label={idx.label}
              value={idx.value}
              changePct={idx.changePct}
              sparkline={idx.sparkline}
              divider={i < indices.length - 1}
            />
          ))
        ) : (
          <span className="font-mono text-[11px] text-slate-500">
            Index feed unavailable
          </span>
        )}
      </div>

      {/* Right: broker badge, alerts, profile */}
      <div className="flex items-center gap-3">
        <div className="hidden items-center gap-2 rounded-md border border-surface-700 bg-surface-900 px-2.5 py-1 text-xs sm:flex">
          <span
            className={cn(
              'h-2 w-2 rounded-full',
              brokerConnected ? 'animate-pulse bg-profit' : 'bg-slate-500',
            )}
          />
          <span className="text-slate-400">Broker:</span>
          <span className="font-medium text-slate-200">{brokerLabel}</span>
          <span
            className={cn(
              'rounded border px-1 py-0.5 font-mono text-[10px]',
              brokerConnected
                ? 'border-profit/20 bg-profit/10 text-profit'
                : 'border-surface-700 bg-surface-800 text-slate-400',
            )}
          >
            {brokerConnected ? 'Active' : 'Not linked'}
          </span>
        </div>

        <Link
          href="/onboarding/telegram"
          aria-label={
            telegramLinked
              ? 'Notifications — Telegram linked'
              : 'Notifications — link Telegram for trade alerts'
          }
          title={
            telegramLinked
              ? 'Trade alerts are delivered to your Telegram'
              : 'Link Telegram to receive trade alerts'
          }
          className="relative rounded-lg border border-surface-700 bg-surface-850 p-2 text-slate-400 transition-colors hover:bg-surface-800 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
        >
          <svg
            aria-hidden="true"
            className="h-4 w-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
            />
          </svg>
          {/* Amber nudge only when Telegram isn't linked yet — real state. */}
          {!telegramLinked && (
            <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-paper ring-2 ring-surface-900" />
          )}
        </Link>

        <div className="relative border-l border-surface-700 pl-2" ref={menuRef}>
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="Account menu"
            className="flex items-center gap-2 rounded-lg p-1 transition-colors hover:bg-surface-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-tr from-brand-600 to-indigo-600 text-xs font-bold text-white shadow">
              {initials(displayName)}
            </span>
            <div className="hidden text-left lg:block">
              <div className="text-xs font-semibold leading-tight text-white">
                {displayName}
              </div>
              <div className="font-mono text-[10px] text-slate-400">
                {brokerConnected ? brokerLabel : 'No broker linked'}
              </div>
            </div>
            <svg
              aria-hidden="true"
              className={cn(
                'hidden h-3.5 w-3.5 text-slate-500 transition-transform sm:block',
                menuOpen && 'rotate-180',
              )}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                d="M19 9l-7 7-7-7"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
              />
            </svg>
          </button>

          {menuOpen && (
            <div
              role="menu"
              className="absolute right-0 top-full z-50 mt-1 w-56 overflow-hidden rounded-lg border border-surface-700 bg-surface-900 shadow-xl"
            >
              <div className="border-b border-surface-700/60 px-3 py-2">
                <p className="truncate text-xs font-semibold text-white">
                  {displayName}
                </p>
                {userEmail && (
                  <p className="truncate font-mono text-[10px] text-slate-400">
                    {userEmail}
                  </p>
                )}
              </div>
              <Link
                href="/brokers"
                role="menuitem"
                onClick={() => setMenuOpen(false)}
                className="block px-3 py-2 text-xs text-slate-300 transition-colors hover:bg-surface-800 hover:text-white"
              >
                Broker Integrations
              </Link>
              <Link
                href="/dashboard/risk"
                role="menuitem"
                onClick={() => setMenuOpen(false)}
                className="block px-3 py-2 text-xs text-slate-300 transition-colors hover:bg-surface-800 hover:text-white"
              >
                Risk Parameters
              </Link>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  logout.mutate();
                }}
                disabled={logout.isPending}
                className="block w-full px-3 py-2 text-left text-xs font-semibold text-loss transition-colors hover:bg-loss/10 disabled:opacity-50"
              >
                {logout.isPending ? 'Signing out…' : 'Sign out'}
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
