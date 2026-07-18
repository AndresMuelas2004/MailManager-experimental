/**
 * Integration tests for ``ConversationViewerMount`` (+ its presentational
 * ``ConversationViewer``) with MSW at the network boundary. The real
 * ``useConversation`` / ``useEmailContent`` hooks, endpoint functions, schema
 * validation and React Query cache all run; only ``/content`` and
 * ``/conversation`` responses are synthesized.
 *
 * Pins Q2=A: the opened email's body paints IMMEDIATELY from the (fast)
 * ``/content`` read without waiting for the live ``/conversation`` call, the
 * chain fills in below when it arrives, the opened email's conversation twin is
 * deduped (primary: exact id — the backend reconciles Outlook conversation ids
 * onto the listing ids; fallback: the physical identity instant+from+subject
 * mirroring the backend's reconciliation key — its body is read under the
 * LISTING id, a cache hit), a chain-fetch error is non-blocking AND still
 * marks the opened (already read on screen) email as read, and Reply targets
 * the opened email before the chain loads and the newest after.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import ConversationViewerMount from './ConversationViewerMount';
import { renderWithProviders } from '../../../test/renderWithProviders';
import { pinTestLang } from '../../../test/i18nTestLang';
import { server } from '../../../test/msw/server';
import type { EmailMetadataOut } from '../../../api/types/dto';

const API_BASE = 'http://localhost:8000';
const CONTENT_PATH = `${API_BASE}/mailboxes/:mailboxId/emails/:pmid/content`;
const CONVERSATION_PATH = `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/emails/:pmid/conversation`;

const IFRAME_TITLE = 'Contenido del correo';
const THREAD_LOADING = 'Cargando la conversación…';
const THREAD_ERROR = 'No se pudo cargar el resto de la conversación.';

pinTestLang('es');

function makeEmail(id: string, overrides: Partial<EmailMetadataOut> = {}): EmailMetadataOut {
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
    received_at: '2024-01-01T00:00:00Z',
    is_read: true,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: false,
    thread_message_count: 1,
    folders: [],
    ...overrides,
  };
}

const NOOP = () => {};

function contentHandler(html: string | null = '<p>BODY</p>', onRequest?: (pmid: string) => void) {
  return http.get(CONTENT_PATH, ({ params }) => {
    onRequest?.(String(params.pmid));
    return HttpResponse.json({ html_body: html, text_body: null, attachments: [] });
  });
}

function mountViewer(
  openedEmail: EmailMetadataOut,
  handlers: { onReply?: (e: EmailMetadataOut) => void } = {},
) {
  return renderWithProviders(
    <ConversationViewerMount
      openedEmail={openedEmail}
      onClose={NOOP}
      onReply={handlers.onReply ?? NOOP}
      onReplyAll={NOOP}
      onForward={NOOP}
    />,
  );
}

describe('ConversationViewerMount — immediate opened body (Q2=A)', () => {
  it('paints the opened email body immediately, before /conversation resolves', async () => {
    let releaseConversation: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      releaseConversation = resolve;
    });
    server.use(
      contentHandler('<p>OPENED-BODY</p>'),
      http.get(CONVERSATION_PATH, async () => {
        await gate;
        return HttpResponse.json({ thread_id: 't_1', messages: [makeEmail('m_opened')] });
      }),
    );

    mountViewer(makeEmail('m_opened'));

    // The opened body iframe paints from /content, without waiting for the live
    // /conversation call (still gated open).
    const iframe = await screen.findByTitle(IFRAME_TITLE);
    expect(iframe.getAttribute('srcdoc')).toContain('OPENED-BODY');
    // The chain is still loading: a discreet non-blocking footer shows — NOT a
    // full-screen spinner replacing the body.
    expect(screen.getByText(THREAD_LOADING)).toBeInTheDocument();

    releaseConversation();
    await waitFor(() => expect(screen.queryByText(THREAD_LOADING)).not.toBeInTheDocument());
  });

  it('fills in the rest of the chain and keeps the opened email expanded', async () => {
    server.use(
      contentHandler('<p>BODY</p>'),
      http.get(CONVERSATION_PATH, () =>
        HttpResponse.json({
          thread_id: 't_1',
          messages: [
            makeEmail('m_opened', { received_at: '2024-01-02T00:00:00Z', from_name: 'Opened' }),
            makeEmail('m_older', { received_at: '2024-01-01T00:00:00Z', from_name: 'Older' }),
          ],
        }),
      ),
    );

    mountViewer(
      makeEmail('m_opened', { received_at: '2024-01-02T00:00:00Z', from_name: 'Opened' }),
    );

    // The opened body paints, then the older message's card fills in below.
    await screen.findByTitle(IFRAME_TITLE);
    expect(await screen.findByText('Older <m_older@example.com>')).toBeInTheDocument();
    // Only the opened email is expanded (a single body iframe)...
    expect(screen.getAllByTitle(IFRAME_TITLE)).toHaveLength(1);
    // ...and it is not duplicated by its same-id conversation twin.
    expect(screen.getAllByText('Opened <m_opened@example.com>')).toHaveLength(1);
  });
});

describe('ConversationViewerMount — dedup of the opened email against the chain', () => {
  it('dedups the opened email when the chain returns it under the same id (Gmail)', async () => {
    server.use(
      contentHandler('<p>BODY</p>'),
      http.get(CONVERSATION_PATH, () =>
        HttpResponse.json({
          thread_id: 't_1',
          messages: [
            makeEmail('m_opened'),
            makeEmail('m_2', { received_at: '2024-03-01T00:00:00Z', from_name: 'Two' }),
          ],
        }),
      ),
    );

    mountViewer(makeEmail('m_opened'));

    await screen.findByText('Two <m_2@example.com>'); // chain loaded
    // The opened message shows exactly once — its same-id twin was deduped.
    expect(screen.getAllByText('m_opened <m_opened@example.com>')).toHaveLength(1);
  });

  it('dedups the Outlook twin (different id, same physical identity) and reads the body with the listing id', async () => {
    // Covers the DEGRADED backend path: normally the backend reconciles the
    // conversation id onto the listing id (same-id dedup), but when that
    // best-effort reconciliation fails the twin arrives verbatim and the
    // physical-identity fallback (same instant + from_email + subject —
    // mirroring the backend's reconciliation key, so same physical message ⇒
    // same subject) must still dedupe it.
    const contentRequests: string[] = [];
    server.use(
      contentHandler('<p>BODY</p>', (pmid) => contentRequests.push(pmid)),
      http.get(CONVERSATION_PATH, () =>
        HttpResponse.json({
          thread_id: 't_1',
          messages: [
            // Same physical message under a DIFFERENT REST id (Outlook), same
            // received_at + from_email + subject as the listing row.
            makeEmail('conv_id_X', {
              received_at: '2024-05-01T10:00:00Z',
              from_email: 'boss@corp.com',
              from_name: 'Boss',
              subject: 'Quarterly numbers',
            }),
          ],
        }),
      ),
    );

    mountViewer(
      makeEmail('row_id_A', {
        received_at: '2024-05-01T10:00:00Z',
        from_email: 'boss@corp.com',
        from_name: 'Boss',
        subject: 'Quarterly numbers',
      }),
    );

    const iframe = await screen.findByTitle(IFRAME_TITLE);
    expect(iframe.getAttribute('srcdoc')).toContain('BODY');
    // Exactly one card — the twin is deduped by received_at + from_email.
    await waitFor(() => expect(screen.getAllByText('Boss <boss@corp.com>')).toHaveLength(1));
    // The body was requested under the LISTING id (cache hit), never the
    // non-deterministic conversation id.
    await waitFor(() => expect(contentRequests).toContain('row_id_A'));
    expect(contentRequests).not.toContain('conv_id_X');
  });
});

describe('ConversationViewerMount — chain error resilience + reply target', () => {
  it('still shows the opened email body when /conversation fails', async () => {
    server.use(
      contentHandler('<p>RESILIENT-BODY</p>'),
      http.get(CONVERSATION_PATH, () =>
        HttpResponse.json(
          { error: { code: 'external_api_error', message: 'boom' } },
          { status: 502 },
        ),
      ),
    );

    mountViewer(makeEmail('m_opened'));

    // The opened email body still paints despite the chain error.
    const iframe = await screen.findByTitle(IFRAME_TITLE);
    expect(iframe.getAttribute('srcdoc')).toContain('RESILIENT-BODY');
    // A non-blocking notice is shown instead of replacing the whole viewer.
    expect(await screen.findByText(THREAD_ERROR)).toBeInTheDocument();
  });

  it('marks the opened email read even when /conversation fails (the warmed body was read)', async () => {
    // The instant open paints the body from the warmed cache without waiting
    // for /conversation, so the user HAS read the email even when the chain
    // fetch fails. The read-status call must still fire — with the listing id
    // and propagate_thread — or the thread stays bold forever after a
    // transient provider failure.
    const readBodies: Array<{
      items: Array<{ provider_message_id: string }>;
      propagate_thread?: boolean;
    }> = [];
    server.use(
      contentHandler('<p>BODY</p>'),
      http.get(CONVERSATION_PATH, () =>
        HttpResponse.json(
          { error: { code: 'external_api_error', message: 'boom' } },
          { status: 502 },
        ),
      ),
      http.patch(`${API_BASE}/mailboxes/:mailboxId/emails/read-status`, async ({ request }) => {
        readBodies.push(
          (await request.json()) as {
            items: Array<{ provider_message_id: string }>;
            propagate_thread?: boolean;
          },
        );
        return HttpResponse.json({ updated_count: 1, accounts: [] });
      }),
    );

    mountViewer(makeEmail('m_opened', { is_read: false }));

    await screen.findByText(THREAD_ERROR);
    await waitFor(() => expect(readBodies).toHaveLength(1));
    expect(readBodies[0].items.map((i) => i.provider_message_id)).toEqual(['m_opened']);
    expect(readBodies[0].propagate_thread).toBe(true);
  });

  it('reply targets the opened email before the chain loads and the newest after', async () => {
    let releaseConversation: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      releaseConversation = resolve;
    });
    const replied: string[] = [];
    server.use(
      contentHandler('<p>BODY</p>'),
      http.get(CONVERSATION_PATH, async () => {
        await gate;
        return HttpResponse.json({
          thread_id: 't_1',
          messages: [
            makeEmail('m_old', { received_at: '2024-01-01T00:00:00Z' }),
            makeEmail('m_new', { received_at: '2024-06-01T00:00:00Z', from_name: 'Newest' }),
          ],
        });
      }),
    );

    mountViewer(makeEmail('m_old', { received_at: '2024-01-01T00:00:00Z' }), {
      onReply: (e) => replied.push(e.provider_message_id),
    });

    const user = userEvent.setup();
    // Before the chain loads the only message is the opened one → reply targets it.
    await user.click(await screen.findByRole('button', { name: 'Responder' }));
    expect(replied).toEqual(['m_old']);

    // Once the chain arrives, the newest message becomes the reply target.
    releaseConversation();
    await screen.findByText('Newest <m_new@example.com>');
    await user.click(screen.getByRole('button', { name: 'Responder' }));
    expect(replied).toEqual(['m_old', 'm_new']);
  });
});
