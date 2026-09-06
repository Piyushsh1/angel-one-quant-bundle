import type { RiskParameters } from '@/features/dashboard/schemas/dashboard.schema';

/**
 * The shape every RMS panel receives: the live edit buffer, the last-saved
 * server values (for the "edited" affordance), and a typed setter. Panels are
 * pure presentation — all persistence lives in the orchestrating form.
 */
export interface RmsPanelProps {
  form: RiskParameters;
  saved: RiskParameters | null;
  setValue: <K extends keyof RiskParameters>(
    key: K,
    value: RiskParameters[K],
  ) => void;
}

/** True when a field's buffered value differs from the last-saved value. */
export function isEdited<K extends keyof RiskParameters>(
  form: RiskParameters,
  saved: RiskParameters | null,
  key: K,
): boolean {
  return saved ? form[key] !== saved[key] : false;
}
