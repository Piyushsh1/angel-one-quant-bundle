import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = { title: 'Support' };

export default function SupportPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12 text-slate-300">
      <Link href="/login" className="text-xs text-brand-400 hover:underline">
        ← Back
      </Link>
      <h1 className="mt-4 text-2xl font-bold text-white">Support</h1>
      <div className="mt-6 space-y-4 text-sm leading-relaxed">
        <p>Need help connecting a broker, linking Telegram, or with your account?</p>
        <ul className="space-y-2">
          <li>
            <span className="text-slate-400">Email:</span>{' '}
            <a
              href="mailto:support@barbell.trade"
              className="font-medium text-brand-400 hover:underline"
            >
              support@barbell.trade
            </a>
          </li>
          <li>
            <span className="text-slate-400">Broker setup:</span> see the
            per-field help text on the{' '}
            <Link href="/onboarding/broker" className="text-brand-400 hover:underline">
              Connect Broker
            </Link>{' '}
            screen.
          </li>
        </ul>
        <p className="text-xs text-slate-500">
          We aim to respond within one business day.
        </p>
      </div>
    </main>
  );
}
