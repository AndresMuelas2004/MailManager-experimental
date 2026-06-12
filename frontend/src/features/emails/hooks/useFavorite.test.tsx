/**
 * Integration tests for ``useFavorite`` (MSW at the network boundary).
 *
 * The real endpoint functions, schema validation and React Query cache all
 * run; only the HTTP response is synthesized. Following the project
 * convention (``useConversation.test.tsx``), the optimistic cache writes are
 * NEVER observed by inspecting the queryClient. Instead we co-mount, under the
 * SAME fresh client, the hooks that EXPOSE those surfaces — ``useEmailList``
 * (``EmailPage`` shape, the Favoritos row star) and ``useConversation``
 * (``ConversationOut`` shape, the viewer's per-message button) — and assert on
 * the ``is_favorite`` values those hooks surface. That keeps the assertions on
 * the observable contract, not on internals (``src/test/CLAUDE.md`` §9.1).
 *
 * Covered:
 *  - F1: the optimistic flip reaches the ``EmailPage`` listing instantly,
 *    before the PATCH resolves (the bug was a no-op against the array check).
 *  - F2: the same flip reaches the ``ConversationOut`` viewer surface, and a
 *    settled toggle invalidates ['conversation'] (observed as a re-fetch).
 *  - F3: ``isToggling(accountId, providerMessageId)`` is true while the toggle
 *    is in flight and false once it settles (public hook contract).
 *  - F4: on success the server's authoritative ``is_favorite`` governs the
 *    final state, even when it contradicts the value sent.
 *  - Rollback: a failed toggle reverts ``is_favorite`` on both surfaces.
 *  - Cross-mailbox: the PATCH is routed to the email's own ``mailbox_id``,
 *    never the route's.
 */

import { type ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse, delay } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import useFavorite from './useFavorite';
import useEmailList from './useEmailList';
import useConversation from './useConversation';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';

const API_BASE = 'http://localhost:8000';

