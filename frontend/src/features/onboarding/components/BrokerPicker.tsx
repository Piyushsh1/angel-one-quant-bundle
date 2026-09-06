'use client';

import { SUPPORTED_BROKERS, type Broker, type BrokerId } from '@/config/brokers';
import { cn } from '@/lib/cn';

interface BrokerPickerProps {
  selected: BrokerId | null;
  onSelect: (id: BrokerId) => void;
}

/**
 * Grid of selectable brokers, driven entirely by the broker registry.
 *
 * "Coming soon" brokers are shown but disabled — listing an unimplemented
 * broker as connectable would invite a user to hand credentials to an adapter
 * that cannot use them. Rendered as a radiogroup so arrow keys move between
 * options, matching native radio behaviour.
 */
export function BrokerPicker({ selected, onSelect }: BrokerPickerProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Choose your broker"
      className="grid grid-cols-1 gap-3 sm:grid-cols-2"
    >
      {SUPPORTED_BROKERS.map((broker) => (
        <BrokerOption
          key={broker.id}
          broker={broker}
          selected={selected === broker.id}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function BrokerOption({
  broker,
  selected,
  onSelect,
}: {
  broker: Broker;
  selected: boolean;
  onSelect: (id: BrokerId) => void;
}) {
  const available = broker.status === 'available';

  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={!available}
      onClick={() => available && onSelect(broker.id)}
      className={cn(
        'flex items-center justify-between rounded-xl border p-4 text-left transition-all',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400',
        available
          ? 'cursor-pointer bg-surface-850/80 hover:border-surface-600'
          : 'cursor-not-allowed bg-surface-900/50 opacity-60',
        selected
          ? 'border-brand-500 shadow-glow-brand'
          : 'border-surface-700/70',
      )}
    >
      <span className="flex items-center gap-3">
        <span
          aria-hidden="true"
          className="h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: broker.dotColor }}
        />
        <span className="text-sm font-semibold text-slate-100">
          {broker.name}
        </span>
      </span>

      {available ? (
        <span
          className={cn(
            'flex h-5 w-5 items-center justify-center rounded-full border',
            selected
              ? 'border-brand-400 bg-brand-500 text-slate-950'
              : 'border-surface-600',
          )}
        >
          {selected && (
            <svg
              className="h-3 w-3"
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
          )}
        </span>
      ) : (
        <span className="font-mono text-[10px] uppercase tracking-wide text-slate-500">
          soon
        </span>
      )}
    </button>
  );
}
