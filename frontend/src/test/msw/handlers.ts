import { http, HttpResponse } from 'msw';

const API_BASE = 'http://localhost:8000';

/**
 * Default happy-path handlers for every backend endpoint exercised in
 * integration tests. Individual specs override specific routes with
 * `server.use(...)` to simulate errors, empty states or edge cases.
 *
 * Handlers here must stay in sync with the backend's API contract —
 * when a new endpoint is added, add a happy-path handler for it.
 */
export const handlers = [
  // Auth
  http.post(`${API_BASE}/auth/google`, () =>
    HttpResponse.json({
      user: { user_id: 'u_test', email: 'tester@example.com', name: 'Tester', avatar_url: null },
      message: 'Logged in',
    }),
  ),
  http.post(`${API_BASE}/auth/dev-login`, () =>
    HttpResponse.json({
      user: { user_id: 'u_dev', email: 'dev@example.com', name: 'Dev User', avatar_url: null },
      message: 'Dev login successful.',
    }),
  ),
  http.get(`${API_BASE}/auth/me`, () =>
    HttpResponse.json({
      user_id: 'u_test',
      email: 'tester@example.com',
      name: 'Tester',
      avatar_url: null,
    }),
  ),
  http.post(`${API_BASE}/auth/logout`, () => HttpResponse.json({ message: 'Logged out' })),
  http.delete(`${API_BASE}/auth/me`, () => HttpResponse.json({ message: 'Deleted' })),

  // Mailboxes
  http.get(`${API_BASE}/mailboxes`, () => HttpResponse.json([])),
  http.post(`${API_BASE}/mailboxes`, () =>
    HttpResponse.json({
      mailbox_id: 'mb_test',
      display_name: 'Test mailbox',
      owner_user_id: 'u_test',
      created_at: new Date().toISOString(),
    }),
  ),

  // Accounts
  http.get(`${API_BASE}/mailboxes/:mailboxId/accounts`, () => HttpResponse.json([])),

  // Emails
  http.get(`${API_BASE}/mailboxes/:mailboxId/emails`, () => HttpResponse.json([])),
  http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, () =>
    HttpResponse.json({ total_synced: 0, accounts: [] }),
  ),
  http.post(`${API_BASE}/mailboxes/:mailboxId/emails/move-to-trash`, () =>
    HttpResponse.json({ affected: 0 }),
  ),
  http.post(`${API_BASE}/mailboxes/:mailboxId/emails/trash`, () =>
    HttpResponse.json({ affected: 0 }),
  ),
  http.post(`${API_BASE}/mailboxes/:mailboxId/emails/spam`, () =>
    HttpResponse.json({ moved_count: 0, accounts: [] }),
  ),
  http.post(`${API_BASE}/mailboxes/:mailboxId/emails/restore-from-spam`, () =>
    HttpResponse.json({ moved_count: 0, accounts: [] }),
  ),
  http.patch(`${API_BASE}/mailboxes/:mailboxId/emails/read-status`, () =>
    HttpResponse.json({ updated_count: 0, accounts: [] }),
  ),

  // Drafts
  http.get(`${API_BASE}/mailboxes/:mailboxId/drafts`, () => HttpResponse.json([])),
  http.post(`${API_BASE}/mailboxes/:mailboxId/drafts/sync`, () =>
    HttpResponse.json({ total_synced: 0, accounts: [] }),
  ),
  // Per-draft endpoints. These are also stubbed inside individual specs
  // via ``installBootstrapHandlers()`` for cases that need richer fakes;
  // these defaults exist so any spec that mounts a page touching drafts
  // does not hit an unhandled-request warning.
  http.post(
    `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/drafts`,
    async ({ params, request }) => {
      const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
      return HttpResponse.json({
        provider_draft_id: 'pdraft_test',
        account_id: String(params.accountId),
        to_recipients: Array.isArray(body.to_recipients) ? body.to_recipients : [],
        cc_recipients: Array.isArray(body.cc_recipients) ? body.cc_recipients : [],
        bcc_recipients: Array.isArray(body.bcc_recipients) ? body.bcc_recipients : [],
        subject: typeof body.subject === 'string' ? body.subject : '',
        body: typeof body.body === 'string' ? body.body : '',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        attachments: [],
        reply_kind: null,
        reply_to_message_id: null,
        reply_to_account_id: null,
        thread_id: null,
        in_reply_to: null,
        references_header: null,
      });
    },
  ),
  http.patch(
    `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/drafts/:draftId`,
    async ({ params, request }) => {
      const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
      return HttpResponse.json({
        provider_draft_id: String(params.draftId),
        account_id: String(params.accountId),
        to_recipients: Array.isArray(body.to_recipients) ? body.to_recipients : [],
        cc_recipients: Array.isArray(body.cc_recipients) ? body.cc_recipients : [],
        bcc_recipients: Array.isArray(body.bcc_recipients) ? body.bcc_recipients : [],
        subject: typeof body.subject === 'string' ? body.subject : '',
        body: typeof body.body === 'string' ? body.body : '',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        attachments: [],
        reply_kind: null,
        reply_to_message_id: null,
        reply_to_account_id: null,
        thread_id: null,
        in_reply_to: null,
        references_header: null,
      });
    },
  ),
  http.delete(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/drafts/:draftId`, () =>
    HttpResponse.json({ status: 'deleted' }),
  ),
  http.post(
    `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/drafts/:draftId/send`,
    ({ params }) =>
      HttpResponse.json({
        provider_message_id: `msg_${String(params.draftId)}`,
        provider: 'gmail',
        status: 'sent',
      }),
  ),

  // Reply / Reply All / Forward
  http.get(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/emails/:pmid/reply-context`, () =>
    HttpResponse.json({
      to_recipients: [],
      cc_recipients: [],
      bcc_recipients: [],
      subject: 'Re: ',
      body: '',
      in_reply_to: '',
      references: '',
      thread_id: '',
      reply_to_message_id: '',
      reply_kind: 'reply',
      original_from_email: '',
    }),
  ),
  // Forward attachment copy (Gmail: copies; Outlook: no-op).
  http.post(
    `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/drafts/:draftId/attachments/copy-from-email`,
    () =>
      HttpResponse.json({
        copied_count: 0,
        skipped: [],
        attachments: [],
      }),
  ),

  // Favourites
  http.patch(
    `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/emails/:pmid/favorite`,
    async ({ params, request }) => {
      const body = (await request.json()) as { favorite?: boolean };
      return HttpResponse.json({
        provider_message_id: String(params.pmid),
        account_id: String(params.accountId),
        is_favorite: Boolean(body.favorite),
      });
    },
  ),
  http.post(`${API_BASE}/mailboxes/:mailboxId/favorites/sync`, () =>
    HttpResponse.json({ total_synced: 0, accounts: [] }),
  ),

  // Virtual mailboxes
  http.get(`${API_BASE}/virtual-mailboxes`, () => HttpResponse.json([])),
  http.post(`${API_BASE}/virtual-mailboxes`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json({
      virtual_mailbox_id: 'vmb_test',
      owner_user_id: 'u_test',
      display_name: typeof body.display_name === 'string' ? body.display_name : 'Test vmbox',
      account_ids: Array.isArray(body.account_ids) ? body.account_ids : [],
      filter_payload:
        body.filter_payload && typeof body.filter_payload === 'object'
          ? (body.filter_payload as Record<string, unknown>)
          : {},
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  }),
  http.get(`${API_BASE}/virtual-mailboxes/:virtualMailboxId`, ({ params }) =>
    HttpResponse.json({
      virtual_mailbox_id: String(params.virtualMailboxId),
      owner_user_id: 'u_test',
      display_name: 'Test vmbox',
      account_ids: [],
      filter_payload: {},
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }),
  ),
  http.patch(`${API_BASE}/virtual-mailboxes/:virtualMailboxId`, async ({ params, request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json({
      virtual_mailbox_id: String(params.virtualMailboxId),
      owner_user_id: 'u_test',
      display_name: typeof body.display_name === 'string' ? body.display_name : 'Test vmbox',
      account_ids: Array.isArray(body.account_ids) ? body.account_ids : [],
      filter_payload:
        body.filter_payload && typeof body.filter_payload === 'object'
          ? (body.filter_payload as Record<string, unknown>)
          : {},
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  }),
  http.delete(`${API_BASE}/virtual-mailboxes/:virtualMailboxId`, () =>
    HttpResponse.json({ status: 'deleted' }),
  ),
  http.get(`${API_BASE}/virtual-mailboxes/:virtualMailboxId/emails`, () => HttpResponse.json([])),
];
