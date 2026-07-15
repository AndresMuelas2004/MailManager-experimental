/**
 * Integration tests for AccountFavoritesPage.
 *
 * This page is the per-account twin of FavoritesPage: same favourites surface,
 * but scoped to one account via ``account_id`` and mounted under the account
 * route segment (``/m/:mailboxId/account/:accountId/favorites``). These tests
 * pin the regression-prone contract that distinguishes it from its siblings:
 * the listing request carries BOTH ``account_id`` and ``favorite=true`` and
 * NEVER ``group_by_thread`` (favourites never group, so the row star stays
 * clickable), and the per-row toggle routes to the email's REAL ``mailbox_id``
 * rather than the route mailbox. HTTP is intercepted at
 * MSW; the real hooks / endpoints / cache run. The page consumes the
 * draft-composer context, provided here as a benign no-op value (not a hook
 * mock).
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse, delay } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';
import { pinTestLang } from '../../../test/i18nTestLang';
import AccountFavoritesPage from './AccountFavoritesPage';
import {
  DraftComposerContext,
  type DraftComposerContextValue,
} from '../../../app/providers/DraftComposerContext';

const API_BASE = 'http://localhost:8000';

// The assertions below pin fixed Spanish copy ("Sincronizar favoritos",
// "Quitar de favoritos", "Seleccionar correo", "Favoritos" tab), so the UI
// language must be Spanish — jsdom otherwise defaults the I18nProvider to
// English. Mirrors FavoritesPage.test.tsx.
pinTestLang('es');

const accountFixture = {
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  provider: 'gmail',
  display_label: 'Gmail',
  config: {},
  email_address: 'me@example.com',
  signature_html: null,
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

function renderAccountFavorites() {
  return renderWithProviders(
    <DraftComposerContext.Provider value={noopComposer}>
      <Routes>
        <Route
          path="/m/:mailboxId/account/:accountId/favorites"
          element={<AccountFavoritesPage />}
        />
      </Routes>
    </DraftComposerContext.Provider>,
    { initialEntries: ['/m/mb_1/account/a_1/favorites'] },
  );
}

describe('AccountFavoritesPage', () => {
  it('lists with account_id + favorite=true and without group_by_thread', async () => {
    const seen: Array<{
      account: string | null;
      favorite: string | null;
      group: string | null;
    }> = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        seen.push({
          account: params.get('account_id'),
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

    renderAccountFavorites();

    await waitFor(() => expect(screen.getByText('Subject f1')).toBeInTheDocument());
    expect(seen.length).toBeGreaterThanOrEqual(1);
    // Account scope on, favourites filter on, conversation grouping off
    // (omitted from the wire). This is the essential difference from
    // FavoritesPage (no account_id) and AccountInboxPage (group_by_thread=true).
    expect(seen.every((s) => s.account === 'a_1')).toBe(true);
    expect(seen.every((s) => s.favorite === 'true')).toBe(true);
    expect(seen.every((s) => s.group === null)).toBe(true);
  });

  it('keeps the clickable row star (not mounted in conversation mode)', async () => {
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

    renderAccountFavorites();

    await waitFor(() => expect(screen.getByText('Subject f1')).toBeInTheDocument());
    // The per-row selection checkbox is present — proof the table is NOT in
    // conversationMode (which would drop selection for read-only thread rows).
    expect(
      screen.getAllByRole('checkbox', { name: 'Seleccionar correo' }).length,
    ).toBeGreaterThanOrEqual(1);
    // The star is an interactive toggle (favourite fixture → "Quitar de
    // favoritos"), not the read-only thread indicator of conversation mode.
    expect(screen.getByRole('button', { name: 'Quitar de favoritos' })).toBeInTheDocument();
  });

  it('toggles the row star with optimistic feedback and disables it while in flight', async () => {
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

    renderAccountFavorites();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getByText('Subject f1')).toBeInTheDocument());
    // The fixture is favourite, so the row star reads "Quitar de favoritos".
    const star = await screen.findByRole('button', { name: 'Quitar de favoritos' });
    await user.click(star);

    // The pending key is set synchronously inside ``toggle`` (before the
    // optimistic flip and before the PATCH is dispatched), so the same star
    // node is already disabled the instant the click is processed. Assert it
    // synchronously rather than via an async poll that, under full-suite
    // parallel load, could resolve only after the 50 ms PATCH had settled.
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
    // the route mailbox (mb_1) — the cross-mailbox case. The toggle must hit
    // mb_other, not mb_1.
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

    renderAccountFavorites();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getByText('Subject f1')).toBeInTheDocument());
    const star = await screen.findByRole('button', { name: 'Quitar de favoritos' });
    await user.click(star);

    await waitFor(() => expect(patchedMailbox).toBe('mb_other'));
  });
});
