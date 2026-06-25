/**
 * Integration tests for ``useVirtualMailboxEmails`` with pagination.
 *
 * MSW intercepts the HTTP boundary; the real endpoint function, schema
 * validation and React Query cache all run. We assert the page → offset
 * mapping on the wire (against the ``/virtual-mailboxes/:id/emails``
 * endpoint, which is a distinct route from the regular listing covered by
 * ``useEmailList.test.tsx``), the parsing of the ``{ items, total, ... }``
 * envelope into the hook's flat return, and ``keepPreviousData`` across a
 * page change.
 *
 * The hook also resolves accounts across every owned mailbox (it can
 * aggregate accounts from several real mailboxes), so the default MSW
 * handlers for ``/mailboxes`` and ``/mailboxes/:id/accounts`` answer those
 * fan-out calls and keep the listing assertions isolated.
 */

import { type ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse, delay } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import useVirtualMailboxEmails from './useVirtualMailboxEmails';
import { readLastSyncedAt } from '../../../lib/lastSync';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';

const API_BASE = 'http://localhost:8000';
const VMB_ID = 'vmb_1';

function makeEmail(id: string) {
  return {
    provider_message_id: id,
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: null,
    from_email: `${id}@example.com`,
    from_name: id,
    subject: `Subject ${id}`,
    received_at: '2024-01-01T00:00:00Z',
    is_read: false,
    box: 'ALL_MAIL',
  };
}

function makeAccount(accountId: string, mailboxId: string) {
  return {
    account_id: accountId,
    mailbox_id: mailboxId,
    provider: 'gmail',
    display_label: 'Gmail',
    config: {},
    email_address: `${accountId}@example.com`,
    signature_html: null,
  };
}

function makeMailbox(mailboxId: string) {
  return {
    mailbox_id: mailboxId,
    display_name: mailboxId,
    owner_user_id: 'u_test',
    created_at: '2024-01-01T00:00:00Z',
  };
}

// Serves a two-mailbox catalogue (a_1 ∈ mb_1, a_2 ∈ mb_2) so the sync fan-out
// can resolve each account_id to its real mailbox_id, and records every
// sync-metadata call the hook fires as ``{mailbox}/{account}`` pairs.
function installCatalogueAndCaptureSync(seen: string[]) {
  server.use(
    http.get(`${API_BASE}/mailboxes`, () =>
      HttpResponse.json([makeMailbox('mb_1'), makeMailbox('mb_2')]),
    ),
    http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () =>
      HttpResponse.json([makeAccount('a_1', 'mb_1')]),
    ),
    http.get(`${API_BASE}/mailboxes/mb_2/accounts`, () =>
      HttpResponse.json([makeAccount('a_2', 'mb_2')]),
    ),
    http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, ({ params, request }) => {
      const accountId = new URL(request.url).searchParams.get('account_id');
      seen.push(`${String(params.mailboxId)}/${accountId}`);
      return HttpResponse.json({ total_synced: 0, accounts: [] });
    }),
  );
}

