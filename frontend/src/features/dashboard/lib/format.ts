/**
 * Display formatters for the trading terminal.
 *
 * All money is Indian rupees rendered with the Indian digit grouping
 * (lakh/crore: 1,24,580.75, not 124,580.75). Signed helpers always show a
 * leading + or − so a value's direction is legible without colour alone — this
 * matters for colour-blind users and for screen readers reading the text.
 */

const INR = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** "1,24,580.75" — no currency symbol, caller prepends ₹ where wanted. */
export function formatAmount(value: number): string {
  return INR.format(Math.abs(value));
}

/** "+₹2,530.75" / "−₹258.00". Uses a true minus sign (−), not a hyphen. */
export function formatSignedInr(value: number): string {
  const sign = value < 0 ? '−' : '+';
  return `${sign}₹${formatAmount(value)}`;
}

/** "₹2,530.75" — unsigned, for prices and balances. */
export function formatInr(value: number): string {
  return `₹${formatAmount(value)}`;
}

/** "+0.54%" / "−1.05%". */
export function formatSignedPct(value: number): string {
  const sign = value < 0 ? '−' : '+';
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

/** Tailwind text colour class from a signed value, using semantic tokens. */
export function pnlColor(value: number): string {
  if (value > 0) return 'text-profit';
  if (value < 0) return 'text-loss';
  return 'text-slate-400';
}
