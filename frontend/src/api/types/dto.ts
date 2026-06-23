import { z } from 'zod';

// Generic envelopes — reused across resources for small responses.
export const statusResponseSchema = z.object({
  status: z.string(),
});
export type StatusResponse = z.infer<typeof statusResponseSchema>;

export const messageResponseSchema = z.object({
  message: z.string(),
});
export type MessageResponse = z.infer<typeof messageResponseSchema>;

// Auth
export const userOutSchema = z.object({
  user_id: z.string(),
  email: z.string(),
  name: z.string().nullable(),
  avatar_url: z.string().nullable(),
});
export type UserOut = z.infer<typeof userOutSchema>;

export const authResponseSchema = z.object({
  user: userOutSchema,
  message: z.string(),
});
export type AuthResponse = z.infer<typeof authResponseSchema>;

// Mailboxes
export const mailboxCreateSchema = z.object({
  display_name: z.string(),
});
export type MailboxCreate = z.infer<typeof mailboxCreateSchema>;

// Rename payload. Like ``mailboxCreateSchema`` it carries NO ``.min/.max``:
// request schemas are not validated at runtime (``request<T>()`` validates
// responses only). The 1..120 bound is enforced client-side in the rename
// form/hook and authoritatively by the backend's Pydantic ``MailboxUpdate``.
export const mailboxUpdateSchema = z.object({
  display_name: z.string(),
});
export type MailboxUpdate = z.infer<typeof mailboxUpdateSchema>;

export const mailboxOutSchema = z.object({
  mailbox_id: z.string(),
  display_name: z.string().nullable(),
  owner_user_id: z.string(),
  created_at: z.string(),
});
export type MailboxOut = z.infer<typeof mailboxOutSchema>;

export const mailboxListSchema = z.array(mailboxOutSchema);

// Accounts
export const accountCreateSchema = z.object({
  provider: z.string(),
  display_label: z.string(),
  config: z.record(z.string(), z.unknown()).optional(),
});
export type AccountCreate = z.infer<typeof accountCreateSchema>;

export const accountUpdateSchema = z.object({
  display_label: z.string().optional(),
  config: z.record(z.string(), z.unknown()).optional(),
});
export type AccountUpdate = z.infer<typeof accountUpdateSchema>;

export const accountOutSchema = z.object({
  account_id: z.string(),
  mailbox_id: z.string(),
  provider: z.string(),
  display_label: z.string(),
  config: z.record(z.string(), z.unknown()),
  email_address: z.string().nullable(),
});
export type AccountOut = z.infer<typeof accountOutSchema>;

export const accountListSchema = z.array(accountOutSchema);

export const accountConnectStartResponseSchema = z.object({
  provider: z.string(),
  account_id: z.string(),
  account_label: z.string(),
  authorization_url: z.string(),
  state: z.string(),
});
export type AccountConnectStartResponse = z.infer<typeof accountConnectStartResponseSchema>;

// Attachments — see decisionesTomadasAdjuntosFrontend.md §4.
export const attachmentMetadataSchema = z.object({
  attachment_id: z.string().uuid(),
  filename: z.string(),
  mime_type: z.string(),
  size: z.number().int().nonnegative(),
  is_downloaded: z.boolean(),
  is_unavailable: z.boolean(),
  position: z.number().int().nonnegative(),
});
export type AttachmentMetadata = z.infer<typeof attachmentMetadataSchema>;

export const draftAttachmentMetadataSchema = z.object({
  draft_attachment_id: z.string().uuid(),
  filename: z.string(),
  mime_type: z.string(),
  size: z.number().int().nonnegative(),
  position: z.number().int().nonnegative(),
  provider_attachment_id: z.string().nullable(),
});
export type DraftAttachmentMetadata = z.infer<typeof draftAttachmentMetadataSchema>;

export const draftAttachmentResponseSchema = draftAttachmentMetadataSchema;
export type DraftAttachmentResponse = z.infer<typeof draftAttachmentResponseSchema>;

export const failedAttachmentSchema = z.object({
  draft_attachment_id: z.string(),
  filename: z.string(),
  reason: z.string(),
});
export type FailedAttachmentDetail = z.infer<typeof failedAttachmentSchema>;

