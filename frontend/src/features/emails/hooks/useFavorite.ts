import { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { setFavorite, syncFavorites } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { ConversationOut, EmailPage } from '../../../api/types/dto';

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

type SyncArgs = {
  // Always the email's REAL mailbox. In a virtual-mailbox spanning
  // several real mailboxes, the caller is responsible for fanning out
  // one ``sync()`` per unique (mailbox_id, account_id) pair —
  // ``useFavorite`` cannot know which mailboxes to touch on its own.
  mailboxId: string;
  accountId?: string;
};

type UseFavoriteReturn = {
  toggle: (args: ToggleArgs) => Promise<void>;
  sync: (args: SyncArgs) => Promise<void>;
  toggling: boolean;
  syncing: boolean;
  // Per-email in-flight flag for the row star in the Favoritos tab, where a
  // single hook instance drives the whole table. The global ``toggling`` would
  // disable every row at once, so callers query this helper by (accountId,
  // providerMessageId) to disable only the row being saved. Derived from the
  // ``pendingKeys`` set; its identity changes when that set changes, which is
  // what re-renders the table into the disabled state.
  isToggling: (accountId: string, providerMessageId: string) => boolean;
  error: UiError | null;
  // The favourites SYNC error, kept apart from the toggle ``error`` so a page
  // can surface it as a NON-blocking notice instead of replacing the whole
  // list (a single account's auth failure must not hide the healthy accounts'
  // favourites — mirrors how the unified inbox keeps its sync error off the
  // table-replacing path).
  syncError: UiError | null;
};

// Per-query snapshots kept for rollback when the toggle's network call fails.
// The two listing surfaces share the ``EmailPage`` envelope
// (``{ items, total, limit, offset }``); the conversation surface is the
// distinct ``ConversationOut`` envelope (``{ thread_id, messages }``), so it
// needs its own snapshot shape rather than reusing the listing one.
type EmailPageSnapshot = Array<[readonly unknown[], EmailPage | undefined]>;
type ConversationSnapshot = Array<[readonly unknown[], ConversationOut | undefined]>;

// Build the per-email cache key used by ``pendingKeys``. Independent of the
// ``account_id|provider_message_id`` row key EmailTable uses — the two never
// need to align.
function pendingKey(accountId: string, providerMessageId: string): string {
  return `${accountId}:${providerMessageId}`;
}

// Rewrite ``is_favorite`` to ``nextFavorite`` on the matching email of every
// cached ``EmailPage`` (the listing and the virtual-mailbox surfaces). New
// object + new ``items`` array per entry — TanStack compares by reference, so
// in-place mutation would not trigger a re-render. Entries whose data is not
// yet populated (``undefined``) are skipped.
function applyFavoriteToEmailPages(
  queryClient: ReturnType<typeof useQueryClient>,
  prefix: 'emails' | 'virtual-mailbox-emails',
  accountId: string,
  providerMessageId: string,
  nextFavorite: boolean,
): EmailPageSnapshot {
  const snapshot = queryClient.getQueriesData<EmailPage>({
    queryKey: [prefix],
  }) as EmailPageSnapshot;
  snapshot.forEach(([key, data]) => {
    // The ``['emails']`` prefix is shared on purpose: the unread-count badge
    // queries are keyed ``['emails', mailbox, 'unread-count', box]`` so they
    // inherit this hook's blanket invalidation (see ``useMailboxUnreadCounts``).
    // Those entries are ``UnreadCount`` envelopes, not ``EmailPage`` listings, so
    // they carry no ``items`` array — skip them rather than calling ``.map`` on
    // ``undefined`` (which aborts the whole optimistic update in ``onMutate``).
    if (!data || !Array.isArray(data.items)) return;
    queryClient.setQueryData<EmailPage>(key, {
      ...data,
      items: data.items.map((email) =>
        email.provider_message_id === providerMessageId && email.account_id === accountId
          ? { ...email, is_favorite: nextFavorite }
          : email,
      ),
    });
  });
  return snapshot;
}

// Same rewrite for every cached ``ConversationOut`` (one entry per open/cached
// conversation, keyed by the base message it was opened from). The touched
// message can appear in ``messages`` of several entries at once (two accounts
// of one thread, or the thread opened from different base messages), so all
// entries are mapped; the match-by-(provider_message_id, account_id) is a
// no-op on the ones that do not contain it.
function applyFavoriteToConversations(
  queryClient: ReturnType<typeof useQueryClient>,
  accountId: string,
  providerMessageId: string,
  nextFavorite: boolean,
): ConversationSnapshot {
  const snapshot = queryClient.getQueriesData<ConversationOut>({
    queryKey: ['conversation'],
  }) as ConversationSnapshot;
  snapshot.forEach(([key, data]) => {
    if (!data) return;
    queryClient.setQueryData<ConversationOut>(key, {
      ...data,
      messages: data.messages.map((message) =>
        message.provider_message_id === providerMessageId && message.account_id === accountId
          ? { ...message, is_favorite: nextFavorite }
          : message,
      ),
    });
  });
  return snapshot;
}

type ToggleContext = {
  prevEmails: EmailPageSnapshot;
  prevVmboxEmails: EmailPageSnapshot;
  prevConversations: ConversationSnapshot;
};

// ``useFavorite`` is intentionally not parameterized by ``mailboxId``.
// Both operations (toggle, sync) receive the mailbox per call, which is
// the only way to handle a virtual-mailbox view correctly: a vmbox can
// aggregate emails whose accounts live in several real mailboxes, and
// the backend endpoints are mailbox-scoped. A constructor-level
// mailboxId would silently bind every call to whatever mailbox is in
// the URL, which is wrong for virtual mailboxes. The blanket cache
// invalidation (queryKey ``['emails']`` without a scope,
// ``['virtual-mailbox-emails']`` and ``['conversation']``) is the price
// of that generality — it mirrors what the bulk-actions hook already does
// and keeps every listing and the open conversation in sync regardless of
// which mailbox was touched.
export default function useFavorite(): UseFavoriteReturn {
  const queryClient = useQueryClient();
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(() => new Set());

  const toggleMutation = useMutation({
    mutationFn: ({
      mailboxId: itemMailboxId,
      accountId,
      providerMessageId,
      favorite,
    }: ToggleArgs) => setFavorite(itemMailboxId, accountId, providerMessageId, favorite),
    // Optimistic update so the star flips immediately and the user does not
    // perceive provider latency — on the row star (``EmailPage`` listings),
    // the virtual-mailbox listings, AND the conversation viewer's per-message
    // button (``ConversationOut``). The blanket invalidation in ``onSettled``
    // reconciles with server truth, including the case where the backend
    // rejected the toggle (404 ``email_not_found``, 502 provider error) and
    // the optimistic flip needs to revert via ``onError``.
    onMutate: async ({ accountId, providerMessageId, favorite }): Promise<ToggleContext> => {
      await queryClient.cancelQueries({ queryKey: ['emails'] });
      await queryClient.cancelQueries({ queryKey: ['virtual-mailbox-emails'] });
      await queryClient.cancelQueries({ queryKey: ['conversation'] });

      const prevEmails = applyFavoriteToEmailPages(
        queryClient,
        'emails',
        accountId,
        providerMessageId,
        favorite,
      );
      const prevVmboxEmails = applyFavoriteToEmailPages(
        queryClient,
        'virtual-mailbox-emails',
        accountId,
        providerMessageId,
        favorite,
      );
      const prevConversations = applyFavoriteToConversations(
        queryClient,
        accountId,
        providerMessageId,
        favorite,
      );

      return { prevEmails, prevVmboxEmails, prevConversations };
    },
    onSuccess: (data) => {
      // Reconcile from the server's authoritative ``is_favorite`` so there is
      // no flicker between the optimistic flip and the ``onSettled`` refetch.
      // Same key-by-key walk as ``onMutate``, but pinning the response value
      // instead of flipping. On the happy path (Provider-First) this equals the
      // optimistic value; it only differs if the server reports otherwise.
      applyFavoriteToEmailPages(
        queryClient,
        'emails',
        data.account_id,
        data.provider_message_id,
        data.is_favorite,
      );
      applyFavoriteToEmailPages(
        queryClient,
        'virtual-mailbox-emails',
        data.account_id,
        data.provider_message_id,
        data.is_favorite,
      );
      applyFavoriteToConversations(
        queryClient,
        data.account_id,
        data.provider_message_id,
        data.is_favorite,
      );
    },
    onError: (_err, _vars, context) => {
      // Roll back every query we touched in ``onMutate`` so the UI reverts to
      // the last-known-good state. ``onSettled`` will then refetch authoritative
      // data from the server.
      const ctx = context as ToggleContext | undefined;
      ctx?.prevEmails.forEach(([key, data]) => queryClient.setQueryData<EmailPage>(key, data));
      ctx?.prevVmboxEmails.forEach(([key, data]) => queryClient.setQueryData<EmailPage>(key, data));
      ctx?.prevConversations.forEach(([key, data]) =>
        queryClient.setQueryData<ConversationOut>(key, data),
      );
    },
    onSettled: () => {
      // Touch every email/virtual-mailbox listing and the open conversation —
      // is_favorite can appear on any of them so a blanket invalidation is the
      // only safe move. ``onSettled`` runs after both success and rollback so
      // the cache always reconciles with server truth.
      queryClient.invalidateQueries({ queryKey: ['emails'] });
      queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
      queryClient.invalidateQueries({ queryKey: ['conversation'] });
    },
  });

  const syncMutation = useMutation({
    mutationFn: ({ mailboxId, accountId }: SyncArgs) => syncFavorites(mailboxId, accountId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['emails'] });
      queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
      queryClient.invalidateQueries({ queryKey: ['conversation'] });
    },
  });

  const toggle = useCallback(
    async (args: ToggleArgs) => {
      const key = pendingKey(args.accountId, args.providerMessageId);
      // New set per update so the state reference changes (drives the row
      // disable re-render); the ``finally`` clears the key after success or
      // error.
      setPendingKeys((prev) => {
        const next = new Set(prev);
        next.add(key);
        return next;
      });
      try {
        await toggleMutation.mutateAsync(args);
      } finally {
        setPendingKeys((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
      }
    },
    [toggleMutation],
  );

  const sync = useCallback(
    async (args: SyncArgs) => {
      await syncMutation.mutateAsync(args);
    },
    [syncMutation],
  );

  // Memoised on ``pendingKeys`` so its identity changes exactly when the set
  // changes — that identity change is what re-renders consumers (EmailTable)
  // into / out of the disabled state. Do NOT stabilise this with a ref: it
  // would freeze the disable feedback.
  const isToggling = useCallback(
    (accountId: string, providerMessageId: string) =>
      pendingKeys.has(pendingKey(accountId, providerMessageId)),
    [pendingKeys],
  );

  const error = toggleMutation.error ? toUiError(toggleMutation.error) : null;
  const syncError = syncMutation.error ? toUiError(syncMutation.error) : null;

  return {
    toggle,
    sync,
    toggling: toggleMutation.isPending,
    syncing: syncMutation.isPending,
    isToggling,
    error,
    syncError,
  };
}
