import { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { updateMailbox } from '../../../api/endpoints/mailboxes';
import { toUiError } from '../../../api/client/errors';
import { MAILBOXES_QUERY_KEY } from './useMailboxList';
import type { MailboxOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type RenameArgs = { mailboxId: string; displayName: string };

type UseRenameMailboxReturn = {
  rename: (args: RenameArgs) => Promise<MailboxOut | null>;
  loading: boolean;
  error: UiError | null;
};

export default function useRenameMailbox(): UseRenameMailboxReturn {
  const queryClient = useQueryClient();
  const [error, setError] = useState<UiError | null>(null);

  const mutation = useMutation({
    mutationFn: ({ mailboxId, displayName }: RenameArgs) =>
      updateMailbox(mailboxId, { display_name: displayName }),
    onSuccess: () => {
      // Refresh the mailbox listing so the sidebar selector and the settings
      // list show the new name. ``['mailboxes']`` is the single namespace
      // every mailbox listing reads from.
      void queryClient.invalidateQueries({ queryKey: MAILBOXES_QUERY_KEY });
    },
  });

  const rename = useCallback(
    async (args: RenameArgs): Promise<MailboxOut | null> => {
      setError(null);
      try {
        return await mutation.mutateAsync(args);
      } catch (err) {
        setError(toUiError(err));
        return null;
      }
    },
    [mutation],
  );

  return { rename, loading: mutation.isPending, error };
}
