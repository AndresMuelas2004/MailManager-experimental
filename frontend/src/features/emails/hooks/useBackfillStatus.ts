import { useEffect, useMemo, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { getBackfillStatus } from '../../../api/endpoints/backfill';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { BackfillAccountStatus } from '../../../api/types/dto';

const POLL_INTERVAL_MS = 2500;

type UseBackfillStatusReturn = {
  // Per-account status keyed by account_id (only accounts with a job appear).
  statuses: Map<string, BackfillAccountStatus>;
  active: boolean;
  // A poll failure is surfaced here and never blocks the view — the listing is
  // a separate query that keeps showing the local copy.
  error: UiError | null;
};

/**
 * Poll the backfill-status endpoint while an account is still bulk-loading its
 * initial history, repainting the listings as the counter grows.
 *
 * Twin of ``features/accounts/hooks/useBackfillStatus`` — same endpoint AND the
 * same ``['backfill-status', mailboxId]`` query key, so TanStack Query dedupes
 * both mounts to ONE network poll and one cache entry. The two live apart (not
 * promoted to lib/) because ``features/emails`` cannot import from
 * ``features/accounts`` (features §6) and a hook that calls an endpoint cannot
 * live in ``lib/`` (lib §3.3). The shared piece is the endpoint + DTO in api/.
 */
export default function useBackfillStatus(mailboxId: string): UseBackfillStatusReturn {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ['backfill-status', mailboxId] as const,
    queryFn: ({ signal }) => getBackfillStatus(mailboxId, signal),
    enabled: mailboxId.length > 0,
    // Only poll while a backfill is active; stop when it finishes. A freshly
    // enqueued job would otherwise never be polled (the query goes idle after
    // the first no-jobs fetch), so ``addAccount`` invalidates this key to force
    // a refetch that returns active=true and re-arms the interval (§5 trap).
    refetchInterval: (q) => (q.state.data?.active ? POLL_INTERVAL_MS : false),
  });

  const data = query.data;

  // Progressive invalidation (self-throttled): repaint the listings when the
  // total fetched_count grows since the previous tick — OR when the backfill
  // just finished (active true→false: paint the last wave, drop the loading
  // label). The growth gate IS the throttle: at most one invalidation per poll
  // tick, none when the counter is still. Invalidating ['emails'] refreshes the
  // listings and, by prefix, the unread badges; ['virtual-mailbox-emails']
  // covers virtual mailboxes. Neither touches ['backfill-status'], so the poll
  // never feeds itself.
  const prevFetchedSum = useRef<number | null>(null);
  const prevActive = useRef(false);
  useEffect(() => {
    if (!data) return;
    const sum = data.accounts.reduce((acc, a) => acc + a.fetched_count, 0);
    const grew = prevFetchedSum.current !== null && sum > prevFetchedSum.current;
    const justFinished = prevActive.current && !data.active;
    if (grew || justFinished) {
      void queryClient.invalidateQueries({ queryKey: ['emails'] });
      void queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
    }
    prevFetchedSum.current = sum;
    prevActive.current = data.active;
  }, [data, queryClient]);

  const statuses = useMemo(
    () => new Map((data?.accounts ?? []).map((a) => [a.account_id, a] as const)),
    [data],
  );

  return {
    statuses,
    active: data?.active ?? false,
    error: query.error ? toUiError(query.error) : null,
  };
}
