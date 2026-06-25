/**
 * Integration tests for ``ConversationMessageBody`` — the per-message archive
 * / unarchive buttons (the ONLY action surface inside a virtual mailbox's
 * conversation viewer).
 *
 * The body owns its own data-fetching hooks (content + downloader) and the
 * per-message mutations through ``useEmailBulkActions``. HTTP is intercepted at
 * MSW: the content GET (so the body mounts) and the archive / restore-from-
 * archive POSTs (so the click is observable as a real network call). Nothing
 * else is mocked — the real endpoint functions, schema validation and React
 * Query cache all run.
 *
 * Box-conditional contract pinned here: a message in ALL_MAIL offers
 * "Archivar"; a message in ARCHIVE offers "Desarchivar"; SENT/SPAM/TRASH offer
 * neither. The mutation must route by ``message.mailbox_id`` (groupByMailbox),
 * never the route — pinned by asserting the POST lands on the message's own
 * mailbox.
 */

import { render as rtlRender, screen, waitFor, type RenderOptions } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactElement } from 'react';
import { describe, expect, it } from 'vitest';

import ConversationMessageBody from './ConversationMessageBody';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { I18nProvider } from '../../../lib/i18n';
import { pinTestLang } from '../../../test/i18nTestLang';
import { server } from '../../../test/msw/server';
import type { EmailMetadataOut } from '../../../api/types/dto';

const API_BASE = 'http://localhost:8000';

pinTestLang('es');

function makeMessage(overrides: Partial<EmailMetadataOut> = {}): EmailMetadataOut {
  return {
    provider_message_id: 'm_1',
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: 't_1',
    from_email: 'bob@example.com',
    from_name: 'Bob',
    to_email: 'me@example.com',
    to_name: null,
    subject: 'Subject',
    received_at: new Date('2024-01-10T09:00:00Z').toISOString(),
    is_read: true,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: false,
    thread_message_count: 1,
    ...overrides,
  };
}

function render(ui: ReactElement, options?: Omit<RenderOptions, 'wrapper'>) {
  const client = createTestQueryClient();
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    );
  }
  return rtlRender(ui, { wrapper: Wrapper, ...options });
}

const ARCHIVE_LABEL = 'Archivar'; // conversation.archive
const UNARCHIVE_LABEL = 'Desarchivar'; // conversation.unarchive

describe('ConversationMessageBody — per-message archive / unarchive', () => {
  it('shows the Archive button for an ALL_MAIL message, not Unarchive', async () => {
    render(<ConversationMessageBody message={makeMessage({ box: 'ALL_MAIL' })} />);
    expect(await screen.findByRole('button', { name: ARCHIVE_LABEL })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: UNARCHIVE_LABEL })).not.toBeInTheDocument();
  });

  it('shows the Unarchive button for an ARCHIVE message, not Archive', async () => {
    render(<ConversationMessageBody message={makeMessage({ box: 'ARCHIVE' })} />);
    expect(await screen.findByRole('button', { name: UNARCHIVE_LABEL })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: ARCHIVE_LABEL })).not.toBeInTheDocument();
  });

  it.each<EmailMetadataOut['box']>(['SENT', 'SPAM', 'TRASH'])(
    'offers neither archive action for a %s message',
    async (box) => {
      render(<ConversationMessageBody message={makeMessage({ box })} />);
      // Wait for the body to settle. The spam button always renders; its
      // accessible name is its aria-label (conversation.markSpam), not the
      // visible "Spam" text.
      expect(await screen.findByRole('button', { name: 'Marcar como spam' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: ARCHIVE_LABEL })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: UNARCHIVE_LABEL })).not.toBeInTheDocument();
    },
  );

  it("archives via a POST to the MESSAGE's own mailbox, not the route", async () => {
    const seen: { mailbox?: string; body?: unknown } = {};
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/archive`, async ({ params, request }) => {
        seen.mailbox = String(params.mailboxId);
        seen.body = await request.json();
        return HttpResponse.json({ moved_count: 1, accounts: [{ account_id: 'a_9', moved: 1 }] });
      }),
    );

    // The message lives in a DIFFERENT real mailbox (mb_other) than any route —
    // groupByMailbox must route the call there.
    render(
      <ConversationMessageBody
        message={makeMessage({ box: 'ALL_MAIL', mailbox_id: 'mb_other', account_id: 'a_9' })}
      />,
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: ARCHIVE_LABEL }));

    await waitFor(() => expect(seen.mailbox).toBe('mb_other'));
    expect(seen.body).toEqual({
      items: [{ account_id: 'a_9', provider_message_id: 'm_1' }],
    });
  });

  it('unarchives via a POST to restore-from-archive', async () => {
    const seen: { mailbox?: string } = {};
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/restore-from-archive`, ({ params }) => {
        seen.mailbox = String(params.mailboxId);
        return HttpResponse.json({ moved_count: 1, accounts: [{ account_id: 'a_1', moved: 1 }] });
      }),
    );

    render(<ConversationMessageBody message={makeMessage({ box: 'ARCHIVE' })} />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: UNARCHIVE_LABEL }));

    await waitFor(() => expect(seen.mailbox).toBe('mb_1'));
  });
});
