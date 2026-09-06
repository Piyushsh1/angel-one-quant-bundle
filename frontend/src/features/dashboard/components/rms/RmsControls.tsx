'use client';

import { useId, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

/**
 * Shared form primitives for the Risk Management System screen.
 *
 * These mirror the institutional-terminal look of the RMS mockup: dense mono
 * numerals, hairline borders, and an "edited" affordance so an operator always
 * sees which guardrails they've changed but not yet deployed.
 */

/** Marks a field whose local value differs from the last-saved server value. */
export function EditedTag() {
  return (
    <span className="rounded bg-brand-500/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-brand-400">
      edited
    </span>
  );
}

/** A labelled numeric input with an optional unit prefix/suffix. */
export function NumberField({
  label,
  hint,
  value,
  onChange,
  edited = false,
  min,
  max,
  step,
  unit,
  unitPosition = 'suffix',
  trailing,
}: {
  label: ReactNode;
  hint?: ReactNode;
  value: number;
  onChange: (value: number) => void;
  edited?: boolean;
  min?: number;
  max?: number;
  step?: number;
  unit?: ReactNode;
  unitPosition?: 'prefix' | 'suffix';
  /** Optional trailing label rendered inside the field, e.g. "LOTS". */
  trailing?: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between gap-2 text-xs font-medium text-slate-300">
        <span>{label}</span>
        {edited && <EditedTag />}
      </span>
      <div
        className={cn(
          'flex items-center rounded-lg border bg-surface-900/90 transition-colors focus-within:ring-2 focus-within:ring-brand-500/40',
          edited ? 'border-brand-500/60' : 'border-surface-700/80',
        )}
      >
        {unit && unitPosition === 'prefix' && (
          <span className="pl-3 text-sm text-slate-500">{unit}</span>
        )}
        <input
          type="number"
          inputMode="decimal"
          min={min}
          max={max}
          step={step}
          value={Number.isFinite(value) ? value : 0}
          onChange={(e) => onChange(Number(e.target.value))}
          className="w-full bg-transparent px-3 py-2.5 font-mono text-sm text-white focus:outline-none"
        />
        {trailing && (
          <span className="pr-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">
            {trailing}
          </span>
        )}
        {unit && unitPosition === 'suffix' && (
          <span className="pr-3 text-sm text-slate-500">{unit}</span>
        )}
      </div>
      {hint && <span className="mt-1 block text-[11px] text-slate-500">{hint}</span>}
    </label>
  );
}

/** A labelled text input (used for the webhook dispatch URI). */
export function TextField({
  label,
  hint,
  value,
  onChange,
  edited = false,
  placeholder,
  type = 'text',
  mono = false,
}: {
  label: ReactNode;
  hint?: ReactNode;
  value: string;
  onChange: (value: string) => void;
  edited?: boolean;
  placeholder?: string;
  type?: 'text' | 'url' | 'time';
  mono?: boolean;
}) {
  const hasLabel = Boolean(label);
  return (
    <label className="block">
      {(hasLabel || edited) && (
        <span className="mb-1 flex items-center justify-between gap-2 text-xs font-medium text-slate-300">
          <span>{label}</span>
          {edited && <EditedTag />}
        </span>
      )}
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          'w-full rounded-lg border bg-surface-900/90 px-3 py-2.5 text-sm text-white transition-colors',
          'placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500/40',
          mono && 'font-mono',
          edited ? 'border-brand-500/60' : 'border-surface-700/80',
        )}
      />
      {hint && <span className="mt-1 block text-[11px] text-slate-500">{hint}</span>}
    </label>
  );
}

/**
 * A stepper for small integer counts (e.g. consecutive-loss threshold), shown
 * as a pill in the mockup ("3 Trades").
 */
export function StepperPill({
  value,
  onChange,
  suffix,
  min = 1,
  max = 50,
  edited = false,
}: {
  value: number;
  onChange: (value: number) => void;
  suffix?: string;
  min?: number;
  max?: number;
  edited?: boolean;
}) {
  const clamp = (n: number) => Math.min(Math.max(n, min), max);
  return (
    <div
      className={cn(
        'inline-flex items-center gap-1 rounded-lg border bg-surface-900/90 px-1',
        edited ? 'border-brand-500/60' : 'border-surface-700/80',
      )}
    >
      <button
        type="button"
        aria-label="Decrease"
        onClick={() => onChange(clamp(value - 1))}
        className="flex h-7 w-7 items-center justify-center rounded text-slate-400 hover:bg-surface-800 hover:text-white focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand-400"
      >
        −
      </button>
      <span className="min-w-[3.5rem] text-center font-mono text-xs font-semibold text-white">
        {value}
        {suffix ? ` ${suffix}` : ''}
      </span>
      <button
        type="button"
        aria-label="Increase"
        onClick={() => onChange(clamp(value + 1))}
        className="flex h-7 w-7 items-center justify-center rounded text-slate-400 hover:bg-surface-800 hover:text-white focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand-400"
      >
        +
      </button>
    </div>
  );
}

/** An accessible on/off switch, the green toggle used throughout the mockup. */
export function Toggle({
  checked,
  onChange,
  label,
  edited = false,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Accessible label; visually hidden — a nearby row already names it. */
  label: string;
  edited?: boolean;
}) {
  return (
    <span className="inline-flex items-center gap-2">
      {edited && <EditedTag />}
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2 focus-visible:ring-offset-surface-850',
          checked ? 'bg-profit-strong' : 'bg-surface-700',
        )}
      >
        <span
          className={cn(
            'inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform',
            checked ? 'translate-x-5' : 'translate-x-0.5',
          )}
        />
      </button>
    </span>
  );
}

/**
 * A row that pairs an explanatory label/description with a trailing control
 * (usually a Toggle). Keeps the alerts and freeze rows visually consistent.
 */
export function ControlRow({
  title,
  description,
  control,
  accent,
}: {
  title: ReactNode;
  description?: ReactNode;
  control: ReactNode;
  /** Optional icon rendered before the title. */
  accent?: ReactNode;
}) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <div className="min-w-0">
        <p
          id={id}
          className="flex items-center gap-1.5 text-xs font-semibold text-slate-200"
        >
          {accent}
          {title}
        </p>
        {description && (
          <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500">
            {description}
          </p>
        )}
      </div>
      <div className="shrink-0 pt-0.5">{control}</div>
    </div>
  );
}

/** A slider with a live value read-out, used for percentage tolerances. */
export function SliderField({
  label,
  hint,
  value,
  onChange,
  min,
  max,
  step,
  format,
  edited = false,
  rightLabel,
}: {
  label: ReactNode;
  hint?: ReactNode;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step: number;
  format: (value: number) => string;
  edited?: boolean;
  /** Small caption at the top-right (e.g. "Auto reject if executed > limit"). */
  rightLabel?: ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-end justify-between gap-2">
        <span className="flex items-center gap-2 text-xs font-medium text-slate-300">
          {label}
          {edited && <EditedTag />}
        </span>
        {rightLabel && (
          <span className="text-[10px] text-slate-500">{rightLabel}</span>
        )}
      </div>
      <div className="flex items-center gap-3">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-surface-700 accent-profit-strong"
          aria-label={typeof label === 'string' ? label : undefined}
        />
        <span className="w-16 shrink-0 text-right font-mono text-sm font-semibold text-profit">
          {format(value)}
        </span>
      </div>
      {hint && <span className="mt-1.5 block text-[11px] text-slate-500">{hint}</span>}
    </div>
  );
}
