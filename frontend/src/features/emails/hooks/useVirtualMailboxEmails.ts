import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';

import { listVirtualMailboxEmails } from '../../../api/endpoints/virtualMailboxes';
import { listAccounts } from '../../../api/endpoints/accounts';
import { listMailboxes } from '../../../api/endpoints/mailboxes';
import { syncEmailMetadata } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import { EMAILS_PAGE_SIZE } from '../../../lib/constants';
import { readLastSyncedAt, writeLastSyncedAt } from '../../../lib/lastSync';
import type { AccountOut, EmailMetadataOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type UseVirtualMailboxEmailsReturn = {
  emails: EmailMetadataOut[];
  accounts: AccountOut[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  loading: boolean;
  syncing: boolean;
  isPlaceholder: boolean;
  error: UiError | null;
  refresh: () => Promise<void>;
  sync: () => void;
  lastSyncedAt: number | null;
};

const MIN_SEARCH_LENGTH = 2;

export default function useVirtualMailboxEmails(
  virtualMailboxId: string,
  mailboxId: string,
  accountIds: string[],
  searchQuery?: string,
  page = 1,
): UseVirtualMailboxEmailsReturn {
  const queryClient = useQueryClient();
  // Per-vmbox last-synced mark (independent of q / page): the open-sync fan-out
  // covers the whole vmbox's account set in one go.
  const scopeKey = `vmbox:${virtualMailboxId}`;
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(() => readLastSyncedAt(scopeKey));
  const trimmedQuery = (searchQuery ?? '').trim();
  const effectiveQ = trimmedQuery.length >= MIN_SEARCH_LENGTH ? trimmedQuery : undefined;
  const emailsKey = ['virtual-mailbox-emails', virtualMailboxId, effectiveQ ?? null, page] as const;

  const emailsQuery = useQuery({
    queryKey: emailsKey,
    queryFn: ({ signal }) =>
      listVirtualMailboxEmails(virtualMailboxId, { q: effectiveQ, page, signal }),
    enabled: virtualMailboxId.length > 0,
    placeholderData: keepPreviousData,
    // Force a fresh GET every time the user navigates back into a
    // virtual mailbox. Virtual mailboxes are user-curated, time-
    // sensitive views (Marina's mail, "today's invoices", etc.) — the
    // user expects newly arrived emails to surface the moment they
    // re-open the view, not after the 30 s global staleTime expires.
    // ``refetchOnMount: 'always'`` overrides the global cache freshness
    // and triggers the GET on every mount, even when a previous render
    // already cached the listing.
    refetchOnMount: 'always',
    staleTime: 0,
  });

  // Accounts are required for the resolveAccount() lookup on the email
  // table — they are fetched across every mailbox the user owns
  // because a virtual mailbox can aggregate accounts from several real
  // mailboxes; loading only the active mailbox leaves those rows with
  // empty provider/email columns.
  const mailboxesQuery = useQuery({
    queryKey: ['mailboxes'],
    queryFn: () => listMailboxes(),
    enabled: mailboxId.length > 0,
  });

  const mailboxes = mailboxesQuery.data ?? [];

  const accountQueries = useQueries({
    queries: mailboxes.map((m) => ({
      queryKey: ['accounts', m.mailbox_id],
      queryFn: () => listAccounts(m.mailbox_id),
      enabled: m.mailbox_id.length > 0,
    })),
  });

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: emailsKey });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryClient, virtualMailboxId, effectiveQ, page]);

  const accountsError = accountQueries.find((q) => q.error)?.error ?? null;
  const error = emailsQuery.error
    ? toUiError(emailsQuery.error)
    : mailboxesQuery.error
      ? toUiError(mailboxesQuery.error)
      : accountsError
        ? toUiError(accountsError)
        : null;

  const accounts: AccountOut[] = accountQueries.flatMap((q) => q.data ?? []);
  const accountsLoading = mailboxesQuery.isLoading || accountQueries.some((q) => q.isLoading);

  // Resolve the vmbox's account_ids against the already-loaded catalogue to
  // recover each account's real mailbox_id (an account aggregated by the vmbox
  // can live under a different real mailbox than the route's). No extra
  // requests: the catalogue is the same one resolveAccount() already consumes.
  const accountById = useMemo(() => {
    const m = new Map<string, AccountOut>();
    for (const a of accounts) m.set(a.account_id, a);
    return m;
  }, [accounts]);

  const syncTargets = useMemo(
    () =>
      accountIds
        .map((aid) => accountById.get(aid))
        .filter((a): a is AccountOut => a !== undefined)
        .map((a) => ({ mailboxId: a.mailbox_id, accountId: a.account_id })),
    [accountIds, accountById],
  );

  const syncMutation = useMutation({
    mutationFn: (targets: { mailboxId: string; accountId: string }[]) =>
      Promise.allSettled(targets.map((t) => syncEmailMetadata(t.mailboxId, t.accountId))),
    // The fan-out can pull in new emails for accounts the vmbox aggregates
    // from other real mailboxes → invalidate ONLY ['virtual-mailbox-emails']
    // (the vmbox listing does not consume the bare ['emails'] key). Minimal
    // blast radius. Promise.allSettled never rejects, so onSuccess always runs
    // and refreshes the listing even when one account's sync failed — which is
    // why the mark is stamped on "attempt completed" and NO aggregated
    // syncError is exposed (asymmetry vs useEmailList's single-call sync).
    onSuccess: () => {
      const ts = Date.now();
      writeLastSyncedAt(scopeKey, ts);
      setLastSyncedAt(ts);
      return queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
    },
  });

  // Re-read the persisted mark when the vmbox scope changes without unmounting
  // (e.g. navigating from one virtual mailbox to another): the useState
  // initializer only runs on the first mount.
  useEffect(() => {
    setLastSyncedAt(readLastSyncedAt(scopeKey));
  }, [scopeKey]);

  // Sync the vmbox's accounts on open (mirrors how the regular listing syncs
  // in useEmailList), gated until the catalogue resolved so account_id can be
  // mapped to its mailbox_id. ``syncKey`` is the stable projection of the
  // target set: the effect re-fires when the SET of accounts changes but not
  // on reorders or unrelated re-renders.
  const accountsReady = !accountsLoading && accounts.length > 0;
  const syncKey = syncTargets
    .map((t) => `${t.mailboxId}:${t.accountId}`)
    .sort()
    .join('|');

  useEffect(() => {
    if (virtualMailboxId.length === 0) return;
    if (!accountsReady) return;
    if (syncTargets.length === 0) return;
    syncMutation.mutate(syncTargets);
    // syncMutation identity is stable per TanStack Query docs; gate by syncKey
    // (the identity of the accounts to sync) so reorders / unrelated re-renders
    // don't re-fire. syncTargets is read inside but excluded from deps — its
    // identity changes every render (useMemo over objects); syncKey is its
    // stable projection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [virtualMailboxId, syncKey, accountsReady]);

  // Manual trigger for the refresh button. Respects the same gating as the
  // open-sync (no-op until the vmbox id and its resolved targets exist). Keyed
  // by ``syncKey`` so the callback identity tracks the target set, not object
  // reorders. Fires the provider SYNC fan-out — NOT refresh (local re-read).
  const sync = useCallback(() => {
    if (virtualMailboxId.length === 0) return;
    if (syncTargets.length === 0) return;
    syncMutation.mutate(syncTargets);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [virtualMailboxId, syncKey]);

  const total = emailsQuery.data?.total ?? 0;

  return {
    emails: emailsQuery.data?.items ?? [],
    accounts,
    total,
    page,
    pageSize: EMAILS_PAGE_SIZE,
    totalPages: Math.max(1, Math.ceil(total / EMAILS_PAGE_SIZE)),
    loading: emailsQuery.isLoading || accountsLoading,
    syncing: syncMutation.isPending || (emailsQuery.isFetching && !emailsQuery.isLoading),
    isPlaceholder: emailsQuery.isPlaceholderData,
    error,
    refresh,
    sync,
    lastSyncedAt,
  };
}
