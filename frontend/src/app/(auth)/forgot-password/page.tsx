import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = { title: 'Reset Password' };

/**
 * Password reset.
 *
 * Self-service email reset is not yet available, so rather than showing a form
 * that pretends to send a link, this page routes the user to support — an
 * honest path that actually resolves the request.
 */
export default function ForgotPasswordPage() {
  return (
    <div className="mx-auto w-full max-w-md rounded-2xl border border-surface-700/60 bg-surface-900 p-6">
      <h1 className="text-lg font-bold text-white">Reset your password</h1>
      <p className="mt-2 text-sm leading-relaxed text-slate-400">
        Self-service password reset isn&apos;t available yet. To reset your
        password, email{' '}
        <a
          href="mailto:support@barbell.trade"
          className="font-medium text-brand-400 hover:underline"
        >
          support@barbell.trade
        </a>{' '}
        from your registered address and we&apos;ll help you regain access.
      </p>
      <Link
        href="/login"
        className="mt-6 inline-block text-sm font-medium text-brand-400 hover:underline"
      >
        ← Back to sign in
      </Link>
    </div>
  );
}