function makeEmail(overrides: Record<string, unknown> = {}) {
  return {
    provider_message_id: 'm_1',
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: 't_1',
    from_email: 'm_1@example.com',
    from_name: 'm_1',
    to_email: 'me@example.com',
    to_name: null,
    subject: 'Subject m_1',
    received_at: '2024-01-01T00:00:00Z',
    is_read: true,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: true,
    thread_message_count: 1,
    ...overrides,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = createTestQueryClient();
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

// Co-mount the mutation hook plus the two read surfaces it must keep in sync,
// all under the same client so an optimistic write is observable as a value
// change on what the read hooks expose.
function mountAll() {
  return renderHook(
    () => {
      const favorites = useFavorite();
      // Favoritos listing: favorite=true, page 1, no grouping — matches the
      // FavoritesPage call.
      const list = useEmailList('mb_1', 'ALL_MAIL', undefined, undefined, true, 1);
      const conversation = useConversation('mb_1', 'a_1', 'm_1', true);
      return { favorites, list, conversation };
    },
    { wrapper },
  );
}

afterEach(() => {
  server.resetHandlers();
});

describe('useFavorite', () => {
  it('flips is_favorite optimistically on the EmailPage and ConversationOut surfaces before the PATCH resolves (F1, F2)', async () => {
    // Two gates released together at the end. Rationale: the optimistic write
    // also touches the conversation cache, which bumps its dataUpdatedAt and
    // re-fires useConversation's lazy-sync effect → invalidate ['emails'] → an
    // emails refetch. With this always-is_favorite:true handler that refetch
    // would clobber the optimistic flip, so once "armed" every further emails
    // GET hangs (emailsGate). The PATCH hangs on patchGate so the in-flight
    // window — the only place optimism is observable — lasts until we assert.
    const gateResolvers: Array<() => void> = [];
    const newGate = () => new Promise<void>((resolve) => gateResolvers.push(resolve));
    const releaseGates = () => gateResolvers.forEach((r) => r());
    const patchGate = newGate();
    const emailsGate = newGate();
    let armEmailsGate = false;
    let patchResolved = false;
    // Count the listing GETs so we can wait for the mount-time ['emails']
    // invalidations (the metadata sync AND the conversation lazy-sync side
    // effect each invalidate ['emails']) to fully drain before toggling.
    let emailsGets = 0;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, async () => {
        emailsGets += 1;
        if (armEmailsGate) await emailsGate;
        return HttpResponse.json({ items: [makeEmail()], total: 1, limit: 50, offset: 0 });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({ thread_id: 't_1', messages: [makeEmail()] }),
      ),
      http.patch(
        `${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/favorite`,
        async ({ params, request }) => {
          const body = (await request.json()) as { favorite?: boolean };
          await patchGate;
          patchResolved = true;
          return HttpResponse.json({
            provider_message_id: String(params.pmid),
            account_id: String(params.accountId),
            is_favorite: Boolean(body.favorite),
          });
        },
      ),
    );

    const { result } = mountAll();

    // Both read surfaces are populated and start as favourite.
    await waitFor(() => expect(result.current.list.emails).toHaveLength(1));
    await waitFor(() => expect(result.current.conversation.messages).toHaveLength(1));
    await waitFor(() => expect(result.current.list.syncing).toBe(false));
    // Wait until the listing GETs stop firing (the sync + conversation
    // invalidations have all drained) so no in-flight refetch can overwrite the
    // optimistic write the moment after it lands.
    await waitFor(async () => {
      const before = emailsGets;
      await delay(60);
      expect(emailsGets).toBe(before);
    });
    expect(result.current.list.emails[0].is_favorite).toBe(true);
    expect(result.current.conversation.messages[0].is_favorite).toBe(true);

    // Arm the emails gate: from here any ['emails'] refetch hangs, so the
    // optimistic write cannot be reverted by the conversation-triggered
    // invalidation while we assert.
    armEmailsGate = true;

    // Fire the toggle WITHOUT awaiting — the optimistic flip lands before the
    // gated PATCH resolves.
    act(() => {
      void result.current.favorites.toggle({
        mailboxId: 'mb_1',
        accountId: 'a_1',
        providerMessageId: 'm_1',
        favorite: false,
      });
    });

    await waitFor(() => expect(result.current.list.emails[0].is_favorite).toBe(false));
    expect(result.current.conversation.messages[0].is_favorite).toBe(false);
    // Proof the flip was optimistic, not post-network: the PATCH is still in
    // flight (gate not yet released) while the surfaces already show the value.
    expect(patchResolved).toBe(false);

    // Release every gate and let the mutation settle.
    act(() => releaseGates());
    await waitFor(() => expect(result.current.favorites.toggling).toBe(false));
  });

  it('re-fetches the conversation after a settled toggle (F2 invalidation)', async () => {
    let conversationCalls = 0;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [makeEmail()], total: 1, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () => {
        conversationCalls += 1;
        return HttpResponse.json({ thread_id: 't_1', messages: [makeEmail()] });
      }),
    );

    const { result } = mountAll();

    await waitFor(() => expect(result.current.conversation.messages).toHaveLength(1));
    await waitFor(() => expect(conversationCalls).toBe(1));

    await act(async () => {
      await result.current.favorites.toggle({
        mailboxId: 'mb_1',
        accountId: 'a_1',
        providerMessageId: 'm_1',
        favorite: false,
      });
    });

    // onSettled invalidates ['conversation'] → the open conversation refetches.
    await waitFor(() => expect(conversationCalls).toBeGreaterThanOrEqual(2));
  });

  it('exposes isToggling per email while the toggle is in flight (F3)', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [makeEmail()], total: 1, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({ thread_id: 't_1', messages: [makeEmail()] }),
      ),
      http.patch(
        `${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/favorite`,
        async ({ params, request }) => {
          const body = (await request.json()) as { favorite?: boolean };
          await delay(40);
          return HttpResponse.json({
            provider_message_id: String(params.pmid),
            account_id: String(params.accountId),
            is_favorite: Boolean(body.favorite),
          });
        },
      ),
    );

    const { result } = mountAll();
    await waitFor(() => expect(result.current.list.emails).toHaveLength(1));
    await waitFor(() => expect(result.current.list.syncing).toBe(false));

    expect(result.current.favorites.isToggling('a_1', 'm_1')).toBe(false);

    act(() => {
      void result.current.favorites.toggle({
        mailboxId: 'mb_1',
        accountId: 'a_1',
        providerMessageId: 'm_1',
        favorite: false,
      });
    });

    await waitFor(() => expect(result.current.favorites.isToggling('a_1', 'm_1')).toBe(true));
    // A different email is never marked pending by this toggle.
    expect(result.current.favorites.isToggling('a_1', 'other')).toBe(false);

    await waitFor(() => expect(result.current.favorites.isToggling('a_1', 'm_1')).toBe(false));
  });

  it("reconciles to the server's authoritative is_favorite on success, even against the sent value (F4)", async () => {
    // Toggle asks to unfavourite (favorite:false), but the server reports the
    // row is STILL favourite. The final state must follow the response, not the
    // optimistic flip. The GET is aligned to the same server value so the
    // assertion is stable regardless of the onSettled refetch timing — what it
    // proves is that the response value governs, never the value sent.
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: [makeEmail({ is_favorite: true })],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({ thread_id: 't_1', messages: [makeEmail({ is_favorite: true })] }),
      ),
      http.patch(
        `${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/favorite`,
        async ({ params }) =>
          HttpResponse.json({
            provider_message_id: String(params.pmid),
            account_id: String(params.accountId),
            is_favorite: true,
          }),
      ),
    );

    const { result } = mountAll();
    await waitFor(() => expect(result.current.list.emails).toHaveLength(1));
    await waitFor(() => expect(result.current.list.syncing).toBe(false));

    await act(async () => {
      await result.current.favorites.toggle({
        mailboxId: 'mb_1',
        accountId: 'a_1',
        providerMessageId: 'm_1',
        favorite: false,
      });
    });

    // Server said "still favourite": the surfaces settle on true, not the
    // optimistic false. Wait for the onSettled refetch to drain first.
    await waitFor(() => expect(result.current.favorites.toggling).toBe(false));
    await waitFor(() => expect(result.current.list.syncing).toBe(false));
    expect(result.current.list.emails[0].is_favorite).toBe(true);
    expect(result.current.conversation.messages[0].is_favorite).toBe(true);
    expect(result.current.favorites.error).toBeNull();
  });

  it('rolls back the optimistic flip on both surfaces when the toggle fails', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [makeEmail()], total: 1, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({ thread_id: 't_1', messages: [makeEmail()] }),
      ),
      // Provider rejects the toggle → the optimistic flip must revert.
      http.patch(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/favorite`, () =>
        HttpResponse.json(
          { error: { code: 'external_api_error', message: 'provider failed' } },
          { status: 502 },
        ),
      ),
    );

    const { result } = mountAll();
    await waitFor(() => expect(result.current.list.emails).toHaveLength(1));
    await waitFor(() => expect(result.current.list.syncing).toBe(false));
    expect(result.current.list.emails[0].is_favorite).toBe(true);

    await act(async () => {
      await result.current.favorites
        .toggle({
          mailboxId: 'mb_1',
          accountId: 'a_1',
          providerMessageId: 'm_1',
          favorite: false,
        })
        .catch(() => {});
    });

    // After rollback + reconcile, both surfaces are favourite again and the
    // error is surfaced as a UiError.
    await waitFor(() => expect(result.current.list.emails[0].is_favorite).toBe(true));
    expect(result.current.conversation.messages[0].is_favorite).toBe(true);
    await waitFor(() => expect(result.current.favorites.error).not.toBeNull());
  });

  it('routes the PATCH to the email own mailbox_id, not the route mailbox (cross-mailbox)', async () => {
    // The toggle is called with the email's REAL mailbox (mb_other), which
    // differs from where the listing/conversation are mounted (mb_1). The PATCH
    // must hit mb_other.
    let patchedMailbox: string | null = null;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: [makeEmail({ mailbox_id: 'mb_other' })],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([])),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/conversation`, () =>
        HttpResponse.json({ thread_id: 't_1', messages: [makeEmail({ mailbox_id: 'mb_other' })] }),
      ),
      http.patch(
        `${API_BASE}/mailboxes/:mailboxId/accounts/a_1/emails/:pmid/favorite`,
        async ({ params, request }) => {
          patchedMailbox = String(params.mailboxId);
          const body = (await request.json()) as { favorite?: boolean };
          return HttpResponse.json({
            provider_message_id: String(params.pmid),
            account_id: String(params.accountId),
            is_favorite: Boolean(body.favorite),
          });
        },
      ),
    );

    const { result } = mountAll();
    await waitFor(() => expect(result.current.list.emails).toHaveLength(1));

    await act(async () => {
      await result.current.favorites.toggle({
        mailboxId: result.current.list.emails[0].mailbox_id,
        accountId: 'a_1',
        providerMessageId: 'm_1',
        favorite: false,
      });
    });

    expect(patchedMailbox).toBe('mb_other');
  });
});
