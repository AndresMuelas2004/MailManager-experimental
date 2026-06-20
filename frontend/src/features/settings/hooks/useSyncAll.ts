import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { listMailboxes } from '../../../api/endpoints/mailboxes';
import { syncEmailMetadata } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';

type SyncAllState = {
  running: boolean;
  doneCount: number;
  totalCount: number;
  // Listing-fetch failure (the run never started) — rendered via its message.
  error: UiError | null;
  // At least one mailbox sync failed; the run still finished. Distinct from
  // ``error`` because there is no backend message to render — the page shows a
  // translated notice instead.
  partialError: boolean;
  // True once a run finished with every mailbox synced successfully — drives
  // the "synchronization complete" message in the page.
  succeeded: boolean;
};

type UseSyncAllReturn = SyncAllState & {
  syncAll: () => Promise<void>;
};

const INITIAL: SyncAllState = {
  running: false,
  doneCount: 0,
  totalCount: 0,
  error: null,
  partialError: false,
  succeeded: false,
};

/**
 * Orchestrates "sync everything": lists the user's mailboxes and fires one
 * ``sync-metadata`` per mailbox (no ``account_id`` → every account of the
 * mailbox syncs). Uses ``Promise.allSettled`` so a single failing mailbox does
 * not abort the rest, exposing ``doneCount`` / ``totalCount`` for the progress
 * indicator. On completion it invalidates the same listing caches a single
 * sync does (the standard blast radius, frontend_guide §2) so the inbox
 * refreshes with the newly pulled mail.
 */
export default function useSyncAll(): UseSyncAllReturn {
  const queryClient = useQueryClient();
  const [state, setState] = useState<SyncAllState>(INITIAL);

  const syncAll = useCallback(async () => {
    setState({ ...INITIAL, running: true });

    let mailboxIds: string[];
    try {
      const mailboxes = await listMailboxes();
      mailboxIds = mailboxes.map((m) => m.mailbox_id);
    } catch (err) {
      setState({ ...INITIAL, error: toUiError(err) });
      return;
    }

    setState({ ...INITIAL, running: true, totalCount: mailboxIds.length });

    const results = await Promise.allSettled(
      mailboxIds.map(async (id) => {
        try {
          await syncEmailMetadata(id);
        } finally {
          // Advance the progress counter as each mailbox settles, regardless
          // of success, so the indicator reaches total even on partial failure.
          setState((prev) => ({ ...prev, doneCount: prev.doneCount + 1 }));
        }
      }),
    );
    const anyFailed = results.some((r) => r.status === 'rejected');

    // Refresh the listing caches once the whole fan-out has settled (same
    // prefixes a single ``sync-metadata`` invalidates).
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['emails'] }),
      queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] }),
    ]);

    setState((prev) => ({
      ...prev,
      running: false,
      partialError: anyFailed,
      succeeded: !anyFailed,
    }));
  }, [queryClient]);

  return { ...state, syncAll };
}
