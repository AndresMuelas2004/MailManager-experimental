import { useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { updateReadStatus } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import type { EmailItemRef, EmailMetadataOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type UseMarkThreadReadReturn = {
  markRead: (emails: EmailMetadataOut[]) => Promise<void>;
  marking: boolean;
  error: UiError | null;
};

// Group items by their REAL ``mailbox_id`` and fan out one read-status call
// per mailbox: a conversation can span several real mailboxes (a virtual
// mailbox aggregating multiple accounts), and the backend endpoint is
// mailbox-scoped. Same shape as ``useEmailBulkActions.groupByMailbox`` but
// kept here so the conversation viewer does not pull in the bulk-bar /
// selection machinery just to mark a thread read.
function groupByMailbox(emails: EmailMetadataOut[]): Map<string, EmailItemRef[]> {
  const out = new Map<string, EmailItemRef[]>();
  for (const e of emails) {
    const ref: EmailItemRef = {
      account_id: e.account_id,
      provider_message_id: e.provider_message_id,
    };
    const bucket = out.get(e.mailbox_id);
    if (bucket) bucket.push(ref);
    else out.set(e.mailbox_id, [ref]);
  }
  return out;
}

// Marks every supplied (already unread) message of a conversation as read.
// Used when opening the conversation viewer (decision 6: opening a
// conversation marks the whole thread read, so its listing row drops the
// bold weight immediately). Blanket-invalidates both listing prefixes so the
// aggregated ``bool_and(is_read)`` of the thread row flips to true.
export default function useMarkThreadRead(): UseMarkThreadReadReturn {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: async (emails: EmailMetadataOut[]) => {
      const groups = groupByMailbox(emails);
      // propagate_thread=true: mark the WHOLE thread of each item read, not
      // just the sent ids. Outlook persists one physical message under several
      // ids (sync vs conversation fetch), so a per-id update leaves the
      // duplicate row unread and the grouped listing row stays bold.
      await Promise.all(
        [...groups].map(([mid, items]) => updateReadStatus(mid, true, items, true)),
      );
    },
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ['emails'] }),
        queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] }),
      ]),
  });

  const markRead = useCallback(
    async (emails: EmailMetadataOut[]) => {
      if (emails.length === 0) return;
      await mutation.mutateAsync(emails).catch(() => undefined);
    },
    [mutation],
  );

  return {
    markRead,
    marking: mutation.isPending,
    error: mutation.error ? toUiError(mutation.error) : null,
  };
}
