import { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { deleteMailbox } from '../../../api/endpoints/mailboxes';
import { toUiError } from '../../../api/client/errors';
import { MAILBOXES_QUERY_KEY } from './useMailboxList';
import type { UiError } from '../../../api/client/errors';

type UseDeleteMailboxReturn = {
  remove: (mailboxId: string) => Promise<boolean>;
  loading: boolean;
  error: UiError | null;
};

export default function useDeleteMailbox(): UseDeleteMailboxReturn {
  const queryClient = useQueryClient();
  const [error, setError] = useState<UiError | null>(null);

  const mutation = useMutation({
    mutationFn: (mailboxId: string) => deleteMailbox(mailboxId),
    onSuccess: () =>
      // Deleting a mailbox cascades: its accounts and all their synced emails
      // are gone. Refresh the mailbox selector AND the broad email caches
      // (a virtual mailbox can aggregate the deleted accounts) plus the
      // ['accounts'] listing of the removed mailbox. Same blast radius as a
      // metadata sync (frontend_guide §2) extended with mailboxes/accounts.
      Promise.all([
        queryClient.invalidateQueries({ queryKey: MAILBOXES_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: ['emails'] }),
        queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] }),
        queryClient.invalidateQueries({ queryKey: ['accounts'] }),
      ]),
  });

  const remove = useCallback(
    async (mailboxId: string): Promise<boolean> => {
      setError(null);
      try {
        await mutation.mutateAsync(mailboxId);
        return true;
      } catch (err) {
        setError(toUiError(err));
        return false;
      }
    },
    [mutation],
  );

  return { remove, loading: mutation.isPending, error };
}
