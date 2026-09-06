import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = { title: 'Risk Disclosure' };

export default function RiskDisclosurePage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12 text-slate-300">
      <Link href="/signup" className="text-xs text-brand-400 hover:underline">
        ← Back
      </Link>
      <h1 className="mt-4 text-2xl font-bold text-white">Risk Disclosure</h1>
      <div className="mt-6 space-y-4 text-sm leading-relaxed">
        <p className="font-semibold text-paper">
          Trading in futures and options (F&amp;O) is high-risk and can result
          in losses that exceed your initial capital.
        </p>
        <p>
          Automated execution does not guarantee profit. Strategies that
          performed well historically can lose money in live markets. Stop
          losses can slip past their trigger in fast or gapping markets.
        </p>
        <p>
          You set your own risk limits (daily loss cap, per-position size, max
          open positions, margin utilisation). These reduce but do not eliminate
          risk. The kill switch halts new entries but cannot undo losses already
          realised.
        </p>
        <p>
          Only trade capital you can afford to lose. Barbell provides tooling,
          not investment advice, and does not recommend any specific trade or
          strategy.
        </p>
        <p className="text-xs text-slate-500">
          This summary is provided for transparency and is not financial advice.
        </p>
      </div>
    </main>
  );
}
