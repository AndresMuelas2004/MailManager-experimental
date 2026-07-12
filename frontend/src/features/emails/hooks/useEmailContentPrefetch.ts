import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { emailContentQueryOptions } from './emailContentQueryOptions';
import type { EmailMetadataOut } from '../../../api/types/dto';

const RECENT_MS = 48 * 60 * 60 * 1000;

// Same scope as the backend's DB prefetch (unread, INBOX, ≤48h) so almost every
// warmed body is a local DB hit (~10 ms), not a provider round trip.
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

/**
 * Warm the in-memory content cache for the recent-unread INBOX rows currently on
 * the page. Privacy-safe: this fetches only the sanitized HTML (the ``/content``
 * JSON) and resolves its proxy URLs — it does NOT mount an iframe or create any
 * ``<img>``, so no image download is triggered and no sender is contacted.
 * Images are only requested (from our backend proxy) when the user actually
 * opens the email and the viewer renders.
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
    const handle = scheduleIdle(() => {
      for (const e of targets) {
        // prefetchQuery dedups: an entry already fresh in cache (staleTime
        // Infinity) is a no-op, so this never double-fetches an open email.
        void queryClient.prefetchQuery(
          emailContentQueryOptions(e.mailbox_id, e.account_id, e.provider_message_id),
        );
      }
    });
    return () => cancelIdle(handle);
    // ``emails`` is read inside but excluded from deps — ``targetKey`` is its
    // stable projection (mirrors the syncKey pattern in useVirtualMailboxEmails).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey, queryClient]);
}
