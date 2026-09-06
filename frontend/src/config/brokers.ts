/**
 * Broker registry.
 *
 * Single source of truth for which brokers exist and what each needs in order
 * to connect. The onboarding credential form is generated from `credentials`,
 * so adding a broker is a data change here rather than new form code.
 *
 * `status` is honest on purpose: a broker is only `available` once its adapter
 * is implemented and tested end to end. Listing an unimplemented broker as
 * available would let a user hand over API keys to something that cannot use
 * them.
 */

export type BrokerId = 'angelone' | 'zerodha' | 'upstox' | 'groww' | 'dhan' | 'fyers';

export type BrokerStatus = 'available' | 'coming_soon';

/** How the user authorises us to act on their account. */
export type AuthKind =
  /** User pastes long-lived API credentials, including a TOTP secret. */
  | 'api_credentials'
  /** Redirect handshake; we never see the user's password. Preferred. */
  | 'oauth_redirect';

export interface CredentialField {
  name: string;
  label: string;
  /** `secret` fields are masked in the UI and never logged. */
  type: 'text' | 'secret';
  placeholder: string;
  /** Where the user finds this value in the broker's own portal. */
  help: string;
}

export interface Broker {
  id: BrokerId;
  name: string;
  status: BrokerStatus;
  authKind: AuthKind;
  /** Accent used for the broker's chip. */
  dotColor: string;
  credentials: CredentialField[];
}

const API_KEY: CredentialField = {
  name: 'apiKey',
  label: 'API Key',
  type: 'text',
  placeholder: 'xxxxxxxxxxxx',
  help: 'Create an app in the broker developer portal; the API key is shown on the app page.',
};

const API_SECRET: CredentialField = {
  name: 'apiSecret',
  label: 'API Secret',
  type: 'secret',
  placeholder: '••••••••••••',
  help: 'Shown once when the app is created. Regenerate it if you no longer have it.',
};

export const BROKERS: readonly Broker[] = [
  {
    id: 'angelone',
    name: 'Angel One',
    status: 'available',
    authKind: 'api_credentials',
    dotColor: '#ef4444',
    credentials: [
      API_KEY,
      {
        name: 'clientId',
        label: 'Client ID',
        type: 'text',
        placeholder: 'A12345678',
        help: 'Your Angel One login ID, shown on the SmartAPI dashboard.',
      },
      {
        name: 'mpin',
        label: 'MPIN',
        type: 'secret',
        placeholder: '••••',
        help: 'The MPIN you use to log in to Angel One — not your web password.',
      },
      {
        name: 'totpSecret',
        label: 'TOTP Secret',
        type: 'secret',
        placeholder: 'BASE32SECRET',
        help: 'The base32 secret shown when you enable TOTP — not the 6-digit code. Keep a copy in your authenticator app.',
      },
    ],
  },
  {
    id: 'zerodha',
    name: 'Zerodha Kite',
    status: 'available',
    // Kite uses a redirect handshake, so we never handle the user's password.
    authKind: 'oauth_redirect',
    dotColor: '#f59e0b',
    credentials: [API_KEY, API_SECRET],
  },
  {
    id: 'upstox',
    name: 'Upstox',
    status: 'available',
    authKind: 'oauth_redirect',
    dotColor: '#3b82f6',
    credentials: [API_KEY, API_SECRET],
  },
  {
    id: 'groww',
    name: 'Groww',
    status: 'available',
    authKind: 'api_credentials',
    dotColor: '#22c55e',
    credentials: [API_KEY, API_SECRET],
  },
  {
    id: 'dhan',
    name: 'Dhan',
    status: 'coming_soon',
    authKind: 'api_credentials',
    dotColor: '#a855f7',
    credentials: [API_KEY, API_SECRET],
  },
  {
    id: 'fyers',
    name: 'Fyers',
    status: 'coming_soon',
    authKind: 'oauth_redirect',
    dotColor: '#06b6d4',
    credentials: [API_KEY, API_SECRET],
  },
] as const;

/** Ordered for display: implemented brokers first. */
export const SUPPORTED_BROKERS = [...BROKERS].sort((a, b) =>
  a.status === b.status ? 0 : a.status === 'available' ? -1 : 1,
);

export function getBroker(id: BrokerId): Broker | undefined {
  return BROKERS.find((b) => b.id === id);
}
