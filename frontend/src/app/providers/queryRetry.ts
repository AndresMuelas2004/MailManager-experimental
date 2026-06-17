import { ApiError } from '../../api/client/errors';

/**
 * Retry policy for read queries. A 429 (rate limit) must never be retried —
 * a retry only burns the limit faster and pushes the cooldown further out.
 * Every other failure keeps the previous behaviour of a single retry.
 *
 * Lives in its own module (not in `QueryProvider.tsx`) so the provider file
 * keeps exporting only its component and React Fast Refresh stays intact.
 */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status === 429) {
    return false;
  }
  return failureCount < 1;
}
