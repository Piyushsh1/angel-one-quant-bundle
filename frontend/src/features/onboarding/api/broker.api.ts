import { api } from '@/lib/api-client';
import { z } from 'zod';
import type { BrokerId } from '@/config/brokers';

/**
 * Broker-connection data layer.
 *
 * The credential payload is sent once, over HTTPS, to the backend, which
 * encrypts it before storage and never returns it. The frontend must never
 * cache or log these values.
 */

const connectResponseSchema = z.object({
  connected: z.boolean(),
  brokerId: z.string(),
  // Masked identifiers the backend deems safe to show back, e.g. "A12***78".
  maskedClientId: z.string().optional(),
  accountName: z.string().optional(),
  availableMargin: z.number().optional(),
});

export type ConnectBrokerResult = z.infer<typeof connectResponseSchema>;

export interface ConnectBrokerInput {
  brokerId: BrokerId;
  /** Field name → value, shaped by the broker's credential list. */
  credentials: Record<string, string>;
}

export async function connectBroker(
  input: ConnectBrokerInput,
  signal?: AbortSignal,
): Promise<ConnectBrokerResult> {
  return api.post('/broker/connect', {
    body: input,
    schema: connectResponseSchema,
    signal,
    // Verifying credentials involves a live broker login server-side, which is
    // slower than a normal request — allow more time before giving up.
    timeoutMs: 30_000,
  });
}
