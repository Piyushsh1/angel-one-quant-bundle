import { SUPPORTED_BROKERS } from '@/config/brokers';
import { cn } from '@/lib/cn';

interface Pillar {
  title: string;
  body: string;
  accent: 'brand' | 'profit';
  icon: React.ReactNode;
}

const PILLARS: Pillar[] = [
  {
    title: 'Your capital stays yours',
    body: 'Orders execute in your own broker account. Barbell never holds or moves your funds.',
    accent: 'brand',
    icon: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
      />
    ),
  },
  {
    title: 'Rules, not impulses',
    body: 'Opening-range breakouts with a regime filter that stands down on quiet days.',
    accent: 'profit',
    icon: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
      />
    ),
  },
  {
    title: 'Costs counted honestly',
    body: 'Every result is net of brokerage, STT, GST and slippage — so paper matches reality.',
    accent: 'brand',
    icon: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M9 7h6m-6 4h6m-7 4h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v8a2 2 0 002 2zm0 0v2a2 2 0 002 2h6"
      />
    ),
  },
];

/**
 * Left-hand narrative panel on the auth screens.
 *
 * Server component: entirely static, so it ships no JavaScript.
 *
 * Every claim here is deliberately verifiable from the product's own
 * behaviour. No execution-volume totals, uptime percentages or latency figures
 * appear, because for a financial product an unverifiable performance claim is
 * a compliance liability, not marketing copy.
 */
export function HeroPanel() {
  return (
    <section className="flex flex-col justify-center space-y-8">
      <div className="space-y-4">
        <p className="inline-flex items-center gap-2.5 rounded-full border border-brand-600/40 bg-brand-500/10 px-3 py-1.5 font-mono text-xs font-semibold tracking-wider text-brand-400">
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-400"
          />
          AUTOMATED TRADING · NON-CUSTODIAL
        </p>

        <h2 className="text-4xl font-extrabold leading-[1.12] tracking-tight text-white sm:text-5xl xl:text-6xl">
          Automated trading.
          <br />
          <span className="shimmer-text">In your account.</span>
          <br />
          On your terms.
        </h2>

        <p className="max-w-2xl text-base leading-relaxed text-slate-300/90 sm:text-lg">
          Connect your own broker via its official API and run systematic
          strategies on your own capital.{' '}
          <span className="font-medium text-white">
            Your funds never leave your broker.
          </span>
        </p>
      </div>

      <div className="space-y-2.5">
        <p className="font-mono text-xs font-medium uppercase tracking-wider text-slate-400">
          Broker integrations
        </p>
        <ul className="flex flex-wrap items-center gap-2.5">
          {SUPPORTED_BROKERS.map((broker) => (
            <li
              key={broker.id}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium',
                broker.status === 'available'
                  ? 'border-surface-700 bg-surface-850/90 text-slate-200'
                  : 'border-surface-800 bg-surface-900/60 text-slate-500',
              )}
            >
              <span
                aria-hidden="true"
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: broker.dotColor }}
              />
              <span>{broker.name}</span>
              {broker.status === 'coming_soon' && (
                <span className="font-mono text-[10px] uppercase tracking-wide text-slate-600">
                  soon
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>

      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {PILLARS.map((pillar) => (
          <li
            key={pillar.title}
            className={cn(
              'group rounded-xl border border-surface-700/70 bg-surface-850/70 p-4 backdrop-blur transition-all duration-300',
              pillar.accent === 'brand'
                ? 'hover:border-brand-500/50'
                : 'hover:border-profit/50',
            )}
          >
            <span
              className={cn(
                'mb-3 flex h-9 w-9 items-center justify-center rounded-lg border transition-transform group-hover:scale-105',
                pillar.accent === 'brand'
                  ? 'border-brand-600/60 bg-brand-500/10 text-brand-400'
                  : 'border-profit-strong/60 bg-profit-dim/40 text-profit',
              )}
            >
              <svg
                aria-hidden="true"
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                {pillar.icon}
              </svg>
            </span>
            <h3 className="mb-1 text-sm font-bold text-white">{pillar.title}</h3>
            <p className="text-xs leading-relaxed text-slate-400">{pillar.body}</p>
          </li>
        ))}
      </ul>

      <div className="border-t border-surface-700/60 pt-4">
        <blockquote className="border-l-2 border-brand-500/70 pl-3 text-xs italic text-slate-400">
          “Discipline compounds what motivation cannot.”
        </blockquote>
      </div>
    </section>
  );
}
