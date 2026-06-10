/**
 * Integration tests for ``useConversation`` (MSW at the network boundary).
 *
 * The real endpoint function, schema validation and React Query cache all
 * run; only the HTTP response is synthesized. We assert:
 *  - the ConversationOut → { messages, threadId } flat mapping;
 *  - the request goes to .../emails/:pmid/conversation with the pmid
 *    URL-ENCODED (Outlook ImmutableId can contain '/');
 *  - a successful fetch invalidates ['emails'] (observed by behaviour: a
 *    co-mounted useEmailList re-issues its GET) exactly once per open, not
 *    in a loop — never by inspecting the queryClient or the queryKey.
 */

import { type ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import useConversation from './useConversation';
import useEmailList from './useEmailList';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';

const API_BASE = 'http://localhost:8000';

function makeMessage(id: string, overrides: Record<string, unknown> = {}) {
  return {
    provider_message_id: id,
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: 't_1',
    from_email: `${id}@example.com`,
    from_name: id,
    subject: `Subject ${id}`,
    received_at: '2024-01-01T00:00:00Z',
    is_read: true,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: false,
    ...overrides,
  };
}

// Each renderHook gets its own client (fresh wrapper invocation), so cache
// never leaks across tests.
function wrapper({ children }: { children: ReactNode }) {
  const client = createTestQueryClient();
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  server.resetHandlers();
});

describe('useConversation', () => {
  it('maps ConversationOut to a flat { messages, threadId } shape', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({
          thread_id: 't_1',
          messages: [makeMessage('m_old'), makeMessage('m_new')],
        }),
      ),
    );

    const { result } = renderHook(() => useConversation('mb_1', 'a_1', 'm_new', true), { wrapper });

    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.threadId).toBe('t_1');
    expect(result.current.messages[0].provider_message_id).toBe('m_old');
    expect(result.current.error).toBeNull();
  });

  it('URL-encodes the provider_message_id in the conversation path', async () => {
    let seenPath: string | null = null;
    // A pmid containing '/' must arrive percent-encoded so it stays a single
    // path segment (Outlook ImmutableId case).
    server.use(
      http.get(
        `${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`,
        ({ request }) => {
          seenPath = new URL(request.url).pathname;
          return HttpResponse.json({ thread_id: '', messages: [] });
        },
      ),
    );

    const { result } = renderHook(() => useConversation('mb_1', 'a_1', 'AAk/ABB+id==', true), {
      wrapper,
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(seenPath).not.toBeNull();
    // The raw '/' must NOT appear inside the encoded id segment.
    expect(seenPath).toContain('AAk%2FABB');
    expect(seenPath).toContain('/conversation');
  });

  it('invalidates the email listing once after a successful fetch (lazy-sync side effect)', async () => {
    let listCalls = 0;
    let conversationCalls = 0;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () => {
        listCalls += 1;
        return HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () => {
        conversationCalls += 1;
        return HttpResponse.json({ thread_id: 't_1', messages: [makeMessage('m_1')] });
      }),
    );

    // Co-mount a listing and the conversation under the SAME client so the
    // blanket ['emails'] invalidation is observable as a re-fetch.
    renderHook(
      () => {
        useEmailList('mb_1', 'ALL_MAIL', undefined, undefined, undefined, 1, true);
        useConversation('mb_1', 'a_1', 'm_1', true);
      },
      { wrapper },
    );

    // The listing fires once on mount; the conversation's success then
    // invalidates it, producing a second listing request.
    await waitFor(() => expect(conversationCalls).toBe(1));
    await waitFor(() => expect(listCalls).toBeGreaterThanOrEqual(2));

    // Give any erroneous invalidation loop a chance to manifest, then assert
    // the conversation fetch did not repeat and the listing settled.
    const settledListCalls = listCalls;
    await new Promise((r) => setTimeout(r, 120));
    expect(conversationCalls).toBe(1);
    expect(listCalls).toBe(settledListCalls);
  });

  it('does not fetch while disabled', async () => {
    let calls = 0;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () => {
        calls += 1;
        return HttpResponse.json({ thread_id: '', messages: [] });
      }),
    );

    renderHook(() => useConversation('mb_1', 'a_1', 'm_1', false), { wrapper });

    await new Promise((r) => setTimeout(r, 80));
    expect(calls).toBe(0);
  });
});
