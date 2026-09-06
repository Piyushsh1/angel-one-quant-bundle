/**
 * Error taxonomy.
 *
 * Every failure that reaches the UI is one of these, so components can branch
 * on a discriminated `kind` rather than string-matching messages. This is what
 * lets a form decide between "show a field error", "show a banner", and "send
 * the user back to sign in".
 */
export type ApiErrorKind =
  /** 400/422 — the request was understood but rejected. May carry field errors. */
  | 'validation'
  /** 401 — no valid session. The caller should redirect to sign-in. */
  | 'unauthenticated'
  /** 403 — authenticated but not permitted. */
  | 'forbidden'
  /** 404 */
  | 'not_found'
  /** 409 — e.g. email already registered. */
  | 'conflict'
  /** 429 — rate limited. `retryAfterSeconds` may be set. */
  | 'rate_limited'
  /** 5xx */
  | 'server'
  /** Request never completed: offline, DNS, CORS, abort. */
  | 'network'
  /** Anything we could not classify. */
  | 'unknown';

/** Field-level messages, keyed by form field name. */
export type FieldErrors = Record<string, string[]>;

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly fieldErrors: FieldErrors;
  readonly retryAfterSeconds: number | null;

  constructor(params: {
    kind: ApiErrorKind;
    message: string;
    status?: number | null;
    fieldErrors?: FieldErrors;
    retryAfterSeconds?: number | null;
  }) {
    super(params.message);
    this.name = 'ApiError';
    this.kind = params.kind;
    this.status = params.status ?? null;
    this.fieldErrors = params.fieldErrors ?? {};
    this.retryAfterSeconds = params.retryAfterSeconds ?? null;
  }

  /** True when retrying the same request could plausibly succeed. */
  get isRetryable(): boolean {
    return (
      this.kind === 'network' ||
      this.kind === 'server' ||
      this.kind === 'rate_limited'
    );
  }
}

const KIND_BY_STATUS: Record<number, ApiErrorKind> = {
  400: 'validation',
  401: 'unauthenticated',
  403: 'forbidden',
  404: 'not_found',
  409: 'conflict',
  422: 'validation',
  429: 'rate_limited',
};

export function kindFromStatus(status: number): ApiErrorKind {
  return KIND_BY_STATUS[status] ?? (status >= 500 ? 'server' : 'unknown');
}

/**
 * Human-facing copy for an error.
 *
 * Deliberately vague on auth failures: "email or password is incorrect" rather
 * than "no such user", so the endpoint cannot be used to enumerate accounts.
 */
export function userFacingMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.kind) {
      case 'network':
        return 'Cannot reach the server. Check your connection and try again.';
      case 'unauthenticated':
        return 'That email or password is incorrect.';
      case 'forbidden':
        return 'You do not have access to that.';
      case 'conflict':
        return error.message || 'That already exists.';
      case 'rate_limited':
        return error.retryAfterSeconds
          ? `Too many attempts. Try again in ${error.retryAfterSeconds}s.`
          : 'Too many attempts. Please wait a moment and try again.';
      case 'server':
        return 'Something went wrong on our end. Please try again shortly.';
      default:
        return error.message || 'Something went wrong.';
    }
  }
  return 'Something went wrong. Please try again.';
}
