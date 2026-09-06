import type { BrokerId } from '@/config/brokers';

/**
 * Per-broker logo marks.
 *
 * These are stylised approximations drawn inline, not the brokers' official
 * trademarked assets — enough to make each card instantly recognisable without
 * shipping (or misusing) real brand files. Each glyph fills a 100×100 viewBox
 * so the wrapper controls the rendered size. Decorative: the broker name label
 * beside it carries the accessible name, so these are `aria-hidden`.
 */
function AngelOne() {
  return (
    <svg aria-hidden="true" className="h-8 w-8" fill="none" viewBox="0 0 100 100">
      <polygon points="50,15 62,35 38,35" fill="#FF5722" />
      <polygon points="38,35 50,55 26,55" fill="#FFA000" />
      <polygon points="62,35 74,55 50,55" fill="#FF7043" />
      <polygon points="26,55 38,75 14,75" fill="#00BCD4" />
      <polygon points="50,55 62,75 38,75" fill="#009688" />
      <polygon points="74,55 86,75 62,75" fill="#4CAF50" />
    </svg>
  );
}

function Zerodha() {
  return (
    <svg aria-hidden="true" className="h-8 w-8" fill="none" viewBox="0 0 100 100">
      <path d="M20 20 L75 20 C75 20 80 50 50 80 L20 20 Z" fill="#0088FF" />
      <path d="M50 80 L80 80 L75 20 Z" fill="#0066CC" />
    </svg>
  );
}

function Groww() {
  return (
    <span
      aria-hidden="true"
      className="relative flex h-8 w-8 items-center justify-center overflow-hidden rounded-full"
    >
      <span className="absolute inset-0 bg-[#00D09C]" />
      <span className="absolute right-0 top-0 h-4 w-8 bg-[#5367FF]" />
    </span>
  );
}

function Upstox() {
  return (
    <span
      aria-hidden="true"
      className="flex h-8 w-8 items-center justify-center rounded-full bg-[#7B3FE4] text-sm font-extrabold tracking-tighter text-white"
    >
      up
    </span>
  );
}

function Fyers() {
  return (
    <span
      aria-hidden="true"
      className="flex h-8 w-8 items-center justify-center font-mono text-xl font-bold tracking-tighter text-[#3b82f6]"
    >
      FF
    </span>
  );
}

function Dhan() {
  return (
    <span
      aria-hidden="true"
      className="flex h-8 w-8 items-center justify-center rounded-full border border-[#1A73E8]/40 bg-[#1A73E8]/20 text-lg font-bold text-[#22C55E]"
    >
      ध
    </span>
  );
}

const LOGOS: Record<BrokerId, () => React.ReactElement> = {
  angelone: AngelOne,
  zerodha: Zerodha,
  groww: Groww,
  upstox: Upstox,
  fyers: Fyers,
  dhan: Dhan,
};

export function BrokerLogo({ id }: { id: BrokerId }) {
  const Mark = LOGOS[id];
  return <Mark />;
}
