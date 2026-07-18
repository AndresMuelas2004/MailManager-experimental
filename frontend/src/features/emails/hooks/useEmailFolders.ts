import { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { assignFolder, unassignFolder } from '../../../api/endpoints/folders';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { ConversationOut, EmailPage, FolderRef } from '../../../api/types/dto';

// Per-email folder assignment. Lives in ``features/emails`` (next to
// ``useFavorite``) — NOT in ``features/folders`` — because its callers are
// ``features/emails`` surfaces (EmailTable / EmailViewer / bulk bar) and a
// feature cannot import from another feature; the shared piece is the endpoint
// in ``api/``. The URL carries the email's own ``mailbox_id`` + ``account_id``
// (like ``useFavorite``), NEVER the route mailbox — a folder is unified and can
// surface an email whose account lives in a different real mailbox.

type MutationArgs = {
  mailboxId: string;
  accountId: string;
  providerMessageId: string;
  folderId: string;
};

type UseEmailFoldersReturn = {
  assign: (args: MutationArgs) => Promise<void>;
  unassign: (args: MutationArgs) => Promise<void>;
  // Per-email in-flight flag (a single hook instance drives a whole table), so
  // callers disable only the row being saved. Derived from ``pendingKeys``.
  isBusy: (accountId: string, providerMessageId: string) => boolean;
  busy: boolean;
  error: UiError | null;
};

function pendingKey(accountId: string, providerMessageId: string): string {
  return `${accountId}:${providerMessageId}`;
}

type EmailPagePrefix = 'emails' | 'virtual-mailbox-emails' | 'folder-emails';

// Pin ``folders`` (the authoritative list from the assign/unassign response) on
// the matching email of every cached ``EmailPage``. New object + new ``items``
// array — TanStack compares by reference. Entries not yet populated are skipped;
// unread-count envelopes (same ``['emails']`` prefix) carry no ``items``.
function applyFoldersToEmailPages(
  queryClient: ReturnType<typeof useQueryClient>,
  prefix: EmailPagePrefix,
  accountId: string,
  providerMessageId: string,
  folders: FolderRef[],
): void {
  const entries = queryClient.getQueriesData<EmailPage>({ queryKey: [prefix] });
  entries.forEach(([key, data]) => {
    if (!data || !Array.isArray(data.items)) return;
    queryClient.setQueryData<EmailPage>(key, {
      ...data,
      items: data.items.map((email) =>
        email.provider_message_id === providerMessageId && email.account_id === accountId
          ? { ...email, folders }
          : email,
      ),
    });
  });
}

function applyFoldersToConversations(
  queryClient: ReturnType<typeof useQueryClient>,
  accountId: string,
  providerMessageId: string,
  folders: FolderRef[],
): void {
  const entries = queryClient.getQueriesData<ConversationOut>({ queryKey: ['conversation'] });
  entries.forEach(([key, data]) => {
    if (!data) return;
    queryClient.setQueryData<ConversationOut>(key, {
      ...data,
      messages: data.messages.map((message) =>
        message.provider_message_id === providerMessageId && message.account_id === accountId
          ? { ...message, folders }
          : message,
      ),
    });
  });
}

export default function useEmailFolders(): UseEmailFoldersReturn {
  const queryClient = useQueryClient();
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(() => new Set());

  // Repaint chips from the authoritative response, then blanket-invalidate.
  // The invalidation radius is a SUPERSET of ``useFavorite``'s: it adds
  // ``['folder-emails']`` (an unassign removes the row from a folder view) and
  // ``['folders']`` (membership counts change) on top of the shared
  // ``['emails']`` / ``['virtual-mailbox-emails']`` / ``['conversation']``.
  const reconcile = useCallback(
    (accountId: string, providerMessageId: string, folders: FolderRef[]) => {
      applyFoldersToEmailPages(queryClient, 'emails', accountId, providerMessageId, folders);
      applyFoldersToEmailPages(
        queryClient,
        'virtual-mailbox-emails',
        accountId,
        providerMessageId,
        folders,
      );
      applyFoldersToEmailPages(queryClient, 'folder-emails', accountId, providerMessageId, folders);
      applyFoldersToConversations(queryClient, accountId, providerMessageId, folders);
    },
    [queryClient],
  );

  const invalidateAll = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['emails'] });
    queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
    queryClient.invalidateQueries({ queryKey: ['folder-emails'] });
    queryClient.invalidateQueries({ queryKey: ['folders'] });
    queryClient.invalidateQueries({ queryKey: ['conversation'] });
  }, [queryClient]);

  const assignMutation = useMutation({
    mutationFn: ({ mailboxId, accountId, providerMessageId, folderId }: MutationArgs) =>
      assignFolder(mailboxId, accountId, providerMessageId, folderId),
    onSuccess: (data, { accountId, providerMessageId }) => {
      reconcile(accountId, providerMessageId, data.folders);
    },
    onSettled: invalidateAll,
  });

  const unassignMutation = useMutation({
    mutationFn: ({ mailboxId, accountId, providerMessageId, folderId }: MutationArgs) =>
      unassignFolder(mailboxId, accountId, providerMessageId, folderId),
    onSuccess: (data, { accountId, providerMessageId }) => {
      reconcile(accountId, providerMessageId, data.folders);
    },
    onSettled: invalidateAll,
  });

  const runWithPending = useCallback(
    async (args: MutationArgs, mutate: (args: MutationArgs) => Promise<unknown>) => {
      const key = pendingKey(args.accountId, args.providerMessageId);
      setPendingKeys((prev) => {
        const next = new Set(prev);
        next.add(key);
        return next;
      });
      try {
        await mutate(args);
      } finally {
        setPendingKeys((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
      }
    },
    [],
  );

  const assign = useCallback(
    (args: MutationArgs) => runWithPending(args, (a) => assignMutation.mutateAsync(a)),
    [runWithPending, assignMutation],
  );
  const unassign = useCallback(
    (args: MutationArgs) => runWithPending(args, (a) => unassignMutation.mutateAsync(a)),
    [runWithPending, unassignMutation],
  );

  const isBusy = useCallback(
    (accountId: string, providerMessageId: string) =>
      pendingKeys.has(pendingKey(accountId, providerMessageId)),
    [pendingKeys],
  );

  const error = assignMutation.error
    ? toUiError(assignMutation.error)
    : unassignMutation.error
      ? toUiError(unassignMutation.error)
      : null;

  return {
    assign,
    unassign,
    isBusy,
    busy: assignMutation.isPending || unassignMutation.isPending,
    error,
  };
}
