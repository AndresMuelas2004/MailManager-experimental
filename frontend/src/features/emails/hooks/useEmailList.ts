import { useCallback, useEffect } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { listEmails, syncEmailMetadata } from '../../../api/endpoints/emails';
import { listAccounts } from '../../../api/endpoints/accounts';
import { toUiError } from '../../../api/client/errors';
import { EMAILS_PAGE_SIZE } from '../../../lib/constants';
import type { AccountOut, EmailMetadataOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';
import type { EmailBox } from '../../../lib/types';

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
): UseEmailListReturn {
  const queryClient = useQueryClient();
  const trimmedQuery = (searchQuery ?? '').trim();
  const effectiveQ = trimmedQuery.length >= MIN_SEARCH_LENGTH ? trimmedQuery : undefined;
  // ``groupByThread`` is a non-nullable boolean → it goes straight into the
  // key (no ``?? null``). It namespaces the grouped (conversation) cache
  // apart from the non-grouped (favourites) cache so the two never collide.
  const emailsKey = [
    'emails',
    mailboxId,
    box,
    accountId ?? null,
    effectiveQ ?? null,
    favorite ?? null,
    groupByThread,
    page,
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
        signal,
      }),
    enabled: mailboxId.length > 0,
    placeholderData: keepPreviousData,
  });

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
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ['emails'] }),
        queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] }),
      ]),
  });

  useEffect(() => {
    if (mailboxId.length === 0) return;
    syncMutation.mutate();
    // syncMutation identity is stable per TanStack Query docs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mailboxId, accountId]);

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: emailsKey });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryClient, mailboxId, box, accountId, effectiveQ, favorite, groupByThread, page]);

  const error = emailsQuery.error
    ? toUiError(emailsQuery.error)
    : accountsQuery.error
      ? toUiError(accountsQuery.error)
      : null;

  const total = emailsQuery.data?.total ?? 0;

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
  };
}
