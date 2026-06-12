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

import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import useConnectedAccounts from './useConnectedAccounts';
import { server } from '../../../test/msw/server';

const API_BASE = 'http://localhost:8000';
const MAILBOX_ID = 'mb_test';

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
  popup = createFakePopup();
  openSpy = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
});

afterEach(() => {
  openSpy.mockRestore();
  server.resetHandlers();
});

async function setupHook() {
  const rendered = renderHook(() => useConnectedAccounts(MAILBOX_ID));
  await waitFor(() => expect(rendered.result.current.loading).toBe(false));
  act(() => {
    rendered.result.current.setSelectedProvider('gmail');
  });
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
