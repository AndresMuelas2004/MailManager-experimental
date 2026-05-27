import { useCallback } from 'react';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';

import { listVirtualMailboxEmails } from '../../../api/endpoints/virtualMailboxes';
import { listAccounts } from '../../../api/endpoints/accounts';
import { listMailboxes } from '../../../api/endpoints/mailboxes';
import { toUiError } from '../../../api/client/errors';
import type { AccountOut, EmailMetadataOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type UseVirtualMailboxEmailsReturn = {
  emails: EmailMetadataOut[];
  accounts: AccountOut[];
  loading: boolean;
  syncing: boolean;
  error: UiError | null;
  refresh: () => Promise<void>;
};

const MIN_SEARCH_LENGTH = 2;

export default function useVirtualMailboxEmails(
  virtualMailboxId: string,
  mailboxId: string,
  searchQuery?: string,
): UseVirtualMailboxEmailsReturn {
  const queryClient = useQueryClient();
  const trimmedQuery = (searchQuery ?? '').trim();
  const effectiveQ = trimmedQuery.length >= MIN_SEARCH_LENGTH ? trimmedQuery : undefined;
  const emailsKey = ['virtual-mailbox-emails', virtualMailboxId, effectiveQ ?? null] as const;

  const emailsQuery = useQuery({
    queryKey: emailsKey,
    queryFn: ({ signal }) => listVirtualMailboxEmails(virtualMailboxId, { q: effectiveQ, signal }),
    enabled: virtualMailboxId.length > 0,
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
  // because scope_kind 'all' and 'accounts' can surface emails whose
  // account_id belongs to a mailbox other than the one active in the
  // sidebar; loading only the active mailbox leaves those rows with
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
  }, [queryClient, virtualMailboxId, effectiveQ]);

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

  return {
    emails: emailsQuery.data ?? [],
    accounts,
    loading: emailsQuery.isLoading || accountsLoading,
    syncing: emailsQuery.isFetching && !emailsQuery.isLoading,
    error,
    refresh,
  };
}
