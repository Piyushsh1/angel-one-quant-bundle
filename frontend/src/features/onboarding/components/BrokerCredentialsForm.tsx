'use client';

import { useState } from 'react';
import { getBroker, type BrokerId } from '@/config/brokers';
import { Input } from '@/components/ui/Input';
import { PasswordInput } from '@/components/ui/PasswordInput';
import { Button } from '@/components/ui/Button';
import { Alert } from '@/components/ui/Alert';
import { useConnectBroker } from '@/features/onboarding/hooks/useConnectBroker';
import { ApiError, userFacingMessage } from '@/lib/errors';

interface BrokerCredentialsFormProps {
  brokerId: BrokerId;
  onBack: () => void;
  /** Optional post-connect handler; when omitted, onboarding advances. */
  onConnected?: () => void;
}

/**
 * Credential form, generated from the selected broker's field list.
 *
 * A different broker means different fields with zero new code — the shape
 * comes from the registry. Secret fields use the masked PasswordInput; the
 * per-field help text tells the user exactly where to find each value in their
 * broker portal, which is the most common support question.
 *
 * The values live only in component state and the single submit request. They
 * are never persisted client-side or logged.
 */
export function BrokerCredentialsForm({
  brokerId,
  onBack,
  onConnected,
}: BrokerCredentialsFormProps) {
  const broker = getBroker(brokerId);
  const connect = useConnectBroker({ onConnected });

  // Local, uncontrolled-ish state keyed by field name.
  const [values, setValues] = useState<Record<string, string>>({});
  const [touched, setTouched] = useState<Record<string, boolean>>({});

  if (!broker) return null;

  const setField = (name: string, value: string) =>
    setValues((prev) => ({ ...prev, [name]: value }));

  const missingRequired = broker.credentials.filter(
    (f) => !(values[f.name] ?? '').trim(),
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (missingRequired.length > 0) {
      setTouched(
        Object.fromEntries(broker.credentials.map((f) => [f.name, true])),
      );
      return;
    }
    connect.mutate({
      brokerId,
      credentials: broker.credentials.reduce<Record<string, string>>(
        (acc, f) => {
          acc[f.name] = (values[f.name] ?? '').trim();
          return acc;
        },
        {},
      ),
    });
  };

  const error = connect.error;
  const fieldErrors =
    error instanceof ApiError ? error.fieldErrors : {};
  const showBanner =
    Boolean(error) && Object.keys(fieldErrors).length === 0;

  const isBusy = connect.isPending;

  return (
    <form onSubmit={handleSubmit} noValidate className="space-y-5">
      <div className="flex items-center gap-3 rounded-lg border border-surface-700/70 bg-surface-850/60 px-4 py-3">
        <span
          aria-hidden="true"
          className="h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: broker.dotColor }}
        />
        <span className="text-sm font-semibold text-slate-100">
          {broker.name}
        </span>
        <button
          type="button"
          onClick={onBack}
          disabled={isBusy}
          className="ml-auto rounded text-xs font-medium text-brand-400 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 disabled:opacity-50"
        >
          Change
        </button>
      </div>

      {/* Trust panel — restated at the moment of highest anxiety. */}
      <Alert tone="info" title="How we protect this">
        Credentials are encrypted before storage and used only to place orders
        you approve or automate. We can never withdraw or transfer your funds,
        and you can revoke access anytime from Settings.
      </Alert>

      {showBanner && (
        <Alert tone="error" title="Could not connect">
          {userFacingMessage(error)}
        </Alert>
      )}

      <div className="space-y-4">
        {broker.credentials.map((field) => {
          const value = values[field.name] ?? '';
          const showError =
            (touched[field.name] && !value.trim()) ||
            Boolean(fieldErrors[field.name]);
          const errorMsg = fieldErrors[field.name]?.[0]
            ?? (touched[field.name] && !value.trim()
              ? `${field.label} is required`
              : undefined);

          const commonProps = {
            label: field.label,
            placeholder: field.placeholder,
            hint: field.help,
            value,
            disabled: isBusy,
            required: true,
            error: showError ? errorMsg : undefined,
            onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
              setField(field.name, e.target.value),
            onBlur: () =>
              setTouched((prev) => ({ ...prev, [field.name]: true })),
          };

          return field.type === 'secret' ? (
            <PasswordInput key={field.name} {...commonProps} />
          ) : (
            <Input key={field.name} {...commonProps} mono={false} />
          );
        })}
      </div>

      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="ghost"
          onClick={onBack}
          disabled={isBusy}
        >
          Back
        </Button>
        <Button
          type="submit"
          fullWidth
          size="lg"
          isLoading={isBusy}
          loadingText="Verifying with broker…"
        >
          Verify &amp; connect
        </Button>
      </div>
    </form>
  );
}
