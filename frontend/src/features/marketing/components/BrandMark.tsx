import Link from 'next/link';

/**
 * The barbell glyph: two weighted candlestick bodies on a shared wick.
 * Decorative, so it is hidden from assistive tech — the adjacent wordmark
 * carries the accessible name.
 */
export function BrandMark({ href = '/' }: { href?: string }) {
  return (
    <Link
      href={href}
      className="group flex items-center gap-3 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
    >
      <span
        aria-hidden="true"
        className="relative flex h-8 w-8 items-center justify-center"
      >
        <span className="absolute h-7 w-[3px] rounded-full bg-brand-400 shadow-[0_0_10px_#22d3ee]" />
        <span className="absolute left-1 h-4 w-2 rounded-sm bg-profit shadow-[0_0_8px_#34d399]" />
        <span className="absolute right-1 h-5 w-2 rounded-sm bg-brand-500" />
      </span>
      <span className="text-xl font-bold tracking-tight text-white transition-colors group-hover:text-brand-400">
        Barbell
      </span>
    </Link>
  );
}
