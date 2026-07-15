/**
 * Integration tests for ConnectedAccountsPage (Settings › Accounts).
 *
 * The page is now a pure management surface: each account renders as a
 * uniform card exposing the three inline actions (edit label / reconnect /
 * delete) — no email preview, no ⋮ menu, no per-card unread badge, and the
 * card no longer navigates into the account (that moved to the sidebar scope
 * switcher). HTTP is intercepted at MSW; the real hooks / endpoints / cache
 * run. We override the accounts listing per test.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { pinTestLang } from '../../../test/i18nTestLang';
import { server } from '../../../test/msw/server';
import ConnectedAccountsPage from './ConnectedAccountsPage';

const API_BASE = 'http://localhost:8000';

// The action labels asserted below are Spanish copy (accounts.editLabel etc.);
// jsdom defaults the I18nProvider to English otherwise.
pinTestLang('es');

const accountOne = {
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  provider: 'gmail',
  display_label: 'Gmail',
  config: {},
  email_address: 'one@example.com',
  signature_html: null,
};
const accountTwo = {
  account_id: 'a_2',
  mailbox_id: 'mb_1',
  provider: 'outlook',
  display_label: 'Outlook',
  config: {},
  email_address: 'two@example.com',
  signature_html: null,
};

function renderConnectedAccounts() {
  return renderWithProviders(
    <Routes>
      <Route path="/m/:mailboxId/settings/accounts" element={<ConnectedAccountsPage />} />
    </Routes>,
    { initialEntries: ['/m/mb_1/settings/accounts'] },
  );
}

describe('ConnectedAccountsPage — account management cards', () => {
  it('renders each account as a card with edit/reconnect/delete actions and no unread badge', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () =>
        HttpResponse.json([accountOne, accountTwo]),
      ),
    );

    renderConnectedAccounts();

    // Both account cards mount (located by their header email text).
    await waitFor(() => expect(screen.getByText('one@example.com')).toBeInTheDocument());
    expect(screen.getByText('two@example.com')).toBeInTheDocument();

    // Each card exposes the three inline actions — no ⋮ menu, no email preview.
    expect(screen.getAllByRole('button', { name: 'Editar etiqueta' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Reconectar cuenta' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Eliminar cuenta' })).toHaveLength(2);

    // The per-card unread badge was removed together with the preview.
    expect(screen.queryByLabelText(/sin leer/)).not.toBeInTheDocument();
  });

  it('opens the inline rename input from the card Edit action', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountOne])),
    );

    renderConnectedAccounts();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getByText('one@example.com')).toBeInTheDocument());
    // AddAccountCard always renders one text input (the custom-name field); the
    // account card itself shows no rename input until Edit is pressed.
    expect(screen.getAllByRole('textbox')).toHaveLength(1);

    await user.click(screen.getByRole('button', { name: 'Editar etiqueta' }));

    // The card header swaps its email label for a rename input → one more textbox.
    expect(screen.getAllByRole('textbox')).toHaveLength(2);
  });

  it('confirms before deleting and issues the DELETE only on confirm', async () => {
    let deleted = false;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountOne])),
      http.delete(`${API_BASE}/mailboxes/mb_1/accounts/a_1`, () => {
        deleted = true;
        return HttpResponse.json({ status: 'deleted' });
      }),
    );

    renderConnectedAccounts();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getByText('one@example.com')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Eliminar cuenta' }));

    // A confirmation modal appears; the DELETE has not fired yet.
    expect(deleted).toBe(false);
    // common.delete is the modal's confirm button ("Eliminar").
    await user.click(screen.getByRole('button', { name: 'Eliminar' }));

    await waitFor(() => expect(deleted).toBe(true));
    // The card is removed from the listing after a successful delete.
    await waitFor(() => expect(screen.queryByText('one@example.com')).not.toBeInTheDocument());
  });

  it('shows the live backfill counter on a card whose account is still loading', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountOne])),
      // The account is mid-backfill → its card shows the counter.
      http.get(`${API_BASE}/mailboxes/mb_1/backfill-status`, () =>
        HttpResponse.json({
          accounts: [
            {
              account_id: 'a_1',
              status: 'running',
              fetched_count: 340,
              target_total: 100000,
              done: false,
            },
          ],
          active: true,
        }),
      ),
    );

    renderConnectedAccounts();

    await waitFor(() => expect(screen.getByText('one@example.com')).toBeInTheDocument());
    const notice = await screen.findByText(/Cargando/);
    expect(notice.textContent).toContain('340');
  });

  it('renders the card normally when the account has no active backfill', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountOne])),
      // A completed job is terminal → the counter is retired.
      http.get(`${API_BASE}/mailboxes/mb_1/backfill-status`, () =>
        HttpResponse.json({
          accounts: [
            {
              account_id: 'a_1',
              status: 'completed',
              fetched_count: 100000,
              target_total: 100000,
              done: true,
            },
          ],
          active: false,
        }),
      ),
    );

    renderConnectedAccounts();

    await waitFor(() => expect(screen.getByText('one@example.com')).toBeInTheDocument());
    expect(screen.queryByText(/Cargando/)).not.toBeInTheDocument();
  });
});

describe('ConnectedAccountsPage — per-user account quota', () => {
  it('renders the quota counter and keeps Add enabled below the limit', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([])),
      // Below the cap → the button stays clickable and no notice shows.
      http.get(`${API_BASE}/accounts/quota`, () => HttpResponse.json({ connected: 0, limit: 15 })),
    );

    renderConnectedAccounts();
    const user = userEvent.setup();

    // The counter mirrors the server quota ("N / max cuentas").
    expect(await screen.findByText('0 / 15 cuentas')).toBeInTheDocument();
    expect(screen.queryByText(/Has alcanzado el máximo/)).not.toBeInTheDocument();

    // With a provider selected canAdd becomes true; below the cap the quota does
    // NOT block the button, so it is enabled.
    await user.click(screen.getByRole('button', { name: /Selecciona un proveedor/ }));
    await user.click(screen.getByRole('button', { name: 'Gmail' }));
    expect(screen.getByRole('button', { name: 'Añadir cuenta' })).toBeEnabled();
  });

  it('disables Add and shows the limit notice at the cap', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([])),
      http.get(`${API_BASE}/accounts/quota`, () => HttpResponse.json({ connected: 15, limit: 15 })),
    );

    renderConnectedAccounts();
    const user = userEvent.setup();

    // The at-limit notice explains why adding is blocked, and the counter is full.
    expect(
      await screen.findByText('Has alcanzado el máximo de 15 cuentas conectadas.'),
    ).toBeInTheDocument();
    expect(screen.getByText('15 / 15 cuentas')).toBeInTheDocument();

    // Even with a provider selected (canAdd true) the at-limit quota disables the
    // button — effectiveCanAdd = canAdd && !atLimit.
    await user.click(screen.getByRole('button', { name: /Selecciona un proveedor/ }));
    await user.click(screen.getByRole('button', { name: 'Gmail' }));
    expect(screen.getByRole('button', { name: 'Añadir cuenta' })).toBeDisabled();
  });
});
