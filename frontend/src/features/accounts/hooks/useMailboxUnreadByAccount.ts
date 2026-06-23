import { useQuery } from '@tanstack/react-query';

import { getUnreadCount } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';

type UseMailboxUnreadByAccountReturn = {
  unreadByAccount: Map<string, number>;
  // Exposed for §4.3 conformance, but the cards deliberately do NOT render it:
  // an unread-count failure simply hides the badge (the page has its own queries
  // with their own error handling). See implementation plan § 1/§6.
  error: UiError | null;
};

// Maps account_id -> unread in ALL_MAIL, to distribute to each AccountCard.
export default function useMailboxUnreadByAccount(
  mailboxId: string,
): UseMailboxUnreadByAccountReturn {
  const query = useQuery({
    // Unread-badge key — MUST start with 'emails' so it inherits every blanket
    // invalidation of the ['emails'] prefix (useEmailViewer / useEmailBulkActions /
    // useMarkThreadRead / useFavorite / useEmailList auto-sync). MUST stay byte-for-byte
    // identical across the three unread-count hooks (mailboxes/emails/accounts) so they
    // share ONE cache entry per (mailbox, box). Editing it here only would silently break
    // the badge's auto-refresh and the cross-surface cache sharing.
    queryKey: ['emails', mailboxId, 'unread-count', 'ALL_MAIL'] as const,
    queryFn: ({ signal }) => getUnreadCount(mailboxId, 'ALL_MAIL', signal),
    enabled: mailboxId.length > 0,
  });
  const byAccount = new Map<string, number>(
    (query.data?.accounts ?? []).map((a) => [a.account_id, a.unread]),
  );
  return {
    unreadByAccount: byAccount,
    error: query.error ? toUiError(query.error) : null,
  };
}
