import { useQuery } from '@tanstack/react-query';

import { toUiError } from '../../../api/client/errors';
import { emailContentQueryOptions } from './emailContentQueryOptions';
import type { EmailContentOut } from '../../../api/types/dto';
import type { UiError } from '../../../api/client/errors';

type Target = { account_id: string; provider_message_id: string };

type UseEmailContentReturn = {
  content: EmailContentOut | null;
  loading: boolean;
  error: UiError | null;
};

// Reads the sanitized body from the in-memory TanStack Query cache. Reopening
// the same email (Favoritos viewer) or re-expanding a conversation message is a
// cache hit — no spinner, no network — because the query is warmed by
// ``useEmailContentPrefetch`` on listing load and kept fresh forever
// (``staleTime: Infinity`` in ``emailContentQueryOptions``). The return shape is
// unchanged from the previous useState/useEffect implementation so the two
// consumers (ViewerWithDownloader, ConversationMessageBody) are untouched.
export default function useEmailContent(mailboxId: string, target: Target): UseEmailContentReturn {
  const { account_id: accountId, provider_message_id: providerMessageId } = target;
  const query = useQuery(emailContentQueryOptions(mailboxId, accountId, providerMessageId));
  return {
    content: query.data ?? null,
    loading: query.isLoading,
    error: query.error ? toUiError(query.error) : null,
  };
}
