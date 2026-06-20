import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse, delay } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { pinTestLang } from '../../../test/i18nTestLang';
import { server } from '../../../test/msw/server';
import VirtualMailboxViewPage from './VirtualMailboxViewPage';

const API_BASE = 'http://localhost:8000';

// Pin Spanish so the fixed Spanish assertions hold (jsdom defaults to English).
pinTestLang('es');

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

  it("propagates the record's account_ids to the hook, firing a sync per account on open", async () => {
    useAccountFanout();
    const seenSyncs: string[] = [];
    server.use(
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
      // Capture which (mailbox, account) pairs the page syncs on open. The
      // default global handler answers too, but does not record the calls.
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, ({ params, request }) => {
        const accountId = new URL(request.url).searchParams.get('account_id');
        seenSyncs.push(`${String(params.mailboxId)}/${accountId}`);
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
    );

    renderVmbox('/m/mb_1/virtual-mailboxes/vmb_1');

    // a_1 lives in mb_1 (per useAccountFanout), so the page resolves it from
    // the catalogue and syncs mb_1/a_1.
    await waitFor(() => expect(seenSyncs).toContain('mb_1/a_1'));
  });

  it('shows a "Sincronizando…" indicator while the open sync is in flight', async () => {
    useAccountFanout();
    server.use(
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
      // Delay the sync so the indicator window is observable.
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, async () => {
        await delay(80);
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
    );

    renderVmbox('/m/mb_1/virtual-mailboxes/vmb_1');

    await waitFor(() => expect(screen.getByText('Sincronizando…')).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText('Sincronizando…')).not.toBeInTheDocument());
  });

  it('renders a single error banner (no table below) when the vmbox record fails with a non-404', async () => {
    useAccountFanout();
    server.use(
      // A non-404 failure on the record fetch must REPLACE the table with one
      // banner, not stack a banner above a still-rendered table (the pre-fix
      // bug: loadError sat outside combinedError, so the table rendered below).
      http.get(`${API_BASE}/virtual-mailboxes/vmb_1`, () =>
        HttpResponse.json(
          { error: { code: 'internal_error', message: 'No se pudo cargar la bandeja' } },
          { status: 500 },
        ),
      ),
      // The listing query is enabled independently of the record, so let it
      // succeed: without the fix its row would render underneath the banner.
      http.get(`${API_BASE}/virtual-mailboxes/vmb_1/emails`, () =>
        HttpResponse.json({ items: [sentEmail], total: 1, limit: 50, offset: 0 }),
      ),
    );

    renderVmbox('/m/mb_1/virtual-mailboxes/vmb_1');

    await waitFor(() => {
      expect(screen.getByText('No se pudo cargar la bandeja')).toBeInTheDocument();
    });
    // The banner replaces the table: the listing row never renders alongside it.
    expect(screen.queryByText('A vmbox sent message')).not.toBeInTheDocument();
  });

  it('is read-only: the listing renders no selection checkboxes', async () => {
    // A virtual mailbox aggregates accounts from possibly several real
    // mailboxes, so it has no single box to target a bulk action and stays
    // read-only — unlike the account and unified inboxes, which DID restore
    // selection. This guard pins that deliberate exclusion: if a future change
    // wires selection here, it must be a conscious decision that updates this
    // test, not a silent drift.
    useAccountFanout();
    server.use(
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

    renderVmbox('/m/mb_1/virtual-mailboxes/vmb_1');

    await waitFor(() => {
      expect(screen.getByText('A vmbox sent message')).toBeInTheDocument();
    });
    expect(screen.queryByRole('checkbox', { name: 'Seleccionar correo' })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('checkbox', { name: 'Seleccionar los 50 correos más recientes' }),
    ).not.toBeInTheDocument();
  });
});