// Emails
export const emailMetadataOutSchema = z.object({
  provider_message_id: z.string(),
  account_id: z.string(),
  mailbox_id: z.string(),
  thread_id: z.string().nullable(),
  from_email: z.string(),
  from_name: z.string().nullable(),
  to_email: z.string().nullable().optional(),
  to_name: z.string().nullable().optional(),
  subject: z.string().nullable(),
  received_at: z.string(),
  is_read: z.boolean(),
  box: z.string(),
  has_attachments: z.boolean().default(false),
  is_favorite: z.boolean().default(false),
  // Number of messages of the thread present in the listed box (conversation
  // view). The backend aggregates is_read / has_attachments / is_favorite
  // across the thread when grouping; this field is the thread's message count
  // in that box. Defaults to 1 for non-grouped listings (favourites) and for
  // every message inside ``ConversationOut`` (the viewer ignores it there).
  thread_message_count: z.number().int().default(1),
});
export type EmailMetadataOut = z.infer<typeof emailMetadataOutSchema>;

export const emailMetadataListSchema = z.array(emailMetadataOutSchema);

// Full message chain of a conversation — response of
// ``GET /mailboxes/{mid}/accounts/{aid}/emails/{pmid}/conversation``.
// ``messages`` is in CHRONOLOGICAL ASCENDING order (oldest first). Each
// message reuses ``emailMetadataOutSchema``; inside this envelope the
// backend leaves ``has_attachments`` always false (B.lazy — attachments
// are discovered per-message when the body is fetched) and
// ``thread_message_count`` always 1 (unused by the viewer). The body is
// NOT included here — it is fetched per message via ``getEmailContent``.
export const conversationOutSchema = z.object({
  thread_id: z.string(),
  messages: z.array(emailMetadataOutSchema),
});
export type ConversationOut = z.infer<typeof conversationOutSchema>;

