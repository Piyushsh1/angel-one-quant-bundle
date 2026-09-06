import { env } from '@/lib/env';
import { ApiError, kindFromStatus, type FieldErrors } from '@/lib/errors';
import { z } from 'zod';

/**
 * The single HTTP boundary for the app.
 *
 * Every network call goes through here so that four concerns are handled in one
 * place instead of being re-implemented per feature:
 *
 *   1. Session cookies are always sent (`credentials: 'include'`).
 *   2. Failures are normalised into `ApiError` with a machine-readable `kind`.
 *   3. Responses are validated against a Zod schema, so a backend contract
 *      change surfaces as a clear parse error rather than an `undefined`
 *      dereference three components deep.
 *   4. Requests time out — a hung fetch would otherwise leave a form spinner
 *      running forever.
 *
 * Auth deliberately uses httpOnly cookies rather than storing a JWT in
 * localStorage: a token readable by JavaScript is exfiltratable by any XSS,
 * and this session can move money.
 */

const DEFAULT_TIMEOUT_MS = 15_000;

/** Name of the non-httpOnly CSRF cookie the backend sets alongside the session. */
const CSRF_COOKIE = 'barbell_csrf';

/** Read the CSRF token the backend set as a readable cookie. '' if absent. */
function readCsrfCookie(): string {
  if (typeof document === 'undefined') return '';
  const match = document.cookie
    .split('; ')
    .find((row) => row.startsWith(`${CSRF_COOKIE}=`));
  return match ? decodeURIComponent(match.split('=')[1] ?? '') : '';
}

/** Shape the backend uses for errors. Kept permissive on purpose. */
const errorBodySchema = z.object({
  detail: z.string().optional(),
  message: z.string().optional(),
  code: z.string().optional(),
  /** FastAPI-style: { field: ["msg", ...] } */
  errors: z.record(z.array(z.string())).optional(),
});

interface RequestOptions<TResponse> {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  /** Schema the successful response must satisfy. */
  schema: z.ZodType<TResponse>;
  signal?: AbortSignal;
  timeoutMs?: number;
  headers?: Record<string, string>;
}

async function parseErrorResponse(
  response: Response,
): Promise<{ message: string; fieldErrors: FieldErrors }> {
  try {
    const raw: unknown = await response.json();
    const parsed = errorBodySchema.safeParse(raw);
    if (parsed.success) {
      return {
        message:
          parsed.data.detail ??
          parsed.data.message ??
          `Request failed (${response.status})`,
        fieldErrors: parsed.data.errors ?? {},
      };
    }
  } catch {
    // Body was empty or not JSON — fall through to the status-based message.
  }
  return {
    message: `Request failed (${response.status})`,
    fieldErrors: {},
  };
}

/**
 * Combine an external abort signal with an internal timeout so whichever fires
 * first cancels the request.
 */
function buildSignal(
  external: AbortSignal | undefined,
  timeoutMs: number,
): { signal: AbortSignal; cleanup: () => void } {
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(new DOMException('Request timed out', 'TimeoutError')),
    timeoutMs,
  );

  const onExternalAbort = () => controller.abort(external?.reason);
  external?.addEventListener('abort', onExternalAbort, { once: true });

  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timer);
      external?.removeEventListener('abort', onExternalAbort);
    },
  };
}

export async function apiRequest<TResponse>(
  path: string,
  options: RequestOptions<TResponse>,
): Promise<TResponse> {
  const {
    method = 'GET',
    body,
    schema,
    signal: externalSignal,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    headers = {},
  } = options;

  const url = `${env.NEXT_PUBLIC_API_BASE_URL}${path}`;
  const { signal, cleanup } = buildSignal(externalSignal, timeoutMs);

  // Build headers explicitly as a string map so no key is ever `undefined`
  // (which strict TS rejects for HeadersInit).
  const requestHeaders: Record<string, string> = {
    Accept: 'application/json',
    ...headers,
  };
  if (body !== undefined) {
    requestHeaders['Content-Type'] = 'application/json';
  }
  // CSRF double-submit: echo the readable CSRF cookie back in a header on
  // state-changing requests. The backend rejects the request unless this
  // matches the token bound to the session server-side.
  if (method !== 'GET') {
    requestHeaders['X-CSRF-Token'] = readCsrfCookie();
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      // httpOnly session cookie travels with every request.
      credentials: 'include',
      headers: requestHeaders,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      signal,
      cache: 'no-store',
    });
  } catch (cause) {
    cleanup();
    if (cause instanceof DOMException && cause.name === 'AbortError') {
      // Caller-initiated cancellation must propagate so React Query can
      // distinguish it from a genuine failure.
      throw cause;
    }
    throw new ApiError({
      kind: 'network',
      message: 'Network request failed',
      status: null,
    });
  } finally {
    cleanup();
  }

  if (!response.ok) {
    const { message, fieldErrors } = await parseErrorResponse(response);
    const retryAfter = response.headers.get('Retry-After');
    throw new ApiError({
      kind: kindFromStatus(response.status),
      message,
      status: response.status,
      fieldErrors,
      retryAfterSeconds: retryAfter ? Number.parseInt(retryAfter, 10) : null,
    });
  }

  // 204 and other empty bodies: let the schema decide whether that is valid.
  if (response.status === 204) {
    return schema.parse(undefined);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError({
      kind: 'server',
      message: 'Server returned a malformed response',
      status: response.status,
    });
  }

  const result = schema.safeParse(payload);
  if (!result.success) {
    // A contract mismatch is a bug, not user error. Surface it loudly in dev
    // and generically in production.
    if (process.env.NODE_ENV !== 'production') {
      // eslint-disable-next-line no-console
      console.error('Response schema mismatch', {
        path,
        issues: result.error.issues,
      });
    }
    throw new ApiError({
      kind: 'server',
      message: 'Server returned an unexpected response',
      status: response.status,
    });
  }

  return result.data;
}

export const api = {
  get: <T>(path: string, opts: Omit<RequestOptions<T>, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...opts, method: 'GET' }),
  post: <T>(path: string, opts: Omit<RequestOptions<T>, 'method'>) =>
    apiRequest<T>(path, { ...opts, method: 'POST' }),
  patch: <T>(path: string, opts: Omit<RequestOptions<T>, 'method'>) =>
    apiRequest<T>(path, { ...opts, method: 'PATCH' }),
  delete: <T>(path: string, opts: Omit<RequestOptions<T>, 'method'>) =>
    apiRequest<T>(path, { ...opts, method: 'DELETE' }),
};
