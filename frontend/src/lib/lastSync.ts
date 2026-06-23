const PREFIX = 'lastSync:';

/**
 * Read the persisted "last synced at" epoch-ms for ``scopeKey``; null when
 * absent, malformed, or storage is unavailable (private mode). Impure (reads
 * localStorage). The try/catch mirrors lib/i18n/detect.ts so a storage failure
 * never breaks the caller.
 */
export function readLastSyncedAt(scopeKey: string): number | null {
  try {
    const raw = window.localStorage.getItem(PREFIX + scopeKey);
    if (raw === null) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

/**
 * Persist ``ms`` as the last-synced time for ``scopeKey``. Impure (writes
 * localStorage); swallows storage errors so a quota / private-mode failure never
 * breaks the refresh — the in-memory state still updates this session.
 */
export function writeLastSyncedAt(scopeKey: string, ms: number): void {
  try {
    window.localStorage.setItem(PREFIX + scopeKey, String(ms));
  } catch {
    // localStorage unavailable — the in-memory state still updates this session.
  }
}
