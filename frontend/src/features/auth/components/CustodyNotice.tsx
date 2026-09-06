/**
 * Non-custody disclosure.
 *
 * Shown on both auth screens because it is the single most important thing a
 * new user needs to understand before handing over broker API credentials:
 * the platform can place orders in their account but cannot move money out
 * of it.
 */
export function CustodyNotice() {
  return (
    <div className="flex items-start gap-2 text-[11px] leading-relaxed text-slate-400">
      <svg
        aria-hidden="true"
        className="mt-0.5 h-4 w-4 shrink-0 text-brand-400"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
        />
      </svg>
      <p>
        <strong className="font-medium text-slate-300">Non-custodial:</strong>{' '}
        Barbell connects only through your broker&apos;s official API. Orders are
        placed in your own account and we cannot withdraw or transfer your funds.
      </p>
    </div>
  );
}
