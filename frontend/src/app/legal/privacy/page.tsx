import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = { title: 'Privacy Policy' };

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12 text-slate-300">
      <Link href="/signup" className="text-xs text-brand-400 hover:underline">
        ← Back
      </Link>
      <h1 className="mt-4 text-2xl font-bold text-white">Privacy Policy</h1>
      <div className="mt-6 space-y-4 text-sm leading-relaxed">
        <p>
          We collect only what the service needs: your account email and name,
          your broker connection details, and the trade/position data required
          to run and display your terminal.
        </p>
        <p>
          Broker credentials (API keys, MPIN, TOTP secret, and session tokens)
          are encrypted at rest before they touch the database. They are used
          only to authenticate with your broker on your behalf and are never
          shared with third parties.
        </p>
        <p>
          Each user&apos;s data is isolated. One account can never read another
          account&apos;s positions, orders, P&amp;L, or credentials.
        </p>
        <p>
          If you link Telegram, your chat identifier is stored so we can deliver
          your own trade alerts to you. You can disconnect it at any time.
        </p>
        <p>
          You may request deletion of your account and associated data by
          contacting support.
        </p>
        <p className="text-xs text-slate-500">
          This summary is provided for transparency and is not legal advice.
        </p>
      </div>
    </main>
  );
}
