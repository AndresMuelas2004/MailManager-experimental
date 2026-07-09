import { useQuery } from '@tanstack/react-query';

import { listAccounts } from '../../../api/endpoints/accounts';
import { toUiError } from '../../../api/client/errors';
import type { AccountOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

export const MAILBOX_ACCOUNTS_QUERY_KEY = (mailboxId: string) => ['accounts', mailboxId] as const;

type UseMailboxAccountsReturn = {
  accounts: AccountOut[];
  error: UiError | null;
};

// Accounts of a mailbox for the sidebar's scope switcher. Lightweight read (no
// email preview) — distinct from useConnectedAccounts, which drives the settings
// management page and holds its accounts in useState. useConnectedAccounts
// invalidates this query key on add/remove/rename so the sidebar stays in sync.
export default function useMailboxAccounts(mailboxId: string): UseMailboxAccountsReturn {
  const query = useQuery({
    queryKey: MAILBOX_ACCOUNTS_QUERY_KEY(mailboxId),
    queryFn: () => listAccounts(mailboxId),
    enabled: mailboxId.length > 0,
  });
  return {
    accounts: query.data ?? [],
    error: query.error ? toUiError(query.error) : null,
  };
}
