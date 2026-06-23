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
          items: [makeMessage('rep', { subject: 'Open thread', thread_message_count: 2 })],
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
            makeMessage('m_old', { from_name: 'Olde', is_read: false }),
            makeMessage('m_new', { from_name: 'Newe', is_read: false }),
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

    // Both message headers render (From label).
    await waitFor(() => {
      expect(screen.getByText(/Olde/)).toBeInTheDocument();
      expect(screen.getByText(/Newe/)).toBeInTheDocument();
    });

    // Ascending order: the older card precedes the newer one in the DOM.
    const olde = screen.getByText(/Olde/);
    const newe = screen.getByText(/Newe/);
    expect(olde.compareDocumentPosition(newe) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // The whole thread's unread messages are marked read on open — one PATCH
    // for the single mailbox, carrying both message refs.
    await waitFor(() => expect(readBodies).toHaveLength(1));
    expect(readBodies[0].mailbox).toBe('mb_1');
    expect(readBodies[0].items).toEqual([
      { account_id: 'a_1', provider_message_id: 'm_old' },
      { account_id: 'a_1', provider_message_id: 'm_new' },
    ]);
  });

  it('lazily loads bodies: the collapsed card does not request its content until expanded', async () => {
    const contentCalls: string[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: [makeMessage('rep', { subject: 'Lazy thread', thread_message_count: 2 })],
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
            makeMessage('m_old', { from_name: 'Olde' }),
            makeMessage('m_new', { from_name: 'Newe' }),
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

    // Only the most-recent message's body is fetched on open; the older
    // (collapsed) card's content is NOT requested yet.
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
          items: [makeMessage('rep', { subject: 'Reply thread', thread_message_count: 2 })],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({
          thread_id: 't_1',
          messages: [makeMessage('m_old'), makeMessage('m_new')],
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
          items: [makeMessage('rep', { subject: 'Degraded thread', thread_message_count: 2 })],
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
            makeMessage('m_old', { from_name: 'Olde' }),
            makeMessage('m_new', { from_name: 'Newe' }),
          ],
        }),
      ),
      // The most-recent message (auto-expanded) fails its content fetch; the
      // older one would succeed when expanded.
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
          items: [makeMessage('rep', { subject: 'Mixed thread', thread_message_count: 1 })],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({
          thread_id: 't_1',
          // The most-recent message lives in a DIFFERENT real mailbox (mb_2),
          // as happens in a cross-mailbox thread. Its content must be fetched
          // against mb_2, not the route's mb_1.
          messages: [makeMessage('m_new', { mailbox_id: 'mb_2', from_name: 'Newe' })],
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

// Unread badge on the account tabs. The page feeds AccountTabs the per-account
// counts from useAccountUnreadCounts, which extracts this account's entry from
// the mailbox-wide breakdown returned by /emails/unread-count.
describe('AccountInboxPage — account tab unread badge', () => {
  it('shows the inbox tab badge with this account unread total', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [makeMessage('m1')], total: 1, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.get(`${API_BASE}/mailboxes/mb_1/emails/unread-count`, ({ request }) => {
        const box = new URL(request.url).searchParams.get('box') ?? 'ALL_MAIL';
        // 3 unread in ALL_MAIL for this account, none in SPAM.
        const total = box === 'SPAM' ? 0 : 3;
        return HttpResponse.json({
          mailbox_id: 'mb_1',
          box,
          total,
          accounts: [{ account_id: 'a_1', unread: total }],
        });
      }),
    );

    renderAccountInbox();

    // The inbox tab carries the badge "3 sin leer"; spam (0) shows none.
    const badge = await screen.findByLabelText('3 sin leer');
    expect(badge).toHaveTextContent('3');
    expect(screen.queryByLabelText('0 sin leer')).not.toBeInTheDocument();
    // The badge belongs to the inbox tab (the link to this account's inbox).
    expect(badge.closest('a')).toHaveAttribute('href', '/m/mb_1/account/a_1/inbox');
  });
});