// Paginated listing envelope. The backend wraps the page of emails in
// ``{ items, total, limit, offset }``: ``total`` is the exact count of
// the full filtered set in the local copy (not just this page), while
// ``limit`` / ``offset`` echo back the values the backend applied.
export const emailPageSchema = z.object({
  items: z.array(emailMetadataOutSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
});
export type EmailPage = z.infer<typeof emailPageSchema>;

export const emailContentOutSchema = z.object({
  html_body: z.string().nullable(),
  text_body: z.string().nullable(),
  attachments: z.array(attachmentMetadataSchema).default([]),
});
export type EmailContentOut = z.infer<typeof emailContentOutSchema>;

export const emailSendRequestSchema = z.object({
  account_id: z.string(),
  subject: z.string(),
  // ``body`` is HTML (rich-text composer). The 1..1_000_000 bounds document
  // the contract and refine the inferred type, but DO NOT run at runtime:
  // ``request<T>()`` validates responses, never request bodies. The real
  // client-side size guard is ``bodyError`` in ``useDraftComposer``; the
  // backend's Pydantic ``max_length`` is the authoritative enforcement.
  body: z.string().min(1).max(1_000_000),
  recipients: z.array(z.string()),
});
export type EmailSendRequest = z.infer<typeof emailSendRequestSchema>;

export const emailItemRefSchema = z.object({
  account_id: z.string(),
  provider_message_id: z.string(),
});
export type EmailItemRef = z.infer<typeof emailItemRefSchema>;

export const accountSyncDetailSchema = z.object({
  account_id: z.string(),
  provider: z.string(),
  emails_synced: z.number(),
  sync_cursor: z.string().nullable(),
});
export type AccountSyncDetail = z.infer<typeof accountSyncDetailSchema>;

export const syncResultOutSchema = z.object({
  total_synced: z.number(),
  accounts: z.array(accountSyncDetailSchema),
});
export type SyncResultOut = z.infer<typeof syncResultOutSchema>;

export const moveToTrashResultSchema = z.object({
  affected: z.number(),
});
export type MoveToTrashResult = z.infer<typeof moveToTrashResultSchema>;

export const trashActionResultSchema = z.object({
  affected: z.number(),
});
export type TrashActionResult = z.infer<typeof trashActionResultSchema>;

export const readStatusResponseSchema = z.object({
  updated_count: z.number(),
  accounts: z.array(
    z.object({
      account_id: z.string(),
      updated: z.number(),
    }),
  ),
});
export type ReadStatusResponse = z.infer<typeof readStatusResponseSchema>;

export const spamResponseSchema = z.object({
  moved_count: z.number(),
  accounts: z.array(
    z.object({
      account_id: z.string(),
      moved: z.number(),
    }),
  ),
});
export type SpamResponse = z.infer<typeof spamResponseSchema>;

// Unread-count response of ``GET /mailboxes/{mid}/emails/unread-count?box=...``.
// ``total`` is the sum of unread messages of the mailbox in that box; ``accounts``
// is the per-account breakdown including accounts with 0 unread. ``box`` is a
// plain string (mirrors ``box`` in ``emailMetadataOutSchema`` — the backend
// validates the value against ``ALL_MAIL``/``SPAM`` on the request side).
export const accountUnreadDetailSchema = z.object({
  account_id: z.string(),
  unread: z.number(),
});
export type AccountUnreadDetail = z.infer<typeof accountUnreadDetailSchema>;

export const unreadCountSchema = z.object({
  mailbox_id: z.string(),
  box: z.string(),
  total: z.number(),
  accounts: z.array(accountUnreadDetailSchema),
});
export type UnreadCount = z.infer<typeof unreadCountSchema>;

// Drafts — body is HTML (rich-text composer; sanitised server-side).
// The ``.max(1_000_000)`` on the request schemas documents the contract and
// refines the type only — it does NOT run at runtime (``request<T>()`` only
// validates responses). The six reply / forward fields mirror the backend
// ``DraftCreate`` / ``DraftOut`` extensions. Zod's default extra-key policy
// ("allow") is intentionally kept — the backend does NOT set ``extra="forbid"``
// so older clients with unknown fields keep working.
export const replyKindSchema = z.enum(['reply', 'reply_all', 'forward']);
export type ReplyKindDto = z.infer<typeof replyKindSchema>;

export const draftCreateSchema = z.object({
  to_recipients: z.array(z.string()).optional(),
  cc_recipients: z.array(z.string()).optional(),
  bcc_recipients: z.array(z.string()).optional(),
  subject: z.string().optional(),
  body: z.string().max(1_000_000).optional(),
  reply_kind: replyKindSchema.nullable().optional(),
  reply_to_message_id: z.string().nullable().optional(),
  reply_to_account_id: z.string().nullable().optional(),
  thread_id: z.string().nullable().optional(),
  in_reply_to: z.string().nullable().optional(),
  references_header: z.string().nullable().optional(),
});
export type DraftCreate = z.infer<typeof draftCreateSchema>;

export const draftUpdateSchema = z.object({
  to_recipients: z.array(z.string()).optional(),
  cc_recipients: z.array(z.string()).optional(),
  bcc_recipients: z.array(z.string()).optional(),
  subject: z.string().optional(),
  body: z.string().max(1_000_000).optional(),
});
export type DraftUpdate = z.infer<typeof draftUpdateSchema>;

export const draftOutSchema = z.object({
  provider_draft_id: z.string(),
  account_id: z.string(),
  to_recipients: z.array(z.string()),
  cc_recipients: z.array(z.string()),
  bcc_recipients: z.array(z.string()),
  subject: z.string(),
  body: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  attachments: z.array(draftAttachmentMetadataSchema).default([]),
  reply_kind: replyKindSchema.nullable().optional(),
  reply_to_message_id: z.string().nullable().optional(),
  reply_to_account_id: z.string().nullable().optional(),
  thread_id: z.string().nullable().optional(),
  in_reply_to: z.string().nullable().optional(),
  references_header: z.string().nullable().optional(),
});
export type DraftOut = z.infer<typeof draftOutSchema>;

export const draftListSchema = z.array(draftOutSchema);

export const draftsAccountSyncDetailSchema = z.object({
  account_id: z.string(),
  provider: z.string(),
  drafts_synced: z.number(),
});
export type DraftsAccountSyncDetail = z.infer<typeof draftsAccountSyncDetailSchema>;

export const draftsSyncResultOutSchema = z.object({
  total_synced: z.number(),
  accounts: z.array(draftsAccountSyncDetailSchema),
});
export type DraftsSyncResultOut = z.infer<typeof draftsSyncResultOutSchema>;

export const draftSendOutSchema = z.object({
  provider_message_id: z.string(),
  provider: z.string(),
  status: z.string(),
});
export type DraftSendOut = z.infer<typeof draftSendOutSchema>;

// Favourites
export const favoriteUpdateRequestSchema = z.object({
  favorite: z.boolean(),
});
export type FavoriteUpdateRequest = z.infer<typeof favoriteUpdateRequestSchema>;

export const favoriteUpdateResponseSchema = z.object({
  provider_message_id: z.string(),
  account_id: z.string(),
  is_favorite: z.boolean(),
});
export type FavoriteUpdateResponse = z.infer<typeof favoriteUpdateResponseSchema>;

export const favoriteSyncAccountDetailSchema = z.object({
  account_id: z.string(),
  provider: z.string(),
  favorites_synced: z.number(),
});
export type FavoriteSyncAccountDetail = z.infer<typeof favoriteSyncAccountDetailSchema>;

export const favoriteSyncResponseSchema = z.object({
  total_synced: z.number(),
  accounts: z.array(favoriteSyncAccountDetailSchema),
});
export type FavoriteSyncResponse = z.infer<typeof favoriteSyncResponseSchema>;

// Virtual mailboxes (fake mailboxes — filtered views over email_metadata).
// Shape after migration 0032: a virtual mailbox is a flat list of
// ``account_ids`` plus a filter. There is no ``scope_kind`` indirection.
export const virtualMailboxFilterBoxSchema = z.enum(['ALL_MAIL', 'SENT', 'SPAM', 'TRASH']);
export type VirtualMailboxFilterBox = z.infer<typeof virtualMailboxFilterBoxSchema>;

export const virtualMailboxFilterPayloadSchema = z.object({
  box: virtualMailboxFilterBoxSchema.optional(),
  box_not_in: z.array(virtualMailboxFilterBoxSchema).optional(),
  from_email: z.string().optional(),
  subject_contains: z.string().optional(),
  is_read: z.boolean().optional(),
  is_favorite: z.boolean().optional(),
});
export type VirtualMailboxFilterPayload = z.infer<typeof virtualMailboxFilterPayloadSchema>;

export const virtualMailboxCreateSchema = z.object({
  display_name: z.string().min(1).max(120),
  account_ids: z.array(z.string()).min(1),
  filter_payload: virtualMailboxFilterPayloadSchema,
});
export type VirtualMailboxCreate = z.infer<typeof virtualMailboxCreateSchema>;

export const virtualMailboxUpdateSchema = virtualMailboxCreateSchema;
export type VirtualMailboxUpdate = z.infer<typeof virtualMailboxUpdateSchema>;

// The backend stores filter_payload as JSONB and re-emits it; we keep
// it loosely typed in the OUT shape because the create/update side
// already enforces the constrained schema.
export const virtualMailboxOutSchema = z.object({
  virtual_mailbox_id: z.string(),
  owner_user_id: z.string(),
  display_name: z.string(),
  account_ids: z.array(z.string()),
  filter_payload: z.record(z.string(), z.unknown()),
  created_at: z.string(),
  updated_at: z.string(),
});
export type VirtualMailboxOut = z.infer<typeof virtualMailboxOutSchema>;

export const virtualMailboxListSchema = z.array(virtualMailboxOutSchema);

// Reply / Reply All / Forward context — response payload for
// ``GET /mailboxes/{mid}/accounts/{aid}/emails/{pmid}/reply-context``.
// Every value is already computed server-side: ``to_recipients`` /
// ``cc_recipients`` apply Reply-To and self-reply rules, ``subject``
// carries the ``Re:`` / ``Fwd:`` prefix, ``body`` is HTML with the original
// quoted inside a ``<blockquote>`` preceded by the attribution line, ready to
// seed into the editor. The composer prefills with these values directly —
// no further computation on the frontend.
export const replyContextOutSchema = z.object({
  to_recipients: z.array(z.string()),
  cc_recipients: z.array(z.string()),
  bcc_recipients: z.array(z.string()).default([]),
  subject: z.string(),
  body: z.string(),
  in_reply_to: z.string(),
  references: z.string(),
  thread_id: z.string(),
  reply_to_message_id: z.string(),
  reply_kind: replyKindSchema,
  original_from_email: z.string(),
});
export type ReplyContextOut = z.infer<typeof replyContextOutSchema>;

// Forward attachment copy — request body and response payload for
// ``POST /mailboxes/{mid}/accounts/{aid}/drafts/{pdid}/attachments/copy-from-email``.
export const copyAttachmentsFromEmailRequestSchema = z.object({
  source_account_id: z.string(),
  source_provider_message_id: z.string().min(1).max(255),
});
export type CopyAttachmentsFromEmailRequest = z.infer<typeof copyAttachmentsFromEmailRequestSchema>;

export const copyAttachmentsFromEmailResponseSchema = z.object({
  copied_count: z.number().int().nonnegative(),
  skipped: z.array(z.record(z.string(), z.string())).default([]),
  attachments: z.array(draftAttachmentMetadataSchema).default([]),
});
export type CopyAttachmentsFromEmailResponse = z.infer<
  typeof copyAttachmentsFromEmailResponseSchema
>;

// Recipient autocomplete (contacts) — items returned by
// ``GET /contacts/suggestions``. ``email`` is validated as a plain
// ``z.string()`` (not ``.email()``) so odd-but-valid addresses the
// backend already stored are never rejected at the boundary. ``name``
// is ``.nullable()``: the backend (``ContactSuggestionOut``) always
// serialises the key, emitting ``"name": null`` when unknown — it is
// never omitted, so ``.nullable()`` (not ``.optional()``) is correct.
export const contactSuggestionSchema = z.object({
  email: z.string(),
  name: z.string().nullable(),
});
export type ContactSuggestion = z.infer<typeof contactSuggestionSchema>;

export const contactSuggestionsResponseSchema = z.array(contactSuggestionSchema);
export type ContactSuggestionsResponse = z.infer<typeof contactSuggestionsResponseSchema>;
