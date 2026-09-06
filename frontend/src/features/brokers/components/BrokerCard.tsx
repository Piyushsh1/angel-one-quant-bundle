'use client';

import type { Broker, BrokerId } from '@/config/brokers';
import { BROKER_DISPLAY } from '@/features/brokers/data/display';
import { BrokerLogo } from '@/features/brokers/components/BrokerLogo';
import { cn } from '@/lib/cn';

interface BrokerCardProps {
  broker: Broker;
  selected: boolean;
  onSelect: (id: BrokerId) => void;
}

function BoltIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      viewBox="0 0 24 24"
    >
      <path
        d="M13 10V3L4 14h7v7l9-11h-7z"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * A single broker in the selection grid.
 *
 * Rendered as a `role="radio"` so the surrounding grid behaves like a native
 * radiogroup (arrow keys move between options, one selection at a time).
 * Coming-soon brokers are shown but `disabled` — a deliberate choice from the
 * broker registry: inviting a user to hand credentials to an adapter that
 * cannot use them would be worse than showing it as not-yet-ready.
 */
export function BrokerCard({ broker, selected, onSelect }: BrokerCardProps) {
  const display = BROKER_DISPLAY[broker.id];
  const available = broker.status === 'available';

  return (
    <div
      role="radio"
      aria-checked={selected}
      aria-disabled={!available}
      tabIndex={available ? 0 : -1}
      onClick={() => available && onSelect(broker.id)}
      onKeyDown={(e) => {
        if (available && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          onSelect(broker.id);
        }
      }}
      className={cn(
        'group relative flex h-[235px] flex-col justify-between rounded-2xl border p-5 transition duration-200',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2 focus-visible:ring-offset-obsidian',
        available
          ? 'cursor-pointer'
          : 'cursor-not-allowed border-surface-700/60 bg-surface-900/60',
        available && selected
          ? 'border-2 border-brand-400 bg-surface-800 shadow-glow-brand'
          : available &&
              'border border-surface-700 bg-surface-850 hover:border-surface-600 hover:bg-surface-800',
      )}
    >
      {/* Top-right status badge */}
      {!available ? (
        <span className="absolute right-4 top-4 rounded border border-surface-700 bg-surface-800 px-2 py-0.5 font-mono text-[10px] font-medium uppercase text-slate-400">
          {display.eta}
        </span>
      ) : selected ? (
        <span className="absolute right-4 top-4 flex h-6 w-6 items-center justify-center rounded-full bg-brand-500 text-slate-950 shadow-[0_0_8px_rgba(34,211,238,0.6)]">
          <svg
            aria-hidden="true"
            className="h-4 w-4"
            fill="none"
            stroke="currentColor"
            strokeWidth={2.8}
            viewBox="0 0 24 24"
          >
            <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      ) : (
        <span className="absolute right-4 top-4 flex h-6 w-6 items-center justify-center rounded-full border border-surface-600 text-transparent transition group-hover:border-surface-500">
          <span className="h-3.5 w-3.5 rounded-full border border-surface-600 group-hover:border-surface-500" />
        </span>
      )}

      <div>
        {/* Logo + name + status */}
        <div className={cn('flex items-center gap-3', !available && 'opacity-70 group-hover:opacity-100 transition')}>
          <span className="flex h-12 w-12 items-center justify-center rounded-xl border border-surface-700 bg-surface-900 p-2 transition">
            <BrokerLogo id={broker.id} />
          </span>
          <div>
            <h3
              className={cn(
                'flex items-center gap-1.5 text-base font-bold',
                available ? 'text-white' : 'text-slate-300',
              )}
            >
              {broker.name}
              {selected && (
                <span className="rounded border border-brand-500/30 bg-surface-900 px-1.5 py-0.5 font-mono text-[10px] text-brand-400">
                  Selected
                </span>
              )}
            </h3>
            <span
              className={cn(
                'mt-0.5 inline-flex items-center gap-1 text-[11px] font-medium',
                available ? 'text-profit' : 'text-slate-400',
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  available ? 'bg-profit' : 'bg-slate-500',
                )}
              />
              {available ? 'Supported' : 'Coming Soon'} • {display.apiLabel}
            </span>
          </div>
        </div>

        <p
          className={cn(
            'mt-3.5 text-xs leading-relaxed',
            available ? 'text-slate-300' : 'text-slate-500',
          )}
        >
          {display.description}
        </p>
      </div>

      {/* Footer meta */}
      <div
        className={cn(
          'flex items-center justify-between border-t pt-3 font-mono text-[11px]',
          available ? 'border-surface-700/70 text-slate-400' : 'border-surface-700/50 text-slate-500',
        )}
      >
        {available ? (
          <span className="flex items-center gap-1 font-semibold text-profit">
            <BoltIcon className="h-3.5 w-3.5" />
            {display.apiLabel}
          </span>
        ) : (
          <span>{display.comingSoonNote}</span>
        )}

        {available ? (
          <span
            className={cn(
              'font-sans font-medium transition',
              selected
                ? 'text-brand-400'
                : 'text-slate-400 group-hover:text-white',
            )}
          >
            {selected ? 'Configure API →' : 'Select'}
          </span>
        ) : (
          <button
            type="button"
            className="font-sans text-xs text-brand-400 underline hover:text-brand-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
          >
            Notify Me
          </button>
        )}
      </div>
    </div>
  );
}
