import { request } from '../client/http';
import { EMAILS_PAGE_SIZE } from '../../lib/constants';
import type { SortKey, SortDir } from '../../lib/listControls';
import {
  archiveResponseSchema,
  conversationOutSchema,
  emailContentOutSchema,
  emailPageSchema,
  favoriteSyncResponseSchema,
  favoriteUpdateResponseSchema,
  moveToTrashResultSchema,
  readStatusResponseSchema,
  replyContextOutSchema,
  spamResponseSchema,
  statusResponseSchema,
  syncResultOutSchema,
  trashActionResultSchema,
  unreadCountSchema,
  type ArchiveResponse,
  type ConversationOut,
  type EmailContentOut,
  type EmailItemRef,
  type EmailPage,
  type EmailSendRequest,
  type FavoriteSyncResponse,
  type FavoriteUpdateResponse,
  type MoveToTrashResult,
  type ReadStatusResponse,
  type ReplyContextOut,
  type ReplyKindDto,
  type SpamResponse,
  type StatusResponse,
  type SyncResultOut,
  type TrashActionResult,
  type UnreadCount,
} from '../types/dto';

export type ListEmailsOptions = {
  q?: string;
  favorite?: boolean; // anchor (Favoritos) — distinct from the favoriteOnly chip
  page?: number;
  groupByThread?: boolean;
  // Sort + quick filters. Internal frontend names; some are translated to the
  // wire names below (``dir``→``sort_dir``, ``hasAttachment``→``has_attachment``,
  // ``favoriteOnly``→``favorite_only``); ``sort`` and ``unread`` match the wire.
  sort?: SortKey;
  dir?: SortDir;
  unread?: boolean;
  hasAttachment?: boolean;
  favoriteOnly?: boolean;
  signal?: AbortSignal;
};

export function listEmails(
  mailboxId: string,
  box: string,
  accountId?: string,
  options: ListEmailsOptions = {},
): Promise<EmailPage> {
  const params = new URLSearchParams({ box });
  if (accountId) params.set('account_id', accountId);
  if (options.q !== undefined && options.q.length > 0) params.set('q', options.q);
  if (options.favorite !== undefined) params.set('favorite', String(options.favorite));
  // Only send group_by_thread when truthy — the backend treats its absence as
  // default=False, mirroring how ``favorite`` is omitted when undefined.
  if (options.groupByThread) params.set('group_by_thread', 'true');
  // Sort: only when it differs from the default (clean URLs / stable cache).
  if (options.sort && options.sort !== 'date') params.set('sort', options.sort);
  if (options.dir === 'asc') params.set('sort_dir', 'asc');
  // Quick filters: only when active. Frontend name -> wire name mapping.
  if (options.unread) params.set('unread', 'true');
  if (options.hasAttachment) params.set('has_attachment', 'true');
  if (options.favoriteOnly) params.set('favorite_only', 'true');
  const limit = EMAILS_PAGE_SIZE;
  const offset = ((options.page ?? 1) - 1) * limit;
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  return request(`/mailboxes/${mailboxId}/emails?${params}`, {
    schema: emailPageSchema,
    signal: options.signal,
  });
}

export function setFavorite(
  mailboxId: string,
  accountId: string,
  providerMessageId: string,
  favorite: boolean,
): Promise<FavoriteUpdateResponse> {
  return request(
    `/mailboxes/${mailboxId}/accounts/${accountId}/emails/${providerMessageId}/favorite`,
    {
      method: 'PATCH',
      body: { favorite },
      schema: favoriteUpdateResponseSchema,
    },
  );
}

export function syncFavorites(
  mailboxId: string,
  accountId?: string,
): Promise<FavoriteSyncResponse> {
  const params = accountId ? `?account_id=${accountId}` : '';
  return request(`/mailboxes/${mailboxId}/favorites/sync${params}`, {
    method: 'POST',
    schema: favoriteSyncResponseSchema,
  });
}

export function getReplyContext(
  mailboxId: string,
  accountId: string,
  providerMessageId: string,
  action: ReplyKindDto,
): Promise<ReplyContextOut> {
  const params = new URLSearchParams({ action });
  return request(
    `/mailboxes/${mailboxId}/accounts/${accountId}/emails/${providerMessageId}/reply-context?${params}`,
    { schema: replyContextOutSchema },
  );
}

