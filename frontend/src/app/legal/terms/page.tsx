import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = { title: 'Terms of Service' };

export default function TermsPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12 text-slate-300">
      <Link href="/signup" className="text-xs text-brand-400 hover:underline">
        ← Back
      </Link>
      <h1 className="mt-4 text-2xl font-bold text-white">Terms of Service</h1>
      <div className="mt-6 space-y-4 text-sm leading-relaxed">
        <p>
          Barbell is a non-custodial trade-execution tool. You connect your own
          broker account and retain full custody of your capital at all times.
          Barbell places orders on your behalf according to the strategies and
          risk parameters you configure; it never holds, transfers, or has
          withdrawal access to your funds.
        </p>
        <p>
          Trading in equities and derivatives carries substantial risk of loss.
          You are solely responsible for the trades executed through your
          connected account, for the risk limits you set, and for compliance
          with the rules of your broker and exchange.
        </p>
        <p>
          The service is provided &ldquo;as is&rdquo; without warranty. To the
          maximum extent permitted by law, Barbell is not liable for trading
          losses, missed trades, broker or exchange outages, or data delays.
        </p>
        <p>
          You must be eligible to trade under the laws of your jurisdiction and
          the terms of your broker. Misuse, including attempts to manipulate
          markets or circumvent broker controls, will result in termination.
        </p>
        <p className="text-xs text-slate-500">
          This summary is provided for transparency and is not legal advice.
        </p>
      </div>
    </main>
  );
}
