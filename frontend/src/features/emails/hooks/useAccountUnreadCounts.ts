import { useQuery } from '@tanstack/react-query';

import { getUnreadCount } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { UnreadCount } from '../../../api/types/dto';

type UseAccountUnreadCountsReturn = {
  inboxUnread: number;
  spamUnread: number;
  // Exposed for §4.3 conformance, but the account tabs deliberately do NOT
  // render it: an unread-count failure simply hides the badge (the page has its
  // own queries with their own error handling). See implementation plan § 1/§6.
  error: UiError | null;
};

// Per-account unread counts for the account tabs (Bandeja/Spam). Fetches the
// mailbox-wide counts (same key/cache as the Sidebar) and extracts this account
// from the breakdown.
export default function useAccountUnreadCounts(
  mailboxId: string,
  accountId: string,
): UseAccountUnreadCountsReturn {
  const inbox = useQuery({
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
  const spam = useQuery({
    // Unread-badge key — MUST start with 'emails' so it inherits every blanket
    // invalidation of the ['emails'] prefix (useEmailViewer / useEmailBulkActions /
    // useMarkThreadRead / useFavorite / useEmailList auto-sync). MUST stay byte-for-byte
    // identical across the three unread-count hooks (mailboxes/emails/accounts) so they
    // share ONE cache entry per (mailbox, box). Editing it here only would silently break
    // the badge's auto-refresh and the cross-surface cache sharing.
    queryKey: ['emails', mailboxId, 'unread-count', 'SPAM'] as const,
    queryFn: ({ signal }) => getUnreadCount(mailboxId, 'SPAM', signal),
    enabled: mailboxId.length > 0,
  });
  const pick = (data?: UnreadCount) =>
    data?.accounts.find((a) => a.account_id === accountId)?.unread ?? 0;
  return {
    inboxUnread: pick(inbox.data),
    spamUnread: pick(spam.data),
    error: inbox.error ? toUiError(inbox.error) : spam.error ? toUiError(spam.error) : null,
  };
}
