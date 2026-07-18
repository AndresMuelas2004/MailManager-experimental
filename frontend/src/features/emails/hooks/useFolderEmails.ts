import { useCallback, useEffect, useState } from 'react';
import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';

import { listFolderEmails } from '../../../api/endpoints/folders';
import { listAccounts } from '../../../api/endpoints/accounts';
import { listMailboxes } from '../../../api/endpoints/mailboxes';
import { syncEmailMetadata } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import { EMAILS_PAGE_SIZE } from '../../../lib/constants';
import { readLastSyncedAt, writeLastSyncedAt } from '../../../lib/lastSync';
import type { AccountOut, EmailMetadataOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type UseFolderEmailsReturn = {
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

// Listing of ONE folder's emails. Mirrors ``useVirtualMailboxEmails`` (flat
// queryKey ``['folder-emails', id, q ?? null, page]``, keepPreviousData,
// ``refetchOnMount:'always'`` + ``staleTime:0`` so newly classified mail
// surfaces the moment the view is reopened). Two deliberate differences from
// the vmbox: a folder is unified over EVERY account of the user (no
// ``account_ids`` snapshot), so the ``sync`` fan-out fires one
// ``sync-metadata`` per MAILBOX (the ``useSyncAll`` pattern) rather than per
// account_id; and the sync is manual (RefreshControl) only — the folder-emails
// endpoint is local-only and an all-accounts sync on every open would be
// unbounded provider load, so we lean on the other listings' syncs (which run
// the rules that fill folders) for freshness.
export default function useFolderEmails(
  folderId: string,
  searchQuery?: string,
  page = 1,
): UseFolderEmailsReturn {
  const queryClient = useQueryClient();
  // Per-folder last-synced mark, independent of q / page (one sync covers the
  // whole folder's mail).
  const scopeKey = `folder:${folderId}`;
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(() => readLastSyncedAt(scopeKey));
  const trimmedQuery = (searchQuery ?? '').trim();
  const effectiveQ = trimmedQuery.length >= MIN_SEARCH_LENGTH ? trimmedQuery : undefined;
  const emailsKey = ['folder-emails', folderId, effectiveQ ?? null, page] as const;

  const emailsQuery = useQuery({
    queryKey: emailsKey,
    queryFn: ({ signal }) => listFolderEmails(folderId, { q: effectiveQ, page, signal }),
    enabled: folderId.length > 0,
    placeholderData: keepPreviousData,
    refetchOnMount: 'always',
    staleTime: 0,
  });

  // Accounts across EVERY owned mailbox — a folder aggregates mail from any
  // account, so resolving the DE/PARA/Cuenta columns needs the full catalogue,
  // exactly like the virtual mailbox listing.
  const mailboxesQuery = useQuery({
    queryKey: ['mailboxes'],
    queryFn: () => listMailboxes(),
    enabled: folderId.length > 0,
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
  }, [queryClient, folderId, effectiveQ, page]);

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

  // Sync fan-out: one ``sync-metadata`` per mailbox (no account_id → every
  // account of the mailbox syncs), Promise.allSettled so one failing mailbox
  // does not abort the rest. The mark is stamped on "attempt completed" — like
  // the vmbox, no aggregated syncError is exposed (allSettled never rejects).
  const syncMutation = useMutation({
    mutationFn: (mailboxIds: string[]) =>
      Promise.allSettled(mailboxIds.map((id) => syncEmailMetadata(id))),
    onSuccess: () => {
      const ts = Date.now();
      writeLastSyncedAt(scopeKey, ts);
      setLastSyncedAt(ts);
      // A sync can pull new mail that the active rules then classify into
      // folders — refresh the folder listings and folder counts, plus the
      // regular / virtual listings whose chips may have changed.
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: ['folder-emails'] }),
        queryClient.invalidateQueries({ queryKey: ['folders'] }),
        queryClient.invalidateQueries({ queryKey: ['emails'] }),
        queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] }),
      ]);
    },
  });

  // Re-read the persisted mark when the folder scope changes without unmounting
  // (navigating from one folder to another): the useState initializer only runs
  // on the first mount.
  useEffect(() => {
    setLastSyncedAt(readLastSyncedAt(scopeKey));
  }, [scopeKey]);

  // Stable projection of the mailbox set (the vmbox uses the same trick): the
  // callback re-creates when the SET of mailboxes changes but not on unrelated
  // re-renders, and it avoids a complex expression in the deps array.
  const mailboxIds = mailboxes.map((m) => m.mailbox_id);
  const mailboxIdsKey = mailboxIds.join('|');

  const sync = useCallback(() => {
    if (folderId.length === 0) return;
    if (mailboxIds.length === 0) return;
    syncMutation.mutate(mailboxIds);
    // syncMutation identity is stable per TanStack Query docs; mailboxIds is
    // read inside but excluded from deps — mailboxIdsKey is its stable
    // projection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folderId, mailboxIdsKey]);

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
