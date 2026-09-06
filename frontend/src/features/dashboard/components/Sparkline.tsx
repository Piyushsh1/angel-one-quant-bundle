import { cn } from '@/lib/cn';

/**
 * A tiny inline trend line. Decorative — the adjacent numeric change carries
 * the meaning, so this is hidden from assistive tech.
 */
export function Sparkline({
  path,
  className,
  viewBox = '0 0 48 16',
}: {
  path: string;
  className?: string;
  viewBox?: string;
}) {
  return (
    <svg
      aria-hidden="true"
      className={cn('overflow-visible', className)}
      fill="none"
      viewBox={viewBox}
    >
      <path
        d={path}
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
