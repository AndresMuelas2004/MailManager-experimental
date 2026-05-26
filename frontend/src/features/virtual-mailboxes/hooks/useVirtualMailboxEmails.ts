import { useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { listVirtualMailboxEmails } from '../../../api/endpoints/virtualMailboxes';
import { listAccounts } from '../../../api/endpoints/accounts';
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
  const accountsKey = ['accounts', mailboxId] as const;

  const emailsQuery = useQuery({
    queryKey: emailsKey,
    queryFn: ({ signal }) => listVirtualMailboxEmails(virtualMailboxId, { q: effectiveQ, signal }),
    enabled: virtualMailboxId.length > 0,
  });

  // Accounts are required for the resolveAccount() lookup on the email
  // table — they are fetched per mailbox so the user can still see
  // provider/email columns even in the cross-mailbox 'all' scope.
  const accountsQuery = useQuery({
    queryKey: accountsKey,
    queryFn: () => listAccounts(mailboxId),
    enabled: mailboxId.length > 0,
  });

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: emailsKey });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryClient, virtualMailboxId, effectiveQ]);

  const error = emailsQuery.error
    ? toUiError(emailsQuery.error)
    : accountsQuery.error
      ? toUiError(accountsQuery.error)
      : null;

  return {
    emails: emailsQuery.data ?? [],
    accounts: accountsQuery.data ?? [],
    loading: emailsQuery.isLoading || accountsQuery.isLoading,
    syncing: emailsQuery.isFetching && !emailsQuery.isLoading,
    error,
    refresh,
  };
}
