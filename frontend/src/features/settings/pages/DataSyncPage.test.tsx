/**
 * Integration test for the "sync everything" page. The useSyncAll hook lists
 * the mailboxes and fans out one sync-metadata per mailbox with
 * Promise.allSettled, so a single failing mailbox does not abort the rest.
 * HTTP is intercepted at the MSW boundary; the UI language is pinned to
 * Spanish so the status messages assert deterministically.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { renderWithProviders } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';
import DataSyncPage from './DataSyncPage';

const API_BASE = 'http://localhost:8000';

function mailbox(id: string) {
  return {
    mailbox_id: id,
    display_name: id,
    owner_user_id: 'u_test',
    created_at: new Date('2024-01-01T00:00:00Z').toISOString(),
  };
}

beforeEach(() => {
  window.localStorage.setItem('lang', 'es');
});

afterEach(() => {
  window.localStorage.clear();
});

describe('DataSyncPage', () => {
  it('syncs every mailbox and reports completion on full success', async () => {
    const syncedMailboxes: string[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes`, () =>
        HttpResponse.json([mailbox('mb_1'), mailbox('mb_2')]),
      ),
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, ({ params }) => {
        syncedMailboxes.push(String(params.mailboxId));
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
    );

    renderWithProviders(<DataSyncPage />);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Sincronizar todo ahora' }));

    await waitFor(() => expect(screen.getByText('Sincronización completada.')).toBeInTheDocument());
    // One sync per mailbox, no account_id (every account of each mailbox).
    expect(syncedMailboxes.sort()).toEqual(['mb_1', 'mb_2']);
  });

  it('reports a partial failure when at least one mailbox sync fails', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes`, () =>
        HttpResponse.json([mailbox('mb_ok'), mailbox('mb_bad')]),
      ),
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, ({ params }) => {
        if (params.mailboxId === 'mb_bad') {
          return HttpResponse.json(
            { error: { code: 'sync_failed', message: 'boom' } },
            { status: 500 },
          );
        }
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
    );

    renderWithProviders(<DataSyncPage />);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Sincronizar todo ahora' }));

    await waitFor(() =>
      expect(screen.getByText('Algunas bandejas no se pudieron sincronizar.')).toBeInTheDocument(),
    );
    // A partial failure is NOT a hard error — the completion message must not show.
    expect(screen.queryByText('Sincronización completada.')).not.toBeInTheDocument();
  });

  it('surfaces the backend error message when listing the mailboxes fails', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes`, () =>
        HttpResponse.json(
          { error: { code: 'forbidden', message: 'Cannot list mailboxes' } },
          { status: 403 },
        ),
      ),
    );

    renderWithProviders(<DataSyncPage />);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Sincronizar todo ahora' }));

    await waitFor(() => expect(screen.getByText('Cannot list mailboxes')).toBeInTheDocument());
    expect(screen.queryByText('Sincronización completada.')).not.toBeInTheDocument();
  });
});
