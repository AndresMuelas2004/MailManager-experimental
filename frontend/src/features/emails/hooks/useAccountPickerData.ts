import { useQueries, useQuery } from '@tanstack/react-query';

import { listAccounts } from '../../../api/endpoints/accounts';
import { listMailboxes } from '../../../api/endpoints/mailboxes';
import type { AccountOut, MailboxOut } from '../../../api/types/dto';

type UseAccountPickerDataReturn = {
  mailboxes: MailboxOut[];
  accounts: AccountOut[];
  loading: boolean;
};

/**
 * Loads the full mailbox/account catalogue for the virtual-mailbox
 * account picker. The form needs every mailbox + every account at once
 * so the user can pick any account across any mailbox.
 */
export default function useAccountPickerData(): UseAccountPickerDataReturn {
  const mailboxesQuery = useQuery({
    queryKey: ['mailboxes'],
    queryFn: () => listMailboxes(),
  });

  const mailboxes = mailboxesQuery.data ?? [];

  const accountQueries = useQueries({
    queries: mailboxes.map((m) => ({
      queryKey: ['accounts', m.mailbox_id],
      queryFn: () => listAccounts(m.mailbox_id),
      enabled: m.mailbox_id.length > 0,
    })),
  });

  const accounts: AccountOut[] = accountQueries.flatMap((q) => q.data ?? []);

  const loading = mailboxesQuery.isLoading || accountQueries.some((q) => q.isLoading);

  return { mailboxes, accounts, loading };
}
