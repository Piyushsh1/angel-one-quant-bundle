/**
 * Query-key factory.
 *
 * Centralising keys prevents the classic bug where one call site invalidates
 * `['user']` while another cached under `['currentUser']`, so the UI silently
 * shows stale data. Keys are `as const` so TypeScript catches typos.
 */
export const authKeys = {
  all: ['auth'] as const,
  currentUser: () => [...authKeys.all, 'currentUser'] as const,
} as const;
