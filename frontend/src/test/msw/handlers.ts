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
  http.post(`${API_BASE}/auth/microsoft`, () =>
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
  http.patch(`${API_BASE}/mailboxes/:mailboxId`, async ({ params, request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json({
      mailbox_id: String(params.mailboxId),
      display_name: typeof body.display_name === 'string' ? body.display_name : 'Test mailbox',
      owner_user_id: 'u_test',
      created_at: new Date().toISOString(),
    });
  }),
  http.delete(`${API_BASE}/mailboxes/:mailboxId`, () => HttpResponse.json({ status: 'deleted' })),

  // Accounts
  http.get(`${API_BASE}/mailboxes/:mailboxId/accounts`, () => HttpResponse.json([])),
  http.post(`${API_BASE}/mailboxes/:mailboxId/accounts`, ({ params }) =>
    HttpResponse.json({
      account_id: 'acc_test',
      mailbox_id: params.mailboxId,
      provider: 'gmail',
      display_label: 'Gmail',
      config: {},
      email_address: null,
      signature_html: null,
    }),
  ),
  http.get(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`, ({ params }) =>
    HttpResponse.json({
      account_id: params.accountId,
      mailbox_id: params.mailboxId,
      provider: 'gmail',
      display_label: 'Gmail',
      config: {},
      email_address: 'connected@example.com',
      signature_html: null,
    }),
  ),
  // Account update (rename, config, signature). Echoes ``signature_html`` /
  // ``display_label`` from the body so signature-save specs can assert the
  // captured request; ``signature_html`` defaults to null when the body
  // omits it (mirrors the backend always serialising the key).
  http.patch(
    `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`,
    async ({ params, request }) => {
      const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
      return HttpResponse.json({
        account_id: String(params.accountId),
        mailbox_id: String(params.mailboxId),
        provider: 'gmail',
        display_label: typeof body.display_label === 'string' ? body.display_label : 'Gmail',
        config:
          body.config && typeof body.config === 'object'
            ? (body.config as Record<string, unknown>)
            : {},
        email_address: 'connected@example.com',
        signature_html: typeof body.signature_html === 'string' ? body.signature_html : null,
      });
    },
  ),
  http.delete(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`, () =>
    HttpResponse.json({ status: 'deleted' }),
  ),
  // Interactive connect flow: returns the provider authorization URL the
  // browser must open; the OAuth callback completes the connection.
  http.post(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/connect`, ({ params }) =>
    HttpResponse.json({
      provider: 'gmail',
      account_id: params.accountId,
      account_label: `${params.mailboxId}__${params.accountId}`,
      authorization_url: 'https://accounts.google.com/o/oauth2/auth?mock=1',
      state: 'state-test',
    }),
  ),
  // Backfill status (background bulk-load progress). Happy-path: no active
  // backfill, so ``active: false`` stops the poll and no counter shows. Specs
  // override with ``server.use(...)`` to return a running job.
  http.get(`${API_BASE}/mailboxes/:mailboxId/backfill-status`, () =>
    HttpResponse.json({ accounts: [], active: false }),
  ),
  // Per-user account quota (user-level, no mailbox prefix). Happy-path: well
  // below the cap so ``atLimit`` is false and "Add account" stays enabled. Specs
  // override with ``server.use(...)`` to exercise the at-limit state.
  http.get(`${API_BASE}/accounts/quota`, () => HttpResponse.json({ connected: 0, limit: 15 })),

  // Recipient autocomplete (contacts) — user-level, no path params. Static
  // happy-path list; specs needing the empty / error case override inline.
  http.get(`${API_BASE}/contacts/suggestions`, () =>
    HttpResponse.json([
      { email: 'amparo@ejemplo.com', name: 'Amparo López' },
      { email: 'soporte@empresa.com', name: null },
    ]),
  ),

  // Emails
  http.get(`${API_BASE}/mailboxes/:mailboxId/emails`, () =>
    HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
  ),
  // Unread count badge. MSW v2 matches routes exactly, so this extra
  // ``/unread-count`` segment never collides with the listing handler above.
  // Happy-path zero counts; specs override with ``server.use(...)`` for figures.
  http.get(`${API_BASE}/mailboxes/:mailboxId/emails/unread-count`, ({ params, request }) => {
    const url = new URL(request.url);
    const box = url.searchParams.get('box') ?? 'ALL_MAIL';
    return HttpResponse.json({
      mailbox_id: String(params.mailboxId),
      box,
      total: 0,
      accounts: [],
    });
  }),
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
  http.post(`${API_BASE}/mailboxes/:mailboxId/emails/archive`, () =>
    HttpResponse.json({ moved_count: 0, accounts: [] }),
  ),
  http.post(`${API_BASE}/mailboxes/:mailboxId/emails/restore-from-archive`, () =>
    HttpResponse.json({ moved_count: 0, accounts: [] }),
  ),
  http.patch(`${API_BASE}/mailboxes/:mailboxId/emails/read-status`, () =>
    HttpResponse.json({ updated_count: 0, accounts: [] }),
  ),

  // Email content + attachment metadata
  http.get(`${API_BASE}/mailboxes/:mailboxId/emails/:pmid/content`, () =>
    HttpResponse.json({ html_body: null, text_body: null, attachments: [] }),
  ),

  // Conversation chain (conversation viewer). Happy-path empty thread;
  // specs override with ``server.use(...)`` to return a populated chain.
  http.get(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/emails/:pmid/conversation`, () =>
    HttpResponse.json({ thread_id: '', messages: [] }),
  ),
  // Received attachment binary download. Returns a binary stream with the
  // real name+extension on ``Content-Disposition`` (mirrors the backend
  // contract). Specs that exercise the "header missing" path override this
  // with ``server.use(...)`` to drop the header.
  http.get(
    `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/emails/:pmid/attachments/:attachmentId`,
    () =>
      new HttpResponse(new Blob(['binary'], { type: 'application/octet-stream' }), {
        headers: {
          'Content-Type': 'application/octet-stream',
          'Content-Disposition':
            'attachment; filename="attachment.bin"; filename*=UTF-8\'\'attachment.bin',
        },
      }),
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
  // Draft attachments (D-07 lazy push). Local-only writes on the backend;
  // happy-path shapes here so any spec that mounts the composer's attach /
  // remove flow does not hit an unhandled-request warning.
  http.post(
    `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/drafts/:draftId/attachments`,
    () =>
      HttpResponse.json({
        draft_attachment_id: '00000000-0000-0000-0000-000000000001',
        filename: 'attachment.bin',
        mime_type: 'application/octet-stream',
        size: 6,
        position: 0,
        provider_attachment_id: null,
      }),
  ),
  http.delete(
    `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/drafts/:draftId/attachments/:draftAttachmentId`,
    () => HttpResponse.json({ status: 'deleted' }),
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
  http.get(`${API_BASE}/virtual-mailboxes/:virtualMailboxId/emails`, () =>
    HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
  ),

  // Folders (user-level). Happy-path: empty list; create/update echo the body.
  http.get(`${API_BASE}/folders`, () => HttpResponse.json([])),
  http.post(`${API_BASE}/folders`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json({
      folder_id: 'folder_test',
      owner_user_id: 'u_test',
      name: typeof body.name === 'string' ? body.name : 'Test folder',
      color: typeof body.color === 'string' ? body.color : null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  }),
  http.get(`${API_BASE}/folders/:folderId`, ({ params }) =>
    HttpResponse.json({
      folder_id: String(params.folderId),
      owner_user_id: 'u_test',
      name: 'Test folder',
      color: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }),
  ),
  http.patch(`${API_BASE}/folders/:folderId`, async ({ params, request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json({
      folder_id: String(params.folderId),
      owner_user_id: 'u_test',
      name: typeof body.name === 'string' ? body.name : 'Test folder',
      color: typeof body.color === 'string' ? body.color : null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  }),
  http.delete(`${API_BASE}/folders/:folderId`, () => HttpResponse.json({ status: 'deleted' })),
  http.get(`${API_BASE}/folders/:folderId/emails`, () =>
    HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
  ),

  // Per-email folder assignment (under the email's mailbox). Happy-path returns
  // the email's folder list after the operation; specs override with a
  // populated list to assert chip repaints.
  http.post(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/emails/:pmid/folders`, () =>
    HttpResponse.json({ folders: [] }),
  ),
  http.delete(
    `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/emails/:pmid/folders/:folderId`,
    () => HttpResponse.json({ folders: [] }),
  ),

  // Rules (user-level). Happy-path: empty list; create/update echo the body.
  http.get(`${API_BASE}/rules`, () => HttpResponse.json([])),
  http.post(`${API_BASE}/rules`, async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json({
      rule_id: 'rule_test',
      owner_user_id: 'u_test',
      name: typeof body.name === 'string' ? body.name : null,
      is_enabled: typeof body.is_enabled === 'boolean' ? body.is_enabled : true,
      match_from_email: typeof body.match_from_email === 'string' ? body.match_from_email : null,
      match_subject_contains:
        typeof body.match_subject_contains === 'string' ? body.match_subject_contains : null,
      target_folder_id:
        typeof body.target_folder_id === 'string' ? body.target_folder_id : 'folder_test',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  }),
  http.get(`${API_BASE}/rules/:ruleId`, ({ params }) =>
    HttpResponse.json({
      rule_id: String(params.ruleId),
      owner_user_id: 'u_test',
      name: null,
      is_enabled: true,
      match_from_email: 'someone@example.com',
      match_subject_contains: null,
      target_folder_id: 'folder_test',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }),
  ),
  http.patch(`${API_BASE}/rules/:ruleId`, async ({ params, request }) => {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    return HttpResponse.json({
      rule_id: String(params.ruleId),
      owner_user_id: 'u_test',
      name: typeof body.name === 'string' ? body.name : null,
      is_enabled: typeof body.is_enabled === 'boolean' ? body.is_enabled : true,
      match_from_email: typeof body.match_from_email === 'string' ? body.match_from_email : null,
      match_subject_contains:
        typeof body.match_subject_contains === 'string' ? body.match_subject_contains : null,
      target_folder_id:
        typeof body.target_folder_id === 'string' ? body.target_folder_id : 'folder_test',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  }),
  http.delete(`${API_BASE}/rules/:ruleId`, () => HttpResponse.json({ status: 'deleted' })),
  // Enqueue "apply to existing": returns the initial running status.
  http.post(`${API_BASE}/rules/:ruleId/apply`, () =>
    HttpResponse.json({ status: 'pending', processed_count: 0, active: true }),
  ),
  // Apply-status poll. Happy-path: no job (never applied) so ``active:false``
  // stops the poll; specs override with a running/growing status.
  http.get(`${API_BASE}/rules/:ruleId/apply-status`, () =>
    HttpResponse.json({ status: 'none', processed_count: 0, active: false }),
  ),
];
