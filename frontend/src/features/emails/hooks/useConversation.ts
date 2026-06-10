import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { getConversation } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import type { EmailMetadataOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type UseConversationReturn = {
  messages: EmailMetadataOut[];
  threadId: string;
  loading: boolean;
  error: UiError | null;
};

// Fetches the full message chain for an opened conversation.
//
// Cache policy override (same rationale as ``useVirtualMailboxEmails``):
// the backend does a lightweight provider call on every fetch to refresh
// the thread's membership/state, and a conversation is a time-sensitive
// view — re-opening the viewer must surface the current state, not a 30 s
// stale snapshot. So ``staleTime: 0`` + ``refetchOnMount: 'always'`` defeat
// the global 30 s freshness window.
//
// The backend lazy-syncs (persists) thread messages it did not have locally
// on every successful fetch, which can change listing counts / order. That
// side effect must invalidate ``['emails']`` + ``['virtual-mailbox-emails']``.
// TanStack Query v5 removed ``onSuccess`` from ``useQuery``, so the
// invalidation rides a ``useEffect`` guarded by ``dataUpdatedAt`` — it fires
// once per successful fetch, never per render.
export default function useConversation(
  mailboxId: string,
  accountId: string,
  providerMessageId: string,
  enabled: boolean,
): UseConversationReturn {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['conversation', mailboxId, accountId, providerMessageId],
    queryFn: ({ signal }) => getConversation(mailboxId, accountId, providerMessageId, signal),
    enabled,
    refetchOnMount: 'always',
    staleTime: 0,
  });

  const lastInvalidatedAt = useRef(0);
  useEffect(() => {
    if (!query.isSuccess) return;
    if (query.dataUpdatedAt === lastInvalidatedAt.current) return;
    lastInvalidatedAt.current = query.dataUpdatedAt;
    void queryClient.invalidateQueries({ queryKey: ['emails'] });
    void queryClient.invalidateQueries({ queryKey: ['virtual-mailbox-emails'] });
  }, [query.isSuccess, query.dataUpdatedAt, queryClient]);

  return {
    messages: query.data?.messages ?? [],
    threadId: query.data?.thread_id ?? '',
    loading: query.isLoading,
    error: query.error ? toUiError(query.error) : null,
  };
}
