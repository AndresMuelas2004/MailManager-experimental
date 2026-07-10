/**
 * Integration tests for the interactive OAuth connect flow inside
 * ``useConnectedAccounts.addAccount``.
 *
 * MSW intercepts the HTTP boundary; the real endpoint functions and schema
 * validation run. ``window.open`` is the only browser API stubbed (jsdom
 * cannot open real popups) — the OAuth callback popup is simulated by
 * dispatching the ``message`` event its page would post.
 *
 * The product contract under test: an account card only appears after the
 * provider connection succeeds, and any failure rolls back (deletes) the
 * account created at the start of the flow.
 */

import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import useConnectedAccounts from './useConnectedAccounts';
import { I18nProvider } from '../../../lib/i18n';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';

const API_BASE = 'http://localhost:8000';
const MAILBOX_ID = 'mb_test';

// The hook reads error copy through ``useTranslation`` AND holds a
// ``useQueryClient`` (it invalidates the sidebar's ['accounts'] query on
// add/remove/rename), so every ``renderHook`` must run inside both the real
// I18nProvider and a QueryClientProvider. The client is rebuilt per test (in
// beforeEach) for isolation. The language is pinned to Spanish (seeded below)
// so the existing Spanish error-copy assertions hold.
let queryClient: QueryClient;
function I18nWrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>{children}</I18nProvider>
    </QueryClientProvider>
  );
}

const EXISTING_ACCOUNT = {
  account_id: 'acc_existing',
  mailbox_id: MAILBOX_ID,
  provider: 'gmail',
  display_label: 'Gmail',
  config: {},
  email_address: 'old@example.com',
  signature_html: null,
};

type FakePopup = {
  closed: boolean;
  close: () => void;
  location: { href: string };
};

function createFakePopup(): FakePopup {
  const popup: FakePopup = {
    closed: false,
    close: () => {
      popup.closed = true;
    },
    location: { href: 'about:blank' },
  };
  return popup;
}

/**
 * Dispatch the callback page's postMessage once the popup has been pointed
 * at the provider authorization URL (i.e. once the start step finished).
 */
function emitOAuthResultWhenStarted(popup: FakePopup, data: Record<string, unknown>): void {
  const tick = () => {
    if (popup.location.href.startsWith('https://')) {
      window.dispatchEvent(new MessageEvent('message', { origin: API_BASE, data }));
    } else if (!popup.closed) {
      setTimeout(tick, 10);
    }
  };
  setTimeout(tick, 10);
}

let popup: FakePopup;
let openSpy: MockInstance<typeof window.open>;

beforeEach(() => {
  window.localStorage.setItem('lang', 'es');
  queryClient = createTestQueryClient();
  popup = createFakePopup();
  openSpy = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
});

afterEach(() => {
  openSpy.mockRestore();
  server.resetHandlers();
  window.localStorage.clear();
});

async function setupHook() {
  const rendered = renderHook(() => useConnectedAccounts(MAILBOX_ID), { wrapper: I18nWrapper });
  await waitFor(() => expect(rendered.result.current.loading).toBe(false));
  act(() => {
    rendered.result.current.setSelectedProvider('gmail');
  });
  return rendered;
}

/**
 * Render the hook with one already-connected account in the listing, so the
 * reconnect tests start from an existing card (reconnect operates on a row
 * that already exists — unlike addAccount, which creates it).
 */
async function setupHookWithAccount() {
  server.use(
    http.get(`${API_BASE}/mailboxes/:mailboxId/accounts`, () =>
      HttpResponse.json([EXISTING_ACCOUNT]),
    ),
  );
  const rendered = renderHook(() => useConnectedAccounts(MAILBOX_ID), { wrapper: I18nWrapper });
  await waitFor(() => expect(rendered.result.current.entries).toHaveLength(1));
  return rendered;
}

