/**
 * Integration tests for FavoritesPage.
 *
 * Favourites are NOT grouped into conversations (the conversation view is for
 * the inbox surfaces only). These tests pin the regression-prone contract via
 * the network boundary and the rendered surface: the listing request carries
 * favorite=true and NEVER group_by_thread, and the table keeps bulk selection
 * (it is not mounted in conversationMode). HTTP is intercepted at MSW; the
 * real hooks / endpoints / cache run. The page consumes the draft-composer
 * context, provided here as a benign no-op value (not a hook mock).
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse, delay } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';
import FavoritesPage from './FavoritesPage';
import {
  DraftComposerContext,
  type DraftComposerContextValue,
} from '../../../app/providers/DraftComposerContext';

const API_BASE = 'http://localhost:8000';

const accountFixture = {
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  provider: 'gmail',
  display_label: 'Gmail',
  config: {},
  email_address: 'me@example.com',
};

function makeFavorite(id: string) {
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
    received_at: new Date('2024-01-10T09:00:00Z').toISOString(),
    is_read: true,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: true,
    thread_message_count: 1,
  };
}

const noopComposer: DraftComposerContextValue = {
  openForNewEmail: () => {},
  openForNewDraft: () => {},
  openForEditDraft: () => {},
  openForReply: async () => {},
  openForReplyAll: async () => {},
  openForForward: async () => {},
  setRefreshCallback: () => {},
  __register: () => {},
};

function renderFavorites() {
  return renderWithProviders(
    <DraftComposerContext.Provider value={noopComposer}>
      <Routes>
        <Route path="/m/:mailboxId/favorites" element={<FavoritesPage />} />
      </Routes>
    </DraftComposerContext.Provider>,
    { initialEntries: ['/m/mb_1/favorites'] },
  );
}

describe('FavoritesPage', () => {
  it('lists with favorite=true and without group_by_thread', async () => {
    const seen: Array<{ favorite: string | null; group: string | null }> = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        seen.push({
          favorite: params.get('favorite'),
          group: params.get('group_by_thread'),
        });
        return HttpResponse.json({
          items: [makeFavorite('f1')],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderFavorites();

    await waitFor(() => expect(screen.getByText('Subject f1')).toBeInTheDocument());
    expect(seen.length).toBeGreaterThanOrEqual(1);
    // Favourites filter on, conversation grouping off (omitted from the wire).
    expect(seen.every((s) => s.favorite === 'true')).toBe(true);
    expect(seen.every((s) => s.group === null)).toBe(true);
  });

  it('keeps bulk selection (not mounted in conversation mode)', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: [makeFavorite('f1')],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
    );

    renderFavorites();

    await waitFor(() => expect(screen.getByText('Subject f1')).toBeInTheDocument());
    // The per-row selection checkbox is present — proof the table is NOT in
    // conversationMode (which would drop selection for read-only thread rows).
    expect(
      screen.getAllByRole('checkbox', { name: 'Seleccionar correo' }).length,
    ).toBeGreaterThanOrEqual(1);
  });

  it('toggles the row star with optimistic feedback and disables it while in flight (F3)', async () => {
    // Hold the PATCH open so the optimistic flip and the in-flight disabled
    // state are both observable before the request settles.
    let patchSeen = false;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [makeFavorite('f1')], total: 1, limit: 50, offset: 0 }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
      http.patch(
        `${API_BASE}/mailboxes/mb_1/accounts/a_1/emails/:pmid/favorite`,
        async ({ params, request }) => {
          patchSeen = true;
          const body = (await request.json()) as { favorite?: boolean };
          await delay(50);
          return HttpResponse.json({
            provider_message_id: String(params.pmid),
            account_id: String(params.accountId),
            is_favorite: Boolean(body.favorite),
          });
        },
      ),
    );

    renderFavorites();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getByText('Subject f1')).toBeInTheDocument());
    // The fixture is favourite, so the row star reads "Quitar de favoritos".
    const star = await screen.findByRole('button', { name: 'Quitar de favoritos' });
    await user.click(star);

    // The pending key is set synchronously inside ``toggle`` (before the
    // optimistic flip and before the PATCH is even dispatched), so the same star
    // node is already disabled the instant the click is processed. Assert it
    // synchronously rather than via an async query: the previous "await
    // findByRole(flipped label) then expect disabled" coupled the anti
    // double-click check to a poll that, under full-suite parallel load, could
    // resolve only after the 50 ms PATCH had already settled and the per-row key
    // was cleared — leaving the star enabled and the assertion flaky.
    expect(star).toBeDisabled();

    // Optimistic flip: the same node's accessible label switches to "no longer
    // favourite" before the PATCH resolves.
    await waitFor(() => expect(star).toHaveAttribute('aria-label', 'Marcar como favorito'));
    expect(patchSeen).toBe(true);

    // Once the PATCH settles the star is interactive again.
    await waitFor(() => expect(star).not.toBeDisabled());
  });

  it('routes the favourite PATCH to the email own mailbox_id, not the route mailbox', async () => {
    // The listing surfaces an email whose REAL mailbox (mb_other) differs from
    // the route mailbox (mb_1) — the cross-mailbox virtual-bandeja case. The
    // toggle must hit mb_other, not mb_1.
    let patchedMailbox: string | null = null;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: [{ ...makeFavorite('f1'), mailbox_id: 'mb_other' }],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
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

    renderFavorites();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getByText('Subject f1')).toBeInTheDocument());
    const star = await screen.findByRole('button', { name: 'Quitar de favoritos' });
    await user.click(star);

    await waitFor(() => expect(patchedMailbox).toBe('mb_other'));
  });
});
