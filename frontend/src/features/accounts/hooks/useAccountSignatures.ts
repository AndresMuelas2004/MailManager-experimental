import { useCallback, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { listAccounts, updateAccount } from '../../../api/endpoints/accounts';
import { toUiError } from '../../../api/client/errors';
import type { AccountOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type UpdateArgs = { accountId: string; signatureHtml: string };

type UseAccountSignaturesReturn = {
  accounts: AccountOut[];
  loading: boolean;
  error: UiError | null;
  savingAccountId: string | null;
  updateSignature: (accountId: string, signatureHtml: string) => Promise<boolean>;
};

export default function useAccountSignatures(mailboxId: string): UseAccountSignaturesReturn {
  const queryClient = useQueryClient();
  const [error, setError] = useState<UiError | null>(null);

  // ``['accounts', mailboxId]`` is the SHARED account-listing key (also read by
  // useAccountPickerData / useVirtualMailboxEmails). Reusing it means the saved
  // signature surfaces in those pickers on invalidation — desired, not a risk.
  const query = useQuery({
    queryKey: ['accounts', mailboxId],
    queryFn: () => listAccounts(mailboxId),
    enabled: !!mailboxId,
  });

  const mutation = useMutation({
    mutationFn: ({ accountId, signatureHtml }: UpdateArgs) =>
      updateAccount(mailboxId, accountId, { signature_html: signatureHtml }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['accounts', mailboxId] });
    },
  });

  const updateSignature = useCallback(
    async (accountId: string, signatureHtml: string): Promise<boolean> => {
      setError(null);
      try {
        await mutation.mutateAsync({ accountId, signatureHtml });
        return true;
      } catch (err) {
        setError(toUiError(err));
        return false;
      }
    },
    [mutation],
  );

  return {
    accounts: query.data ?? [],
    loading: query.isLoading,
    error,
    // The account currently being saved (drives the per-row spinner). Derived
    // from the in-flight mutation variables so it clears automatically.
    savingAccountId: mutation.isPending ? (mutation.variables?.accountId ?? null) : null,
    updateSignature,
  };
}
