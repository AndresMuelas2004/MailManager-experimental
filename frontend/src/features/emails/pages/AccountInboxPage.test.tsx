/**
 * Integration tests for AccountInboxPage. Two concerns are covered:
 *
 *  - the conversation path (thread-grouped listing + the conversation viewer
 *    opened from a row), and
 *  - the lupa ``in:`` operator column sync (the De/Para column follows the
 *    effective box derived from ``q``).
 *
 * MSW intercepts HTTP at the network boundary; the real hooks, endpoint
 * functions, schema validation and React Query cache run. We never mock
 * useConversation / updateReadStatus / endpoint functions.
 *
 * The page consumes the cross-cutting draft-composer context. We provide a
 * recording test double of that PROVIDER (not a hook mock) so the Reply
 * assertion can observe which message the composer was opened for — the same
 * way production wires the context, just with a capturing impl.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { pinTestLang } from '../../../test/i18nTestLang';
import { server } from '../../../test/msw/server';
import AccountInboxPage from './AccountInboxPage';
import {
  DraftComposerContext,
  type DraftComposerContextValue,
} from '../../../app/providers/DraftComposerContext';
import type { EmailMetadataOut } from '../../../api/types/dto';

const API_BASE = 'http://localhost:8000';

// The page renders backend-independent copy through ``t()``; pin Spanish so
// the fixed Spanish assertions in this file hold (default is English in jsdom).
pinTestLang('es');

// A sent email: the recipient (to_email) is the "other side" the SENT
// column must surface; from_email is the user's own account.
const sentEmail = {
  provider_message_id: 'm_sent',
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  thread_id: null,
  from_email: 'alice@example.com',
  from_name: 'Alice',
  to_email: 'recipient@example.com',
  to_name: 'Recipient',
  subject: 'A message I sent',
  received_at: new Date('2024-01-12T09:00:00Z').toISOString(),
  is_read: true,
  box: 'SENT',
  has_attachments: false,
  is_favorite: false,
};

// An inbound email for the no-operator inbox case: the sender (from_email)
// is the "other side" the received column must surface.
const inboxEmail = {
  provider_message_id: 'm_inbox',
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  thread_id: null,
  from_email: 'sender@example.com',
  from_name: 'Sender',
  to_email: 'alice@example.com',
  to_name: 'Alice',
  subject: 'A message I received',
  received_at: new Date('2024-01-12T09:00:00Z').toISOString(),
  is_read: true,
  box: 'ALL_MAIL',
  has_attachments: false,
  is_favorite: false,
};

const accountFixture = {
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  provider: 'gmail',
  display_label: 'Gmail',
  config: {},
  email_address: 'me@example.com',
  signature_html: null,
};

function makeMessage(id: string, overrides: Partial<EmailMetadataOut> = {}): EmailMetadataOut {
  return {
    provider_message_id: id,
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: 't_1',
    from_email: `${id}@example.com`,
    from_name: id,
    to_email: 'me@example.com',
    to_name: null,
    subject: `Subject ${id}`,
    received_at: '2024-01-10T09:00:00Z',
    is_read: true,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: false,
    thread_message_count: 1,
    folders: [],
    ...overrides,
  };
}

function noopComposer(
  overrides: Partial<DraftComposerContextValue> = {},
): DraftComposerContextValue {
  return {
    openForNewEmail: () => {},
    openForNewDraft: () => {},
    openForEditDraft: () => {},
    openForReply: async () => {},
    openForReplyAll: async () => {},
    openForForward: async () => {},
    setRefreshCallback: () => {},
    __register: () => {},
    ...overrides,
  };
}

function renderAccountInbox(
  composer: DraftComposerContextValue = noopComposer(),
  initialEntry = '/m/mb_1/account/a_1/inbox',
) {
  return renderWithProviders(
    <DraftComposerContext.Provider value={composer}>
      <Routes>
        <Route
          path="/m/:mailboxId/account/:accountId/inbox"
          element={<AccountInboxPage box="ALL_MAIL" />}
        />
      </Routes>
    </DraftComposerContext.Provider>,
    { initialEntries: [initialEntry] },
  );
}

describe('AccountInboxPage — conversation view', () => {
  it('requests group_by_thread=true on the listing', async () => {
    const seenGroup: (string | null)[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        seenGroup.push(new URL(request.url).searchParams.get('group_by_thread'));
        return HttpResponse.json({
          items: [makeMessage('rep', { subject: 'Thread row', thread_message_count: 2 })],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderAccountInbox();

    await waitFor(() => expect(screen.getByText('Thread row')).toBeInTheDocument());
    expect(seenGroup).toContain('true');
  });

  it('opening a thread renders the chain ascending with the most-recent expanded and marks unread read per mailbox', async () => {
    const readBodies: Array<{ mailbox: string; items: unknown }> = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          // The grouped listing representative IS the thread's most-recent
          // message (Gmail: its id matches the newest member); the row opens it.
          items: [
            makeMessage('m_new', {
              subject: 'Open thread',
              thread_message_count: 2,
              is_read: false,
              from_name: 'Newe',
              received_at: '2024-01-11T09:00:00Z',
            }),
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({
          thread_id: 't_1',
          messages: [
            makeMessage('m_old', {
              from_name: 'Olde',
              is_read: false,
              received_at: '2024-01-10T09:00:00Z',
            }),
            makeMessage('m_new', {
              from_name: 'Newe',
              is_read: false,
              received_at: '2024-01-11T09:00:00Z',
            }),
          ],
        }),
      ),
      http.patch(
        `${API_BASE}/mailboxes/:mailboxId/emails/read-status`,
        async ({ params, request }) => {
          const body = (await request.json()) as { items: unknown };
          readBodies.push({ mailbox: String(params.mailboxId), items: body.items });
          return HttpResponse.json({ updated_count: 2, accounts: [] });
        },
      ),
    );

    renderAccountInbox();

    await waitFor(() => expect(screen.getByText('Open thread')).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByText('Open thread'));

    // Both message headers render (From label). The opened email (the newest,
    // m_new) is merged with the chain; its same-id conversation twin is deduped.
    await waitFor(() => {
      expect(screen.getByText(/Olde/)).toBeInTheDocument();
      expect(screen.getByText(/Newe/)).toBeInTheDocument();
    });

    // Ascending order: the older card precedes the newer (opened) one in the DOM.
    const olde = screen.getByText(/Olde/);
    const newe = screen.getByText(/Newe/);
    expect(olde.compareDocumentPosition(newe) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // The whole thread's unread messages are marked read on open — one PATCH
    // for the single mailbox. The opened row (m_new) leads, then the older
    // member; the opened row's conversation twin is deduped by id.
    await waitFor(() => expect(readBodies).toHaveLength(1));
    expect(readBodies[0].mailbox).toBe('mb_1');
    expect(readBodies[0].items).toEqual([
      { account_id: 'a_1', provider_message_id: 'm_new' },
      { account_id: 'a_1', provider_message_id: 'm_old' },
    ]);
  });

  it('marks the opened listing row itself and sends propagate_thread (Outlook duplicate-id fix)', async () => {
    // Outlook persists the SAME message under different REST ids in the sync
    // (listing) vs the conversation fetch, so marking only the conversation
    // members leaves the listing row (a separate DB row) unread. The viewer
    // must include the opened row's OWN id and send propagate_thread=true so
    // the backend flips every row of the thread.
    const readBodies: Array<{
      items: Array<{ provider_message_id: string }>;
      propagateThread: unknown;
    }> = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: [
            makeMessage('listing_rep', {
              subject: 'Unread thread',
              thread_message_count: 2,
              is_read: false,
            }),
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({
          thread_id: 't_1',
          // The conversation returns a DIFFERENT id than the listing row.
          messages: [makeMessage('conv_id', { from_name: 'Convo', is_read: false })],
        }),
      ),
      http.patch(`${API_BASE}/mailboxes/:mailboxId/emails/read-status`, async ({ request }) => {
        const body = (await request.json()) as {
          items: Array<{ provider_message_id: string }>;
          propagate_thread?: boolean;
        };
        readBodies.push({ items: body.items, propagateThread: body.propagate_thread });
        return HttpResponse.json({ updated_count: 2, accounts: [] });
      }),
    );

    renderAccountInbox();
    await waitFor(() => expect(screen.getByText('Unread thread')).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByText('Unread thread'));

    await waitFor(() => expect(readBodies).toHaveLength(1));
    expect(readBodies[0].propagateThread).toBe(true);
    const ids = readBodies[0].items.map((i) => i.provider_message_id).sort();
    expect(ids).toEqual(['conv_id', 'listing_rep']);
  });

  it('lazily loads bodies: the collapsed card does not request its content until expanded', async () => {
    const contentCalls: string[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          // Opened row = the newest thread member (m_new), expanded by default.
          items: [
            makeMessage('m_new', {
              subject: 'Lazy thread',
              thread_message_count: 2,
              from_name: 'Newe',
              received_at: '2024-01-11T09:00:00Z',
            }),
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({
          thread_id: 't_1',
          messages: [
            makeMessage('m_old', { from_name: 'Olde', received_at: '2024-01-10T09:00:00Z' }),
            makeMessage('m_new', { from_name: 'Newe', received_at: '2024-01-11T09:00:00Z' }),
          ],
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/emails/:pmid/content`, ({ params }) => {
        contentCalls.push(String(params.pmid));
        return HttpResponse.json({ html_body: '<p>body</p>', text_body: null, attachments: [] });
      }),
    );

    renderAccountInbox();
    await waitFor(() => expect(screen.getByText('Lazy thread')).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByText('Lazy thread'));

    // Only the opened (most-recent) message's body is fetched on open, under its
    // own listing id; the older (collapsed) card's content is NOT requested yet.
    await waitFor(() => expect(contentCalls).toContain('m_new'));
    expect(contentCalls).not.toContain('m_old');

    // Expanding the older card now triggers its lazy body fetch.
    await user.click(screen.getByText(/Olde/));
    await waitFor(() => expect(contentCalls).toContain('m_old'));
  });

  it('replies with the most-recent message of the thread', async () => {
    const replyTargets: EmailMetadataOut[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          // Opened row = the newest thread member (m_new).
          items: [
            makeMessage('m_new', {
              subject: 'Reply thread',
              thread_message_count: 2,
              received_at: '2024-01-11T09:00:00Z',
            }),
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({
          thread_id: 't_1',
          messages: [
            makeMessage('m_old', { received_at: '2024-01-10T09:00:00Z' }),
            makeMessage('m_new', { received_at: '2024-01-11T09:00:00Z' }),
          ],
        }),
      ),
    );

    const composer = noopComposer({
      openForReply: async (email) => {
        replyTargets.push(email);
      },
    });
    renderAccountInbox(composer);

    await waitFor(() => expect(screen.getByText('Reply thread')).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByText('Reply thread'));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Responder' })).toBeInTheDocument(),
    );
    await user.click(screen.getByRole('button', { name: 'Responder' }));

    expect(replyTargets).toHaveLength(1);
    // The reply targets the LAST (most-recent) message of the loaded chain.
    expect(replyTargets[0].provider_message_id).toBe('m_new');
  });

  it('tolerates a per-message 404 on content without breaking the rest of the viewer', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          // Opened row = the newest thread member (m_new), auto-expanded.
          items: [
            makeMessage('m_new', {
              subject: 'Degraded thread',
              thread_message_count: 2,
              from_name: 'Newe',
              received_at: '2024-01-11T09:00:00Z',
            }),
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({
          thread_id: 't_1',
          messages: [
            makeMessage('m_old', { from_name: 'Olde', received_at: '2024-01-10T09:00:00Z' }),
            makeMessage('m_new', { from_name: 'Newe', received_at: '2024-01-11T09:00:00Z' }),
          ],
        }),
      ),
      // The opened (most-recent) message, auto-expanded, fails its content
      // fetch; the older one would succeed when expanded.
      http.get(`${API_BASE}/mailboxes/mb_1/emails/m_new/content`, () =>
        HttpResponse.json(
          { error: { code: 'email_not_found', message: 'No encontrado' } },
          { status: 404 },
        ),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/emails/:pmid/content`, () =>
        HttpResponse.json({ html_body: '<p>ok</p>', text_body: null, attachments: [] }),
      ),
    );

    renderAccountInbox();
    await waitFor(() => expect(screen.getByText('Degraded thread')).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByText('Degraded thread'));

    // The failing card surfaces its own error inside the viewer; the other
    // card's header stays visible (the modal is not torn down).
    await waitFor(() => expect(screen.getByText('No encontrado')).toBeInTheDocument());
    expect(screen.getByText(/Olde/)).toBeInTheDocument();
  });
});

describe('AccountInboxPage — cross-mailbox per-message content (virtual-style trap is not specific to vmbox)', () => {
  it('fetches per-message content with the message own mailbox_id', async () => {
    const contentMailboxes: string[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          // The opened row's REAL mailbox is mb_2 (a cross-mailbox representative,
          // the trap a virtual bandeja surfaces). Its per-message content must be
          // fetched against mb_2 (EmailMetadataOut.mailbox_id), not the route mb_1.
          items: [
            makeMessage('m_x', {
              mailbox_id: 'mb_2',
              subject: 'Mixed thread',
              thread_message_count: 1,
              from_name: 'Cross',
            }),
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.get(`${API_BASE}/mailboxes/:mailboxId/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({
          thread_id: 't_1',
          messages: [makeMessage('m_x', { mailbox_id: 'mb_2', from_name: 'Cross' })],
        }),
      ),
      http.get(`${API_BASE}/mailboxes/:mailboxId/emails/:pmid/content`, ({ params }) => {
        contentMailboxes.push(String(params.mailboxId));
        return HttpResponse.json({ html_body: '<p>x</p>', text_body: null, attachments: [] });
      }),
    );

    renderAccountInbox();
    await waitFor(() => expect(screen.getByText('Mixed thread')).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByText('Mixed thread'));

    // The opened email's body is fetched against ITS OWN mailbox (mb_2), never
    // the route's mb_1.
    await waitFor(() => expect(contentMailboxes).toContain('mb_2'));
    expect(contentMailboxes).not.toContain('mb_1');
  });
});

// Lupa ``in:`` operator column sync: the De/Para column of the individual
// view follows the EFFECTIVE box derived from ``q`` (the row may be a thread
// row in conversation mode, but the column-sense logic is independent of
// grouping). The backend applies the real box override from ``q``; the
// frontend only mirrors it cosmetically.
describe('AccountInboxPage — in: column sync', () => {
  it('shows the recipient under "Para" (not "De") when q carries in:sent', async () => {
    const seenQueries: (string | null)[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        seenQueries.push(new URL(request.url).searchParams.get('q'));
        return HttpResponse.json({ items: [sentEmail], total: 1, limit: 50, offset: 0 });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderWithProviders(
      <Routes>
        <Route
          path="/m/:mailboxId/account/:accountId/sent"
          element={<AccountInboxPage box="SENT" />}
        />
      </Routes>,
      { initialEntries: ['/m/mb_1/account/a_1/sent?q=in:sent'] },
    );

    await waitFor(() => {
      expect(screen.getByText('A message I sent')).toBeInTheDocument();
    });

    // Individual view shows a single side column: with the effective box
    // being SENT it must be "Para" with the recipient, not "De".
    expect(screen.getByText('Para')).toBeInTheDocument();
    expect(screen.queryByText('De')).not.toBeInTheDocument();
    expect(screen.getByText('recipient@example.com')).toBeInTheDocument();

    // q must travel literally — the frontend never rewrites operators.
    expect(seenQueries[seenQueries.length - 1]).toBe('in:sent');
  });

  it('shows the sender under "De" in the inbox with no in: operator', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [inboxEmail], total: 1, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderWithProviders(
      <Routes>
        <Route
          path="/m/:mailboxId/account/:accountId/inbox"
          element={<AccountInboxPage box="ALL_MAIL" />}
        />
      </Routes>,
      { initialEntries: ['/m/mb_1/account/a_1/inbox'] },
    );

    await waitFor(() => {
      expect(screen.getByText('A message I received')).toBeInTheDocument();
    });

    // Received individual view shows the "De" column with the real sender.
    expect(screen.getByText('De')).toBeInTheDocument();
    expect(screen.queryByText('Para')).not.toBeInTheDocument();
    expect(screen.getByText('sender@example.com')).toBeInTheDocument();
  });
});

// Selection regression guard. Commit #6 (conversation view) silently dropped
// the checkbox / bulk-bar wiring from this page, leaving inert placeholder
// boxes that looked like checkboxes but did nothing. These tests pin the
// header "select all" and the per-row selection back in place: clicking a
// checkbox must toggle real selection and reveal the bulk-actions bar. While
// they pass, the wiring cannot disappear unnoticed again.
describe('AccountInboxPage — selection', () => {
  function stubTwoThreads() {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: [
            makeMessage('rep1', { subject: 'First thread' }),
            makeMessage('rep2', { subject: 'Second thread' }),
          ],
          total: 2,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );
  }

  it('the header checkbox selects every visible row and reveals the bulk bar', async () => {
    stubTwoThreads();
    renderAccountInbox();
    await waitFor(() => expect(screen.getByText('First thread')).toBeInTheDocument());

    // Nothing selected yet → the bulk bar is absent.
    expect(screen.queryByRole('button', { name: 'Limpiar selección' })).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(
      screen.getByRole('checkbox', { name: 'Seleccionar los 50 correos más recientes' }),
    );

    // The bulk bar appears and every row checkbox is checked.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Limpiar selección' })).toBeInTheDocument(),
    );
    const rowChecks = screen.getAllByRole('checkbox', { name: 'Seleccionar correo' });
    expect(rowChecks).toHaveLength(2);
    rowChecks.forEach((cb) => expect(cb).toBeChecked());
  });

  it('a row checkbox selects only that row without opening the conversation viewer', async () => {
    const conversationCalls: string[] = [];
    stubTwoThreads();
    server.use(
      http.get(
        `${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`,
        ({ params }) => {
          conversationCalls.push(String(params.pmid));
          return HttpResponse.json({ thread_id: 't_1', messages: [makeMessage('rep1')] });
        },
      ),
    );

    renderAccountInbox();
    await waitFor(() => expect(screen.getByText('First thread')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getAllByRole('checkbox', { name: 'Seleccionar correo' })[0]);

    // Only the clicked row is selected and the bulk bar shows up.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Limpiar selección' })).toBeInTheDocument(),
    );
    const rowChecks = screen.getAllByRole('checkbox', { name: 'Seleccionar correo' });
    expect(rowChecks[0]).toBeChecked();
    expect(rowChecks[1]).not.toBeChecked();

    // The checkbox stops click propagation, so the row's open handler — and
    // therefore the conversation fetch — never fires.
    expect(conversationCalls).toHaveLength(0);
  });
});

// Refresh control. The account header mounts a RefreshControl; its button is
// addressed by the accessible name 'Buscar correo nuevo' (aria-label). The
// per-account scope means the sync-metadata POST carries account_id=a_1. The
// page auto-syncs on mount, so the click is asserted as a DELTA.
describe('AccountInboxPage — refresh control', () => {
  beforeEach(() => {
    window.localStorage.removeItem('lastSync:emails:mb_1:a_1');
  });
  afterEach(() => {
    window.localStorage.removeItem('lastSync:emails:mb_1:a_1');
  });

  function stubInbox(seenSyncs: string[]) {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: [makeMessage('rep', { subject: 'Account thread' })],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, ({ params, request }) => {
        const accountId = new URL(request.url).searchParams.get('account_id');
        seenSyncs.push(`${String(params.mailboxId)}/${accountId}`);
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
    );
  }

  it('mounts the refresh button addressable by its accessible name', async () => {
    stubInbox([]);
    renderAccountInbox();

    await waitFor(() => expect(screen.getByText('Account thread')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Buscar correo nuevo' })).toBeInTheDocument();
  });

  it('clicking the refresh button fires an extra sync-metadata POST scoped to the account', async () => {
    const seenSyncs: string[] = [];
    stubInbox(seenSyncs);
    renderAccountInbox();

    await waitFor(() => expect(screen.getByText('Account thread')).toBeInTheDocument());
    // Wait for the mount auto-sync (carrying account_id=a_1) to land.
    await waitFor(() => expect(seenSyncs.length).toBeGreaterThanOrEqual(1));
    const before = seenSyncs.length;

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Buscar correo nuevo' }));

    await waitFor(() => expect(seenSyncs.length).toBe(before + 1));
    // The per-account scope flows into the sync as account_id=a_1.
    expect(seenSyncs[seenSyncs.length - 1]).toBe('mb_1/a_1');
  });
});

// Sort + quick-filter controls. The account header mounts ListControls; the
// page reads/writes the control state to the URL and feeds it to useEmailList.
// We assert what travels on the wire (the real endpoint runs through MSW), the
// page-reset side effect, and the filtered-empty message.
describe('AccountInboxPage — sort + quick-filter controls', () => {
  function stubInbox(onListRequest?: (url: URL) => void) {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        if (onListRequest) onListRequest(new URL(request.url));
        return HttpResponse.json({
          items: [makeMessage('rep', { subject: 'Controls thread' })],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );
  }

  it('changing the sort to Asunto sends sort=subject on the next request', async () => {
    const seenSorts: (string | null)[] = [];
    stubInbox((url) => seenSorts.push(url.searchParams.get('sort')));
    renderAccountInbox();
    await waitFor(() => expect(screen.getByText('Controls thread')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole('combobox'), 'subject');

    await waitFor(() => expect(seenSorts).toContain('subject'));
    // The default-sorted first load must NOT have sent the param.
    expect(seenSorts[0]).toBeNull();
  });

  it('toggling the direction sends sort_dir=asc', async () => {
    const seenDirs: (string | null)[] = [];
    stubInbox((url) => seenDirs.push(url.searchParams.get('sort_dir')));
    renderAccountInbox();
    await waitFor(() => expect(screen.getByText('Controls thread')).toBeInTheDocument());

    const user = userEvent.setup();
    // Default is desc → the button is labelled "Descendente".
    await user.click(screen.getByRole('button', { name: 'Descendente' }));

    await waitFor(() => expect(seenDirs).toContain('asc'));
  });

  it('pressing a chip adds its wire param and resets page to 1 (offset back to 0)', async () => {
    const seen: Array<{ unread: string | null; offset: string | null }> = [];
    stubInbox((url) =>
      seen.push({
        unread: url.searchParams.get('unread'),
        offset: url.searchParams.get('offset'),
      }),
    );
    // Start on page 3 so the reset to page 1 is observable on the wire.
    renderAccountInbox(noopComposer(), '/m/mb_1/account/a_1/inbox?page=3');
    await waitFor(() => expect(screen.getByText('Controls thread')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'No leídos' }));

    // After the chip press the request carries unread=true AND offset=0 (the
    // control change dropped ``page`` from the URL → first page).
    await waitFor(() =>
      expect(seen.some((s) => s.unread === 'true' && s.offset === '0')).toBe(true),
    );
  });

  it('multiple chips travel together in one request', async () => {
    const seen: Array<Record<string, string | null>> = [];
    stubInbox((url) =>
      seen.push({
        unread: url.searchParams.get('unread'),
        has_attachment: url.searchParams.get('has_attachment'),
      }),
    );
    renderAccountInbox();
    await waitFor(() => expect(screen.getByText('Controls thread')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'No leídos' }));
    await user.click(screen.getByRole('button', { name: 'Con adjuntos' }));

    await waitFor(() =>
      expect(seen.some((s) => s.unread === 'true' && s.has_attachment === 'true')).toBe(true),
    );
  });

  it('shows the filtered-empty message (not the default one) when a filter yields nothing', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );
    // A chip is active from the URL → the empty state must read "filtered".
    renderAccountInbox(noopComposer(), '/m/mb_1/account/a_1/inbox?unread=1');

    await waitFor(() =>
      expect(screen.getByText('No hay correos que coincidan con los filtros')).toBeInTheDocument(),
    );
    expect(screen.queryByText('No hay correos en esta bandeja')).not.toBeInTheDocument();
  });
});
