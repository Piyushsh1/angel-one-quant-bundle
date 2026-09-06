import { z } from 'zod';

/**
 * Validated public environment.
 *
 * Parsed once at module load so a missing or malformed variable fails loudly at
 * startup instead of surfacing as `undefined` inside a fetch URL at runtime.
 *
 * Only `NEXT_PUBLIC_*` values belong here — everything in this file is inlined
 * into the client bundle. Secrets must never appear.
 */
const publicEnvSchema = z.object({
  NEXT_PUBLIC_API_BASE_URL: z
    .string()
    .url('NEXT_PUBLIC_API_BASE_URL must be an absolute URL')
    .default('http://localhost:8000/api/v1'),
  NEXT_PUBLIC_APP_ENV: z
    .enum(['dev', 'preprod', 'prod'])
    .default('dev'),
});

const parsed = publicEnvSchema.safeParse({
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  NEXT_PUBLIC_APP_ENV: process.env.NEXT_PUBLIC_APP_ENV,
});

if (!parsed.success) {
  // Flatten to a readable list rather than dumping a Zod tree.
  const issues = parsed.error.issues
    .map((i) => `  · ${i.path.join('.')}: ${i.message}`)
    .join('\n');
  throw new Error(`Invalid public environment configuration:\n${issues}`);
}

export const env = parsed.data;

/** True when this build targets the real-money environment. */
export const isProd = env.NEXT_PUBLIC_APP_ENV === 'prod';
