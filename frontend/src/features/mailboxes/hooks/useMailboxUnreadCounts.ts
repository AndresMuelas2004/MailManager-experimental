import { useQuery } from '@tanstack/react-query';

import { getUnreadCount } from '../../../api/endpoints/emails';
import { toUiError } from '../../../api/client/errors';
import type { UiError } from '../../../api/client/errors';
import type { UnreadCount } from '../../../api/types/dto';

type UseMailboxUnreadCountsReturn = {
  inboxTotal: number;
  spamTotal: number;
  // Per-account breakdown (account_id -> unread) for ALL_MAIL and SPAM. The
  // Sidebar uses these when the active scope is a single account so the nav
  // badges show that account's unread instead of the mailbox-wide total.
  inboxByAccount: Map<string, number>;
  spamByAccount: Map<string, number>;
  // Exposed for §4.3 conformance, but the Sidebar/title deliberately do NOT
  // render it: an unread-count failure simply hides the badge (the page has its
  // own queries with their own error handling). See implementation plan § 1/§6.
  error: UiError | null;
};

const toByAccount = (data?: UnreadCount): Map<string, number> =>
  new Map((data?.accounts ?? []).map((a) => [a.account_id, a.unread]));

// Mailbox-wide unread totals for the Inbox (ALL_MAIL) and Spam nav entries.
export default function useMailboxUnreadCounts(mailboxId: string): UseMailboxUnreadCountsReturn {
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
  return {
    inboxTotal: inbox.data?.total ?? 0,
    spamTotal: spam.data?.total ?? 0,
    inboxByAccount: toByAccount(inbox.data),
    spamByAccount: toByAccount(spam.data),
    error: inbox.error ? toUiError(inbox.error) : spam.error ? toUiError(spam.error) : null,
  };
}
