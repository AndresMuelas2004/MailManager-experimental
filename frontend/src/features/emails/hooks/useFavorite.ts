import { useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { setFavorite, syncFavorites } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { EmailMetadataOut } from '../../../api/types/dto';

type ToggleArgs = {
  // The email's REAL mailbox (carried in the listing payload). Virtual
  // mailboxes can surface emails whose account lives in a mailbox
  // other than the route's mailbox — the toggle endpoint validates
  // ``account ∈ mailbox`` on the backend so we must always pass the
  // mailbox that actually owns the account, not the route mailbox.
  mailboxId: string;
  accountId: string;
  providerMessageId: string;
  favorite: boolean;
};

type UseFavoriteReturn = {
  toggle: (args: ToggleArgs) => Promise<void>;
  sync: (accountId?: string) => Promise<void>;
  toggling: boolean;
  syncing: boolean;
  error: UiError | null;
};

// Per-query snapshot kept for rollback when the toggle's network call
// fails. Tagged so different cache key shapes (regular listing vs
// virtual-mailbox listing) can be restored uniformly.
type CacheSnapshot = Array<[readonly unknown[], EmailMetadataOut[] | undefined]>;

export default function useFavorite(mailboxId: string): UseFavoriteReturn {
  const queryClient = useQueryClient();

  const toggleMutation = useMutation({
    mutationFn: ({
      mailboxId: itemMailboxId,
      accountId,
      providerMessageId,
      favorite,
    }: ToggleArgs) => setFavorite(itemMailboxId, accountId, providerMessageId, favorite),
    // Optimistic update so the star flips immediately and the user
    // does not perceive provider latency. The blanket invalidation in
    // ``onSettled`` reconciles with server truth — including the case
    // where the backend rejected the toggle (404 ``email_not_found``,
    // 502 provider error) and the optimistic flip needs to revert.
    onMutate: async ({ accountId, providerMessageId, favorite }) => {
      await queryClient.cancelQueries({ queryKey: ['emails', mailboxId] });
      await queryClient.cancelQueries({ queryKey: ['virtual-mailbox-emails'] });

      const prevEmails = queryClient.getQueriesData<EmailMetadataOut[]>({
        queryKey: ['emails', mailboxId],
      }) as CacheSnapshot;
      const prevVmboxEmails = queryClient.getQueriesData<EmailMetadataOut[]>({
        queryKey: ['virtual-mailbox-emails'],
      }) as CacheSnapshot;

      const applyOptimistic = (snapshot: CacheSnapshot) => {
        snapshot.forEach(([key, data]) => {
          if (!Array.isArray(data)) return;
          const updated = data.map((email) =>
            email.provider_message_id === providerMessageId && email.account_id === accountId
              ? { ...email, is_favorite: favorite }
              : email,
          );
          queryClient.setQueryData(key, updated);
        });
      };
      applyOptimistic(prevEmails);
      applyOptimistic(prevVmboxEmails);

      return { prevEmails, prevVmboxEmails };
    },
    onError: (_err, _vars, context) => {
      // Roll back every query we touched in ``onMutate`` so the UI
      // reverts to the last-known-good state. ``onSettled`` will then
      // refetch authoritative data from the server.
      const ctx = context as
        | { prevEmails?: CacheSnapshot; prevVmboxEmails?: CacheSnapshot }
        | undefined;
      ctx?.prevEmails?.forEach(([key, data]) => queryClient.setQueryData(key, data));
      ctx?.prevVmboxEmails?.forEach(([key, data]) => queryClient.setQueryData(key, data));
    },
    onSettled: () => {
      // Touch every email/virtual-mailbox listing — is_favorite can
      // appear on any of them so a blanket invalidation is the only
      // safe move. ``onSettled`` runs after both success and rollback
      // so the cache always reconciles with server truth.
      queryClient.invalidateQueries({ queryKey: ['emails', mailboxId] });
      queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
    },
  });

  const syncMutation = useMutation({
    mutationFn: (accountId?: string) => syncFavorites(mailboxId, accountId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['emails', mailboxId] });
      queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
    },
  });

  const toggle = useCallback(
    async (args: ToggleArgs) => {
      await toggleMutation.mutateAsync(args);
    },
    [toggleMutation],
  );

  const sync = useCallback(
    async (accountId?: string) => {
      await syncMutation.mutateAsync(accountId);
    },
    [syncMutation],
  );

  const error = toggleMutation.error
    ? toUiError(toggleMutation.error)
    : syncMutation.error
      ? toUiError(syncMutation.error)
      : null;

  return {
    toggle,
    sync,
    toggling: toggleMutation.isPending,
    syncing: syncMutation.isPending,
    error,
  };
}