// Full conversation chain for the message the user opened. The thread is
// identified by ``providerMessageId`` (NOT thread_id) because Outlook's
// conversationId is base64 with ``/`` / ``+`` / ``=`` and would break a
// path segment — the backend derives the thread from the message row.
// Unlike the sibling endpoints (``getEmailContent`` / ``getReplyContext`` /
// ``setFavorite``) this one URL-encodes the id, since Outlook's ImmutableId
// can contain ``/``.
export function getConversation(
  mailboxId: string,
  accountId: string,
  providerMessageId: string,
  signal?: AbortSignal,
): Promise<ConversationOut> {
  return request(
    `/mailboxes/${mailboxId}/accounts/${accountId}/emails/${encodeURIComponent(
      providerMessageId,
    )}/conversation`,
    { schema: conversationOutSchema, signal },
  );
}

export function getEmailContent(
  mailboxId: string,
  providerMessageId: string,
  accountId: string,
): Promise<EmailContentOut> {
  const params = new URLSearchParams({ account_id: accountId });
  return request(`/mailboxes/${mailboxId}/emails/${providerMessageId}/content?${params}`, {
    schema: emailContentOutSchema,
  });
}

export function getUnreadCount(
  mailboxId: string,
  box: 'ALL_MAIL' | 'SPAM',
  signal?: AbortSignal,
): Promise<UnreadCount> {
  const params = new URLSearchParams({ box });
  return request(`/mailboxes/${mailboxId}/emails/unread-count?${params}`, {
    schema: unreadCountSchema,
    signal,
  });
}

export function syncEmailMetadata(mailboxId: string, accountId?: string): Promise<SyncResultOut> {
  const params = accountId ? `?account_id=${accountId}` : '';
  return request(`/mailboxes/${mailboxId}/emails/sync-metadata${params}`, {
    method: 'POST',
    schema: syncResultOutSchema,
  });
}

export function sendEmail(mailboxId: string, payload: EmailSendRequest): Promise<StatusResponse> {
  return request(`/mailboxes/${mailboxId}/emails/send`, {
    method: 'POST',
    body: payload,
    schema: statusResponseSchema,
  });
}

export function moveToTrash(mailboxId: string, items: EmailItemRef[]): Promise<MoveToTrashResult> {
  return request(`/mailboxes/${mailboxId}/emails/move-to-trash`, {
    method: 'POST',
    body: { items },
    schema: moveToTrashResultSchema,
  });
}

export function trashAction(
  mailboxId: string,
  action: 'delete' | 'restore',
  items: EmailItemRef[],
): Promise<TrashActionResult> {
  return request(`/mailboxes/${mailboxId}/emails/trash`, {
    method: 'POST',
    body: { action, items },
    schema: trashActionResultSchema,
  });
}

export function updateReadStatus(
  mailboxId: string,
  isRead: boolean,
  items: EmailItemRef[],
): Promise<ReadStatusResponse> {
  return request(`/mailboxes/${mailboxId}/emails/read-status`, {
    method: 'PATCH',
    body: { is_read: isRead, items },
    schema: readStatusResponseSchema,
  });
}

export function markAsSpam(mailboxId: string, items: EmailItemRef[]): Promise<SpamResponse> {
  return request(`/mailboxes/${mailboxId}/emails/spam`, {
    method: 'POST',
    body: { items },
    schema: spamResponseSchema,
  });
}

export function restoreFromSpam(mailboxId: string, items: EmailItemRef[]): Promise<SpamResponse> {
  return request(`/mailboxes/${mailboxId}/emails/restore-from-spam`, {
    method: 'POST',
    body: { items },
    schema: spamResponseSchema,
  });
}

export function archiveEmails(mailboxId: string, items: EmailItemRef[]): Promise<ArchiveResponse> {
  return request(`/mailboxes/${mailboxId}/emails/archive`, {
    method: 'POST',
    body: { items },
    schema: archiveResponseSchema,
  });
}

export function unarchiveEmails(
  mailboxId: string,
  items: EmailItemRef[],
): Promise<ArchiveResponse> {
  return request(`/mailboxes/${mailboxId}/emails/restore-from-archive`, {
    method: 'POST',
    body: { items },
    schema: archiveResponseSchema,
  });
}
