import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';
import VirtualMailboxViewPage from './VirtualMailboxViewPage';

const API_BASE = 'http://localhost:8000';

const sentEmail = {
  provider_message_id: 'm_sent',
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  thread_id: null,
  from_email: 'alice@example.com',
  from_name: 'Alice',
  to_email: 'recipient@example.com',
  to_name: 'Recipient',
  subject: 'A vmbox sent message',
  received_at: new Date('2024-01-12T09:00:00Z').toISOString(),
  is_read: true,
  box: 'SENT',
  has_attachments: false,
  is_favorite: false,
};

const accountFixture = {
  account_id: 'a_1',
  mailbox_id: 'mb_1',
  provider: 'gmail',
  display_label: 'Gmail',
  config: {},
  email_address: 'alice@example.com',
};

const mailboxFixture = {
  mailbox_id: 'mb_1',
  display_name: 'Primary',
  owner_user_id: 'u_test',
  created_at: new Date('2024-01-01T00:00:00Z').toISOString(),
};

// The vmbox listing hook fans out to /mailboxes (to discover every owned
// mailbox) and /mailboxes/:id/accounts (so resolveAccount can fill the
// provider/account columns). These two are shared across the cases.
function useAccountFanout() {
  server.use(
    http.get(`${API_BASE}/mailboxes`, () => HttpResponse.json([mailboxFixture])),
    http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () => HttpResponse.json([accountFixture])),
  );
}

function renderVmbox(initialEntry: string) {
  return renderWithProviders(
    <Routes>
      <Route
        path="/m/:mailboxId/virtual-mailboxes/:virtualMailboxId"
        element={<VirtualMailboxViewPage />}
      />
    </Routes>,
    { initialEntries: [initialEntry] },
  );
}

describe('VirtualMailboxViewPage', () => {
  it('renders sent-mail columns when q carries in:sent (intersecting the vmbox)', async () => {
    useAccountFanout();
    server.use(
      // filter_payload has no box → without in: the page would show inbound
      // columns; the in:sent in q is what flips them to sent.
      http.get(`${API_BASE}/virtual-mailboxes/vmb_1`, () =>
        HttpResponse.json({
          virtual_mailbox_id: 'vmb_1',
          owner_user_id: 'u_test',
          display_name: 'My vmbox',
          account_ids: ['a_1'],
          filter_payload: {},
          created_at: new Date('2024-01-01T00:00:00Z').toISOString(),
          updated_at: new Date('2024-01-01T00:00:00Z').toISOString(),
        }),
      ),
      http.get(`${API_BASE}/virtual-mailboxes/vmb_1/emails`, () =>
        HttpResponse.json({ items: [sentEmail], total: 1, limit: 50, offset: 0 }),
      ),
    );

    renderVmbox('/m/mb_1/virtual-mailboxes/vmb_1?q=in:sent');

    await waitFor(() => {
      expect(screen.getByText('A vmbox sent message')).toBeInTheDocument();
    });

    // Unified view shows both columns; with the effective box SENT the
    // recipient must appear under "Para" (its real recipient, not the
    // user's own account) and the sender under "De" is the account email.
    expect(screen.getByText('Para')).toBeInTheDocument();
    expect(screen.getByText('De')).toBeInTheDocument();
    expect(screen.getByText('recipient@example.com')).toBeInTheDocument();
  });

  it('preserves sent columns from filter_payload.box when there is no in: operator', async () => {
    useAccountFanout();
    server.use(
      http.get(`${API_BASE}/virtual-mailboxes/vmb_1`, () =>
        HttpResponse.json({
          virtual_mailbox_id: 'vmb_1',
          owner_user_id: 'u_test',
          display_name: 'My vmbox',
          account_ids: ['a_1'],
          filter_payload: { box: 'SENT' },
          created_at: new Date('2024-01-01T00:00:00Z').toISOString(),
          updated_at: new Date('2024-01-01T00:00:00Z').toISOString(),
        }),
      ),
      http.get(`${API_BASE}/virtual-mailboxes/vmb_1/emails`, () =>
        HttpResponse.json({ items: [sentEmail], total: 1, limit: 50, offset: 0 }),
      ),
    );

    renderVmbox('/m/mb_1/virtual-mailboxes/vmb_1');

    await waitFor(() => {
      expect(screen.getByText('A vmbox sent message')).toBeInTheDocument();
    });

    // No in: → the saved filter_payload.box='SENT' drives the columns, the
    // previous behaviour intact.
    expect(screen.getByText('recipient@example.com')).toBeInTheDocument();
  });
});