function wrapper({ children }: { children: ReactNode }) {
  const client = createTestQueryClient();
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

let seenOffsets: (string | null)[] = [];

beforeEach(() => {
  seenOffsets = [];
  // The sync mark persists to localStorage; clear it so ``lastSyncedAt``
  // deterministically starts null for the refresh tests below.
  window.localStorage.clear();
});

afterEach(() => {
  server.resetHandlers();
  window.localStorage.clear();
});

describe('useVirtualMailboxEmails — pagination', () => {
  it('parses the envelope and derives total / page / pageSize / totalPages', async () => {
    server.use(
      http.get(`${API_BASE}/virtual-mailboxes/${VMB_ID}/emails`, () =>
        HttpResponse.json({
          items: [makeEmail('m_1'), makeEmail('m_2')],
          total: 120,
          limit: 50,
          offset: 0,
        }),
      ),
    );

    const { result } = renderHook(() => useVirtualMailboxEmails(VMB_ID, 'mb_1', [], undefined, 1), {
      wrapper,
    });

    await waitFor(() => expect(result.current.emails).toHaveLength(2));
    expect(result.current.total).toBe(120);
    expect(result.current.page).toBe(1);
    expect(result.current.pageSize).toBe(50);
    // ceil(120 / 50) = 3.
    expect(result.current.totalPages).toBe(3);
  });

  it('sends offset=(page-1)*50 on the wire', async () => {
    server.use(
      http.get(`${API_BASE}/virtual-mailboxes/${VMB_ID}/emails`, ({ request }) => {
        seenOffsets.push(new URL(request.url).searchParams.get('offset'));
        return HttpResponse.json({ items: [makeEmail('m_1')], total: 200, limit: 50, offset: 100 });
      }),
    );

    const { result } = renderHook(() => useVirtualMailboxEmails(VMB_ID, 'mb_1', [], undefined, 3), {
      wrapper,
    });

    await waitFor(() => expect(result.current.emails).toHaveLength(1));
    // page 3 → offset 100; limit is always 50.
    expect(seenOffsets).toContain('100');
  });

  it('keeps the previous page while the next page is loading (keepPreviousData)', async () => {
    server.use(
      http.get(`${API_BASE}/virtual-mailboxes/${VMB_ID}/emails`, async ({ request }) => {
        const offset = new URL(request.url).searchParams.get('offset');
        // Delay the second page so the placeholder window is observable.
        if (offset === '50') {
          await delay(80);
          return HttpResponse.json({ items: [makeEmail('p2')], total: 120, limit: 50, offset: 50 });
        }
        return HttpResponse.json({ items: [makeEmail('p1')], total: 120, limit: 50, offset: 0 });
      }),
    );

    const { result, rerender } = renderHook(
      ({ page }: { page: number }) => useVirtualMailboxEmails(VMB_ID, 'mb_1', [], undefined, page),
      { wrapper, initialProps: { page: 1 } },
    );

    await waitFor(() => expect(result.current.emails[0]?.provider_message_id).toBe('p1'));
    expect(result.current.isPlaceholder).toBe(false);

    // Move to page 2: while the delayed fetch is in flight the previous
    // page's data must stay (no flash to empty) and isPlaceholder is true.
    rerender({ page: 2 });

    await waitFor(() => expect(result.current.isPlaceholder).toBe(true));
    expect(result.current.emails[0]?.provider_message_id).toBe('p1');

    // Once the new page resolves the data swaps and the flag clears.
    await waitFor(() => expect(result.current.emails[0]?.provider_message_id).toBe('p2'));
    expect(result.current.isPlaceholder).toBe(false);
  });
});

describe('useVirtualMailboxEmails — sync on open', () => {
  it('fires one sync-metadata call per account, resolving each mailbox from the catalogue', async () => {
    const seenSyncs: string[] = [];
    installCatalogueAndCaptureSync(seenSyncs);

    const { result } = renderHook(
      () => useVirtualMailboxEmails(VMB_ID, 'mb_1', ['a_1', 'a_2'], undefined, 1),
      { wrapper },
    );

    // Two accounts spanning two real mailboxes → two syncs with the resolved
    // mailbox_id (NOT the route's mb_1 for a_2).
    await waitFor(() => expect(seenSyncs).toHaveLength(2));
    expect(seenSyncs.sort()).toEqual(['mb_1/a_1', 'mb_2/a_2']);

    await waitFor(() => expect(result.current.syncing).toBe(false));
  });

  it('refetches the listing after the sync resolves', async () => {
    const seenSyncs: string[] = [];
    installCatalogueAndCaptureSync(seenSyncs);

    let listingCalls = 0;
    server.use(
      http.get(`${API_BASE}/virtual-mailboxes/${VMB_ID}/emails`, () => {
        listingCalls += 1;
        return HttpResponse.json({ items: [makeEmail('m_1')], total: 1, limit: 50, offset: 0 });
      }),
    );

    const { result } = renderHook(
      () => useVirtualMailboxEmails(VMB_ID, 'mb_1', ['a_1'], undefined, 1),
      { wrapper },
    );

    // First GET on mount, second GET caused by onSuccess invalidating
    // ['virtual-mailbox-emails'].
    await waitFor(() => expect(seenSyncs).toHaveLength(1));
    await waitFor(() => expect(listingCalls).toBeGreaterThanOrEqual(2));
    expect(result.current.emails[0]?.provider_message_id).toBe('m_1');
  });

  it('keeps syncing=true while the fan-out is in flight and clears it when done', async () => {
    installCatalogueAndCaptureSync([]);
    server.use(
      // Delay the sync so the syncing window is observable.
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, async () => {
        await delay(80);
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
    );

    const { result } = renderHook(
      () => useVirtualMailboxEmails(VMB_ID, 'mb_1', ['a_1'], undefined, 1),
      { wrapper },
    );

    await waitFor(() => expect(result.current.syncing).toBe(true));
    await waitFor(() => expect(result.current.syncing).toBe(false));
  });

  it('skips an account that is not in the catalogue (revoked / not owned)', async () => {
    const seenSyncs: string[] = [];
    installCatalogueAndCaptureSync(seenSyncs);

    // a_1 resolves; a_unknown is not in the catalogue → only one sync.
    renderHook(() => useVirtualMailboxEmails(VMB_ID, 'mb_1', ['a_1', 'a_unknown'], undefined, 1), {
      wrapper,
    });

    await waitFor(() => expect(seenSyncs).toHaveLength(1));
    expect(seenSyncs).toEqual(['mb_1/a_1']);
  });

  it('does not block the listing when one account sync fails', async () => {
    const seenSyncs: string[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes`, () =>
        HttpResponse.json([makeMailbox('mb_1'), makeMailbox('mb_2')]),
      ),
      http.get(`${API_BASE}/mailboxes/mb_1/accounts`, () =>
        HttpResponse.json([makeAccount('a_1', 'mb_1')]),
      ),
      http.get(`${API_BASE}/mailboxes/mb_2/accounts`, () =>
        HttpResponse.json([makeAccount('a_2', 'mb_2')]),
      ),
      // a_2's sync (mb_2) fails with 500; a_1's (mb_1) succeeds.
      http.post(`${API_BASE}/mailboxes/mb_2/emails/sync-metadata`, () =>
        HttpResponse.json({ error: { code: 'sync_failed', message: 'boom' } }, { status: 500 }),
      ),
      http.post(`${API_BASE}/mailboxes/mb_1/emails/sync-metadata`, ({ request }) => {
        const accountId = new URL(request.url).searchParams.get('account_id');
        seenSyncs.push(`mb_1/${accountId}`);
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
      http.get(`${API_BASE}/virtual-mailboxes/${VMB_ID}/emails`, () =>
        HttpResponse.json({ items: [makeEmail('m_1')], total: 1, limit: 50, offset: 0 }),
      ),
    );

    const { result } = renderHook(
      () => useVirtualMailboxEmails(VMB_ID, 'mb_1', ['a_1', 'a_2'], undefined, 1),
      { wrapper },
    );

    // The healthy account still synced, the listing still shows, error stays
    // null and syncing settles back to false (allSettled never rejects).
    await waitFor(() => expect(seenSyncs).toEqual(['mb_1/a_1']));
    await waitFor(() => expect(result.current.emails[0]?.provider_message_id).toBe('m_1'));
    await waitFor(() => expect(result.current.syncing).toBe(false));
    expect(result.current.error).toBeNull();
  });

  it('does not re-fire the sync when the account set is unchanged across re-renders', async () => {
    const seenSyncs: string[] = [];
    installCatalogueAndCaptureSync(seenSyncs);

    const { rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useVirtualMailboxEmails(VMB_ID, 'mb_1', ids, undefined, 1),
      { wrapper, initialProps: { ids: ['a_1'] } },
    );

    await waitFor(() => expect(seenSyncs).toHaveLength(1));

    // A fresh array with the same contents must not re-trigger the sync
    // (gating is by syncKey, the stable projection of the target set).
    rerender({ ids: ['a_1'] });
    await delay(50);
    expect(seenSyncs).toHaveLength(1);
  });
});

describe('useVirtualMailboxEmails — manual refresh & sync mark', () => {
  it('sync() fires the fan-out again, one sync-metadata POST per resolved account', async () => {
    const seenSyncs: string[] = [];
    installCatalogueAndCaptureSync(seenSyncs);

    const { result } = renderHook(
      () => useVirtualMailboxEmails(VMB_ID, 'mb_1', ['a_1', 'a_2'], undefined, 1),
      { wrapper },
    );

    // The open auto-sync fans out to both accounts; record the count and assert
    // the explicit sync() click adds a second full fan-out.
    await waitFor(() => expect(seenSyncs).toHaveLength(2));

    await act(async () => {
      result.current.sync();
    });

    await waitFor(() => expect(seenSyncs).toHaveLength(4));
    expect(seenSyncs.slice(2).sort()).toEqual(['mb_1/a_1', 'mb_2/a_2']);
  });

  it('advances lastSyncedAt from null to a numeric epoch and persists it under the vmbox scope', async () => {
    installCatalogueAndCaptureSync([]);

    const { result } = renderHook(
      () => useVirtualMailboxEmails(VMB_ID, 'mb_1', ['a_1'], undefined, 1),
      { wrapper },
    );

    // Starts null (storage cleared); the resolved fan-out stamps it.
    expect(result.current.lastSyncedAt).toBeNull();

    await waitFor(() => expect(typeof result.current.lastSyncedAt).toBe('number'));
    expect(readLastSyncedAt(`vmbox:${VMB_ID}`)).toBe(result.current.lastSyncedAt);
  });
});
