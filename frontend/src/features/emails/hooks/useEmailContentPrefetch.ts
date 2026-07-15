import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { emailContentQueryOptions } from './emailContentQueryOptions';
import type { EmailMetadataOut } from '../../../api/types/dto';

const RECENT_MS = 48 * 60 * 60 * 1000;

// Max ``/content`` prefetches in flight at once. The warm loop used to fire one
// request per target with no wait — up to a full page (~50) in a burst —
// flooding the synchronous backend precisely when the user opens an email, so
// the entry the viewer needs sat queued behind dozens of siblings. A small
// bound keeps every target warmed without starving the open. Exported so the
// concurrency test can assert the ceiling by name.
export const PREFETCH_CONCURRENCY = 3;

// Same scope as the backend's sync-time DB prefetch (unread, INBOX, ≤48h) so
// most warmed bodies are a local DB hit (~10 ms). Rows the backend already
// pre-cached (its 50 most-recent per account) are pure DB hits; a target beyond
// that set is a cache miss that triggers one provider fetch on the backend —
// acceptable and bounded by PREFETCH_CONCURRENCY, not a guaranteed local hit.
function isPrefetchTarget(e: EmailMetadataOut): boolean {
  return (
    !e.is_read &&
    e.box === 'ALL_MAIL' &&
    Date.now() - new Date(e.received_at).getTime() <= RECENT_MS
  );
}

// requestIdleCallback with a setTimeout fallback (Safari and jsdom lack it).
// Impure (schedules on the host); returns an opaque handle the caller cancels.
// The fallback is also what makes the prefetch fire under Vitest/jsdom.
function scheduleIdle(run: () => void): number {
  if (typeof window.requestIdleCallback === 'function') {
    return window.requestIdleCallback(run);
  }
  return window.setTimeout(run, 0);
}

function cancelIdle(handle: number): void {
  if (typeof window.cancelIdleCallback === 'function') {
    window.cancelIdleCallback(handle);
    return;
  }
  window.clearTimeout(handle);
}

// Bounded-concurrency pool: at most ``limit`` lanes run at once, each pulling
// the next item off a shared cursor until the list drains. ``isAborted`` is
// checked before starting each item so a page/target change or unmount stops
// launching new requests; the in-flight ``worker`` awaits are left to settle
// (TanStack Query dedups an already-warm entry — ``staleTime: Infinity`` makes
// the repeat a no-op — so nothing is double-fetched).
async function runWithConcurrency<T>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<unknown>,
  isAborted: () => boolean,
): Promise<void> {
  let cursor = 0;
  async function lane(): Promise<void> {
    while (cursor < items.length) {
      if (isAborted()) return;
      const item = items[cursor];
      cursor += 1;
      await worker(item);
    }
  }
  const laneCount = Math.min(limit, items.length);
  await Promise.all(Array.from({ length: laneCount }, () => lane()));
}

/**
 * Warm the in-memory content cache for the recent-unread INBOX rows currently on
 * the page. Privacy-safe: this fetches only the sanitized HTML (the ``/content``
 * JSON) and resolves its proxy URLs — it does NOT mount an iframe or create any
 * ``<img>``, so no image download is triggered and no sender is contacted.
 * Images are only requested (from our backend proxy) when the user actually
 * opens the email and the viewer renders.
 *
 * Warms EVERY eligible target, but with bounded concurrency
 * (``PREFETCH_CONCURRENCY``) rather than all at once, so the burst never
 * competes with the open the user is attempting.
 *
 * Called from the listing hooks (``useEmailList`` / ``useVirtualMailboxEmails``)
 * with the loaded page. A no-op for rows failing ``isPrefetchTarget`` (read /
 * non-INBOX / >48h), so Favoritos and older listings schedule nothing.
 */
export default function useEmailContentPrefetch(emails: EmailMetadataOut[]): void {
  const queryClient = useQueryClient();
  // Stable projection of the target subset: the effect re-fires only when the
  // SET of prefetch targets changes, not on unrelated re-renders.
  const targetKey = emails
    .filter(isPrefetchTarget)
    .map((e) => `${e.mailbox_id}:${e.account_id}:${e.provider_message_id}`)
    .sort()
    .join('|');

  useEffect(() => {
    const targets = emails.filter(isPrefetchTarget);
    if (targets.length === 0) return;
    // Flipped by the cleanup and consulted by the pool before each next item:
    // a page/target change or unmount stops the warm loop instead of draining a
    // stale page's remaining targets.
    let aborted = false;
    const handle = scheduleIdle(() => {
      void runWithConcurrency(
        targets,
        PREFETCH_CONCURRENCY,
        (e) =>
          queryClient.prefetchQuery(
            emailContentQueryOptions(e.mailbox_id, e.account_id, e.provider_message_id),
          ),
        () => aborted,
      );
    });
    return () => {
      aborted = true;
      cancelIdle(handle);
    };
    // ``emails`` is read inside but excluded from deps — ``targetKey`` is its
    // stable projection (mirrors the syncKey pattern in useVirtualMailboxEmails).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey, queryClient]);
}
