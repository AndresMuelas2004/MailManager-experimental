/**
 * Integration tests for the conversation path of AccountInboxPage.
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
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';
import AccountInboxPage from './AccountInboxPage';
import {
  DraftComposerContext,
  type DraftComposerContextValue,
} from '../../../app/providers/DraftComposerContext';
import type { EmailMetadataOut } from '../../../api/types/dto';

const API_BASE = 'http://localhost:8000';

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
