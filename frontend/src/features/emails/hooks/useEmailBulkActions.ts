import { useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import {
  markAsSpam,
  moveToTrash,
  restoreFromSpam,
  trashAction,
  updateReadStatus,
} from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import type { EmailItemRef, EmailMetadataOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type Params = {
  refresh: () => Promise<void>;
  clearSelection: () => void;
};

export type UseEmailBulkActionsReturn = {
  loading: boolean;
  error: UiError | null;
  moveToTrashItems: (emails: EmailMetadataOut[]) => Promise<void>;
  setReadStatusItems: (emails: EmailMetadataOut[], isRead: boolean) => Promise<void>;
  spamItems: (emails: EmailMetadataOut[]) => Promise<void>;
  restoreFromSpamItems: (emails: EmailMetadataOut[]) => Promise<void>;
  trashActionItems: (emails: EmailMetadataOut[], action: 'delete' | 'restore') => Promise<void>;
};

// Bulk operations are scoped to ONE mailbox at a time in the backend
// (the mailbox_id sits in the URL path). A virtual mailbox can mix
// emails from several real mailboxes in the same selection, so the
// frontend groups items by ``email.mailbox_id`` and fans out one call
// per mailbox. Each call still validates ``account ∈ mailbox`` on the
// backend, but with the right pair this time.
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

export default function useEmailBulkActions({
  refresh,
  clearSelection,
}: Params): UseEmailBulkActionsReturn {
  const queryClient = useQueryClient();

  const invalidate = useCallback(() => {
    // Blanket invalidation across every mailbox listing AND every
    // virtual-mailbox listing: a single bulk action can have touched
    // emails from several mailboxes (virtual-mailbox scope='all').
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: ['emails'] }),
      queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] }),
    ]);
  }, [queryClient]);

  const sharedOnSuccess = useCallback(async () => {
    clearSelection();
    await invalidate();
    await refresh();
  }, [clearSelection, invalidate, refresh]);

  const moveToTrashMut = useMutation({
    mutationFn: async (emails: EmailMetadataOut[]) => {
      const groups = groupByMailbox(emails);
      await Promise.all([...groups].map(([mid, items]) => moveToTrash(mid, items)));
    },
    onSuccess: sharedOnSuccess,
  });

  const readStatusMut = useMutation({
    mutationFn: async ({ emails, isRead }: { emails: EmailMetadataOut[]; isRead: boolean }) => {
      const groups = groupByMailbox(emails);
      await Promise.all([...groups].map(([mid, items]) => updateReadStatus(mid, isRead, items)));
    },
    onSuccess: sharedOnSuccess,
  });

  const spamMut = useMutation({
    mutationFn: async (emails: EmailMetadataOut[]) => {
      const groups = groupByMailbox(emails);
      await Promise.all([...groups].map(([mid, items]) => markAsSpam(mid, items)));
    },
    onSuccess: sharedOnSuccess,
  });

  const restoreSpamMut = useMutation({
    mutationFn: async (emails: EmailMetadataOut[]) => {
      const groups = groupByMailbox(emails);
      await Promise.all([...groups].map(([mid, items]) => restoreFromSpam(mid, items)));
    },
    onSuccess: sharedOnSuccess,
  });

  const trashActionMut = useMutation({
    mutationFn: async ({
      emails,
      action,
    }: {
      emails: EmailMetadataOut[];
      action: 'delete' | 'restore';
    }) => {
      const groups = groupByMailbox(emails);
      await Promise.all([...groups].map(([mid, items]) => trashAction(mid, action, items)));
    },
    onSuccess: sharedOnSuccess,
  });

  const loading =
    moveToTrashMut.isPending ||
    readStatusMut.isPending ||
    spamMut.isPending ||
    restoreSpamMut.isPending ||
    trashActionMut.isPending;

  const firstError =
    moveToTrashMut.error ||
    readStatusMut.error ||
    spamMut.error ||
    restoreSpamMut.error ||
    trashActionMut.error;
  const error = firstError ? toUiError(firstError) : null;

  const moveToTrashItems = useCallback(
    async (emails: EmailMetadataOut[]) => {
      await moveToTrashMut.mutateAsync(emails).catch(() => undefined);
    },
    [moveToTrashMut],
  );

  const setReadStatusItems = useCallback(
    async (emails: EmailMetadataOut[], isRead: boolean) => {
      await readStatusMut.mutateAsync({ emails, isRead }).catch(() => undefined);
    },
    [readStatusMut],
  );

  const spamItems = useCallback(
    async (emails: EmailMetadataOut[]) => {
      await spamMut.mutateAsync(emails).catch(() => undefined);
    },
    [spamMut],
  );

  const restoreFromSpamItems = useCallback(
    async (emails: EmailMetadataOut[]) => {
      await restoreSpamMut.mutateAsync(emails).catch(() => undefined);
    },
    [restoreSpamMut],
  );

  const trashActionItems = useCallback(
    async (emails: EmailMetadataOut[], action: 'delete' | 'restore') => {
      await trashActionMut.mutateAsync({ emails, action }).catch(() => undefined);
    },
    [trashActionMut],
  );

  return {
    loading,
    error,
    moveToTrashItems,
    setReadStatusItems,
    spamItems,
    restoreFromSpamItems,
    trashActionItems,
  };
}
