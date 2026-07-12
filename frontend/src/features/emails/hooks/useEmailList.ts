import { useCallback, useEffect, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { listEmails, syncEmailMetadata } from '../../../api/endpoints/emails';
import { listAccounts } from '../../../api/endpoints/accounts';
import { toUiError } from '../../../api/client/errors';
import { EMAILS_PAGE_SIZE } from '../../../lib/constants';
import { readLastSyncedAt, writeLastSyncedAt } from '../../../lib/lastSync';
import { DEFAULT_LIST_CONTROLS } from '../../../lib/listControls';
import useEmailContentPrefetch from './useEmailContentPrefetch';
import type { AccountOut, AccountSyncFailure, EmailMetadataOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';
import type { EmailBox } from '../../../lib/types';
import type { ListControlsState } from '../../../lib/listControls';

type UseEmailListReturn = {
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
  syncError: UiError | null;
  syncFailedAccounts: AccountSyncFailure[];
};

const MIN_SEARCH_LENGTH = 2;

export default function useEmailList(
  mailboxId: string,
  box: EmailBox,
  accountId?: string,
  searchQuery?: string,
  favorite?: boolean,
  page = 1,
  groupByThread = false,
  controls: ListControlsState = DEFAULT_LIST_CONTROLS,
): UseEmailListReturn {
  const queryClient = useQueryClient();
  // The underlying sync is per mailbox/account (all boxes at once), so the
  // last-synced mark is scoped to mailbox+account — independent of box, q,
  // favorite and page.
  const scopeKey = `emails:${mailboxId}:${accountId ?? 'ALL'}`;
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(() => readLastSyncedAt(scopeKey));
  const trimmedQuery = (searchQuery ?? '').trim();
  const effectiveQ = trimmedQuery.length >= MIN_SEARCH_LENGTH ? trimmedQuery : undefined;
  // ``groupByThread`` is a non-nullable boolean → it goes straight into the
  // key (no ``?? null``). It namespaces the grouped (conversation) cache
  // apart from the non-grouped (favourites) cache so the two never collide.
  // ``controls`` is rebuilt on every render (parseListControls returns a fresh
  // object), so its primitive fields go into the key individually — each
  // sort/filter combination caches under its own entry.
  const emailsKey = [
    'emails',
    mailboxId,
    box,
    accountId ?? null,
    effectiveQ ?? null,
    favorite ?? null,
    groupByThread,
    page,
    controls.sort,
    controls.dir,
    controls.unread,
    controls.hasAttachment,
    controls.favorite,
  ] as const;
  const accountsKey = ['accounts', mailboxId] as const;

  const emailsQuery = useQuery({
    queryKey: emailsKey,
    queryFn: ({ signal }) =>
      listEmails(mailboxId, box, accountId, {
        q: effectiveQ,
        favorite,
        page,
        groupByThread,
        sort: controls.sort,
        dir: controls.dir,
        unread: controls.unread,
        hasAttachment: controls.hasAttachment,
        favoriteOnly: controls.favorite,
        signal,
      }),
    enabled: mailboxId.length > 0,
    placeholderData: keepPreviousData,
  });

  // Warm the in-memory body cache for this page's recent-unread INBOX rows (F2).
  // No-op for read / non-INBOX / >48h rows, so Favoritos schedules nothing.
  useEmailContentPrefetch(emailsQuery.data?.items ?? []);

  const accountsQuery = useQuery({
    queryKey: accountsKey,
    queryFn: () => listAccounts(mailboxId),
    enabled: mailboxId.length > 0,
  });

  const syncMutation = useMutation({
    mutationFn: () => syncEmailMetadata(mailboxId, accountId),
    // A metadata sync can pull in new emails for accounts a virtual mailbox
    // aggregates from other real mailboxes, so invalidate the bare ['emails']
    // prefix plus ['virtual-mailbox-emails'] — same blast radius as
    // useEmailBulkActions / useFavorite / useEmailViewer (frontend_guide §2).
    // onSuccess only runs on a resolved sync, so the mark advances exclusively
    // on a real provider success (a 4xx/5xx leaves it untouched).
    onSuccess: () => {
      const ts = Date.now();
      writeLastSyncedAt(scopeKey, ts);
      setLastSyncedAt(ts);
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: ['emails'] }),
        queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] }),
      ]);
    },
  });

  useEffect(() => {
    if (mailboxId.length === 0) return;
    syncMutation.mutate();
    // syncMutation identity is stable per TanStack Query docs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mailboxId, accountId]);

  // Re-read the persisted mark when the scope changes without unmounting (e.g.
  // navigating between accounts in AccountInboxPage): the useState initializer
  // only runs on the first mount, so a scope switch would otherwise keep the
  // previous account's mark until the next sync.
  useEffect(() => {
    setLastSyncedAt(readLastSyncedAt(scopeKey));
  }, [scopeKey]);

  // Manual trigger for the refresh button. Stable identity (mutate is stable
  // per TanStack Query). This fires the provider SYNC mutation — NOT refresh,
  // which only re-reads the local query.
  const sync = useCallback(() => {
    if (mailboxId.length === 0) return;
    syncMutation.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mailboxId]);

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: emailsKey });
    // ``controls`` is decomposed into primitives here (not passed as the object)
    // because parseListControls returns a fresh object each render, which would
    // recreate ``refresh`` on every render and break its stable identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    queryClient,
    mailboxId,
    box,
    accountId,
    effectiveQ,
    favorite,
    groupByThread,
    page,
    controls.sort,
    controls.dir,
    controls.unread,
    controls.hasAttachment,
    controls.favorite,
  ]);

  const error = emailsQuery.error
    ? toUiError(emailsQuery.error)
    : accountsQuery.error
      ? toUiError(accountsQuery.error)
      : null;

  const total = emailsQuery.data?.total ?? 0;

  // Non-blocking sync error: surfaced as an inline notice on the refresh
  // control. Kept SEPARATE from ``error`` (which replaces the table) so a
  // transient provider failure never empties the already-loaded listing.
  const syncError = syncMutation.error ? toUiError(syncMutation.error) : null;

  // Accounts left unsynced by the last RESOLVED sync (200 partial success). A
  // total failure rejects the mutation (409/502) → ``data`` is undefined → the
  // list stays empty and ``syncError`` (red notice) fires instead. The two
  // states are mutually exclusive by the mutation's outcome. The per-account
  // view always sees an empty list (its single account's failure rejects).
  const syncFailedAccounts = syncMutation.data?.failed_accounts ?? [];

  return {
    emails: emailsQuery.data?.items ?? [],
    accounts: accountsQuery.data ?? [],
    total,
    page,
    pageSize: EMAILS_PAGE_SIZE,
    totalPages: Math.max(1, Math.ceil(total / EMAILS_PAGE_SIZE)),
    loading: emailsQuery.isLoading || accountsQuery.isLoading,
    syncing: syncMutation.isPending || (emailsQuery.isFetching && !emailsQuery.isLoading),
    isPlaceholder: emailsQuery.isPlaceholderData,
    error,
    refresh,
    sync,
    lastSyncedAt,
    syncError,
    syncFailedAccounts,
  };
}
