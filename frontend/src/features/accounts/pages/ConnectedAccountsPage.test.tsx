/**
 * Integration tests for ConnectedAccountsPage — the per-account unread badge
 * on each AccountCard (Settings › Accounts).
 *
 * The card counts come from a SEPARATE TanStack Query hook
 * (useMailboxUnreadByAccount) rather than from useConnectedAccounts (which
 * holds the cards in useState), so it inherits the ['emails'] invalidation.
 * HTTP is intercepted at MSW; the real hooks / endpoints / cache run. We
 * override the accounts listing and the unread-count breakdown; the per-card
 * email preview listing falls back to the default empty handler.
 */

import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { pinTestLang } from '../../../test/i18nTestLang';
import { server } from '../../../test/msw/server';
import ConnectedAccountsPage from './ConnectedAccountsPage';

const API_BASE = 'http://localhost:8000';

// The badge aria-label "{count} sin leer" is Spanish copy; jsdom defaults the
// I18nProvider to English otherwise.
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

describe('ConnectedAccountsPage — per-account unread badge', () => {
  it('shows a badge on the account with unread mail and none on the account at zero', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () =>
        HttpResponse.json([accountOne, accountTwo]),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/emails/unread-count`, () =>
        HttpResponse.json({
          mailbox_id: 'mb_1',
          box: 'ALL_MAIL',
          total: 4,
          accounts: [
            { account_id: 'a_1', unread: 4 },
            { account_id: 'a_2', unread: 0 },
          ],
        }),
      ),
    );

    renderConnectedAccounts();

    // Both account cards mount (located by their header email text).
    await waitFor(() => expect(screen.getByText('one@example.com')).toBeInTheDocument());
    expect(screen.getByText('two@example.com')).toBeInTheDocument();

    // a_1 has 4 unread → its card carries the "4 sin leer" badge; a_2 is at 0
    // so the Badge renders nothing. Exactly one badge exists across both cards.
    expect(screen.getByLabelText('4 sin leer')).toHaveTextContent('4');
    expect(screen.queryByLabelText('0 sin leer')).not.toBeInTheDocument();

    // Defence: the badge sits in the a_1 card header (alongside one@example.com),
    // not the a_2 header. The header is the shared parent of the email span and
    // the badge.
    const headerOne = screen.getByText('one@example.com').closest('div')?.parentElement;
    expect(within(headerOne as HTMLElement).getByLabelText('4 sin leer')).toBeInTheDocument();
    const headerTwo = screen.getByText('two@example.com').closest('div')?.parentElement;
    expect(within(headerTwo as HTMLElement).queryByLabelText('4 sin leer')).not.toBeInTheDocument();
  });
});