describe('useConnectedAccounts.addAccount — interactive OAuth flow', () => {
  it('adds the card only after the popup reports success', async () => {
    const { result } = await setupHook();
    emitOAuthResultWhenStarted(popup, {
      source: 'mailmanager-oauth',
      ok: true,
      provider: 'gmail',
      message: 'Account connected successfully.',
    });

    await act(async () => {
      await result.current.addAccount();
    });

    expect(result.current.error).toBeNull();
    expect(result.current.entries).toHaveLength(1);
    expect(result.current.entries[0].account.email_address).toBe('connected@example.com');
    expect(result.current.entries[0].status).toBe('ready');
    expect(popup.location.href).toContain('https://accounts.google.com/');
  });

  it('backgrounds the initial load: no metadata sync, drafts sync fires, backfill poll kicked', async () => {
    let syncMetaCalled = false;
    let draftsSyncCalled = false;
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, () => {
        syncMetaCalled = true;
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
      http.post(`${API_BASE}/mailboxes/:mailboxId/drafts/sync`, () => {
        draftsSyncCalled = true;
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
    );
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    const { result } = await setupHook();
    emitOAuthResultWhenStarted(popup, {
      source: 'mailmanager-oauth',
      ok: true,
      provider: 'gmail',
      message: 'Account connected successfully.',
    });

    await act(async () => {
      await result.current.addAccount();
    });

    expect(result.current.entries).toHaveLength(1);
    expect(result.current.entries[0].status).toBe('ready');
    // The initial history now downloads via the backend backfill — addAccount
    // no longer syncs metadata for the initial load.
    expect(syncMetaCalled).toBe(false);
    // Drafts still sync best-effort (fire-and-forget) — let it settle.
    await waitFor(() => expect(draftsSyncCalled).toBe(true));
    // The poll is kicked so the "Cargando…" counter appears without a remount.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['backfill-status', MAILBOX_ID] });
  });

  it('rolls the account back and shows the error when the start step fails', async () => {
    let deleted = false;
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/connect`, () =>
        HttpResponse.json(
          {
            error: {
              code: 'account_connect_auth_error',
              message: 'Failed to start the account connect flow.',
              detail: {},
            },
          },
          { status: 502 },
        ),
      ),
      http.delete(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`, () => {
        deleted = true;
        return HttpResponse.json({ status: 'deleted' });
      }),
    );

    const { result } = await setupHook();
    await act(async () => {
      await result.current.addAccount();
    });

    expect(result.current.entries).toHaveLength(0);
    expect(deleted).toBe(true);
    expect(result.current.error?.message).toContain('Failed to start the account connect flow.');
    expect(popup.closed).toBe(true);
  });

  it('rolls back when the popup closes without completing the connection', async () => {
    let deleted = false;
    server.use(
      // The account never gets an email_address: the silent-close probe
      // must treat it as "not connected".
      http.get(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`, ({ params }) =>
        HttpResponse.json({
          account_id: params.accountId,
          mailbox_id: params.mailboxId,
          provider: 'gmail',
          display_label: 'Gmail',
          config: {},
          email_address: null,
          signature_html: null,
        }),
      ),
      http.delete(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`, () => {
        deleted = true;
        return HttpResponse.json({ status: 'deleted' });
      }),
    );

    const { result } = await setupHook();
    // Simulate the user closing the popup once it shows the provider page.
    const closeWhenStarted = () => {
      if (popup.location.href.startsWith('https://')) {
        popup.closed = true;
      } else {
        setTimeout(closeWhenStarted, 10);
      }
    };
    setTimeout(closeWhenStarted, 10);

    await act(async () => {
      await result.current.addAccount();
    });

    expect(result.current.entries).toHaveLength(0);
    expect(deleted).toBe(true);
    expect(result.current.error?.message).toContain('No se completó la autenticación');
  });

  it('fails fast without creating anything when the popup is blocked', async () => {
    openSpy.mockReturnValue(null);
    let created = false;
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/accounts`, () => {
        created = true;
        return HttpResponse.json({
          account_id: 'acc_test',
          mailbox_id: MAILBOX_ID,
          provider: 'gmail',
          display_label: 'Gmail',
          config: {},
          email_address: null,
          signature_html: null,
        });
      }),
    );

    const { result } = await setupHook();
    await act(async () => {
      await result.current.addAccount();
    });

    expect(created).toBe(false);
    expect(result.current.entries).toHaveLength(0);
    expect(result.current.error?.message).toContain('ventanas emergentes');
  });
});

describe('useConnectedAccounts.reconnectAccount — interactive OAuth re-auth', () => {
  it('reconnects an existing account without recreating or deleting it', async () => {
    let created = false;
    let deleted = false;
    let connectedId: string | null = null;
    const { result } = await setupHookWithAccount();
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/accounts`, () => {
        created = true;
        return HttpResponse.json(EXISTING_ACCOUNT);
      }),
      http.post(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/connect`, ({ params }) => {
        connectedId = String(params.accountId);
        return HttpResponse.json({
          provider: 'gmail',
          account_id: params.accountId,
          account_label: `${params.mailboxId}__${params.accountId}`,
          authorization_url: 'https://accounts.google.com/o/oauth2/auth?mock=1',
          state: 'state-test',
        });
      }),
      http.delete(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`, () => {
        deleted = true;
        return HttpResponse.json({ status: 'deleted' });
      }),
    );

    emitOAuthResultWhenStarted(popup, {
      source: 'mailmanager-oauth',
      ok: true,
      provider: 'gmail',
      message: 'Account connected successfully.',
    });

    await act(async () => {
      await result.current.reconnectAccount('acc_existing');
    });

    // The connect ran against the EXISTING account; nothing was created or deleted.
    expect(result.current.error).toBeNull();
    expect(created).toBe(false);
    expect(deleted).toBe(false);
    expect(connectedId).toBe('acc_existing');
    expect(result.current.entries).toHaveLength(1);
    expect(result.current.entries[0].account.account_id).toBe('acc_existing');
    expect(result.current.entries[0].status).toBe('ready');
    expect(popup.location.href).toContain('https://accounts.google.com/');
  });

  it('still syncs metadata on reconnect and does NOT kick the backfill poll', async () => {
    // A reconnection is not a first connection: the backend enqueues no backfill
    // (sync_cursor is set), so reconnect keeps its classic sync-metadata call
    // and never touches the backfill-status query.
    let syncMetaCalled = false;
    const { result } = await setupHookWithAccount();
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/connect`, ({ params }) =>
        HttpResponse.json({
          provider: 'gmail',
          account_id: params.accountId,
          account_label: `${params.mailboxId}__${params.accountId}`,
          authorization_url: 'https://accounts.google.com/o/oauth2/auth?mock=1',
          state: 'state-test',
        }),
      ),
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, () => {
        syncMetaCalled = true;
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
    );
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    emitOAuthResultWhenStarted(popup, {
      source: 'mailmanager-oauth',
      ok: true,
      provider: 'gmail',
      message: 'Account connected successfully.',
    });

    await act(async () => {
      await result.current.reconnectAccount('acc_existing');
    });

    expect(syncMetaCalled).toBe(true);
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ['backfill-status', MAILBOX_ID] });
  });

  it('keeps the account (no rollback) and shows the error when the reconnect fails', async () => {
    let deleted = false;
    const { result } = await setupHookWithAccount();
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/connect`, () =>
        HttpResponse.json(
          {
            error: {
              code: 'account_connect_auth_error',
              message: 'Failed to start the account connect flow.',
              detail: {},
            },
          },
          { status: 502 },
        ),
      ),
      http.delete(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`, () => {
        deleted = true;
        return HttpResponse.json({ status: 'deleted' });
      }),
    );

    await act(async () => {
      await result.current.reconnectAccount('acc_existing');
    });

    // Contract difference vs addAccount: a previously-connected account is
    // never deleted when its reconnect fails — the user simply retries.
    expect(deleted).toBe(false);
    expect(result.current.entries).toHaveLength(1);
    expect(result.current.entries[0].account.account_id).toBe('acc_existing');
    expect(result.current.error?.message).toContain('Failed to start the account connect flow.');
    expect(popup.closed).toBe(true);
  });
});

describe('useConnectedAccounts.editAccountLabel — inline rename', () => {
  it('PATCHes the new label and replaces the entry in place', async () => {
    let patchedBody: Record<string, unknown> | null = null;
    const { result } = await setupHookWithAccount();
    server.use(
      http.patch(
        `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`,
        async ({ params, request }) => {
          patchedBody = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({
            ...EXISTING_ACCOUNT,
            account_id: String(params.accountId),
            display_label: String(patchedBody.display_label),
          });
        },
      ),
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.editAccountLabel('acc_existing', '  Personal Gmail  ');
    });

    expect(ok).toBe(true);
    // The label is trimmed before it reaches the wire.
    expect(patchedBody).toEqual({ display_label: 'Personal Gmail' });
    // The hook holds its accounts in useState, so the updated AccountOut
    // replaces the entry's account in place (the card re-renders). It also
    // invalidates the sidebar's ['accounts'] query, not observed in this test.
    expect(result.current.entries[0].account.display_label).toBe('Personal Gmail');
    expect(result.current.error).toBeNull();
  });

  it('does nothing and returns false for a blank label', async () => {
    let called = false;
    const { result } = await setupHookWithAccount();
    server.use(
      http.patch(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`, () => {
        called = true;
        return HttpResponse.json(EXISTING_ACCOUNT);
      }),
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.editAccountLabel('acc_existing', '   ');
    });

    expect(ok).toBe(false);
    expect(called).toBe(false);
    expect(result.current.entries[0].account.display_label).toBe('Gmail');
  });

  it('surfaces the error and keeps the old label when the PATCH fails', async () => {
    const { result } = await setupHookWithAccount();
    server.use(
      http.patch(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId`, () =>
        HttpResponse.json(
          { error: { code: 'account_operation_error', message: 'Failed to update account.' } },
          { status: 500 },
        ),
      ),
    );

    let ok: boolean | undefined;
    await act(async () => {
      ok = await result.current.editAccountLabel('acc_existing', 'New Label');
    });

    expect(ok).toBe(false);
    expect(result.current.entries[0].account.display_label).toBe('Gmail');
    expect(result.current.error?.message).toContain('Failed to update account.');
  });
});
