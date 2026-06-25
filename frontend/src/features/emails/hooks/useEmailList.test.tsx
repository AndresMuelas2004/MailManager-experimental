/**
 * Integration tests for ``useEmailList`` with pagination.
 *
 * MSW intercepts the HTTP boundary; the real endpoint function, schema
 * validation and React Query cache all run. We assert the page → offset
 * mapping on the wire, the parsing of the ``{ items, total, ... }``
 * envelope into the hook's flat return, and the ``keepPreviousData``
 * behaviour across a page change.
 *
 * The sync ``useEffect`` fires ``POST /emails/sync-metadata`` on mount;
 * the default MSW handler answers it with an empty result so it does not
 * interfere with the listing assertions.
 */

import { type ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse, delay } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import useEmailList from './useEmailList';
import { readLastSyncedAt } from '../../../lib/lastSync';
import { DEFAULT_LIST_CONTROLS } from '../../../lib/listControls';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';
import type { ListControlsState } from '../../../lib/listControls';

const API_BASE = 'http://localhost:8000';

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

describe('useEmailList — pagination', () => {
  it('parses the envelope and derives total / page / pageSize / totalPages', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({
          items: [makeEmail('m_1'), makeEmail('m_2')],
          total: 120,
          limit: 50,
          offset: 0,
        }),
      ),
    );

    const { result } = renderHook(
      () => useEmailList('mb_1', 'ALL_MAIL', undefined, undefined, undefined, 1),
      { wrapper },
    );

    await waitFor(() => expect(result.current.emails).toHaveLength(2));
    expect(result.current.total).toBe(120);
    expect(result.current.page).toBe(1);
    expect(result.current.pageSize).toBe(50);
    // ceil(120 / 50) = 3.
    expect(result.current.totalPages).toBe(3);
  });

  it('sends offset=(page-1)*50 on the wire', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        seenOffsets.push(new URL(request.url).searchParams.get('offset'));
        return HttpResponse.json({ items: [makeEmail('m_1')], total: 200, limit: 50, offset: 100 });
      }),
    );

    const { result } = renderHook(
      () => useEmailList('mb_1', 'ALL_MAIL', undefined, undefined, undefined, 3),
      { wrapper },
    );

    await waitFor(() => expect(result.current.emails).toHaveLength(1));
    // page 3 → offset 100; limit is always 50.
    expect(seenOffsets).toContain('100');
  });

  it('sends group_by_thread=true on the wire when grouped, and omits it otherwise', async () => {
    const seenGroup: (string | null)[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        seenGroup.push(new URL(request.url).searchParams.get('group_by_thread'));
        return HttpResponse.json({ items: [makeEmail('m_1')], total: 12, limit: 50, offset: 0 });
      }),
    );

    // 7th positional arg = groupByThread. ``total`` now counts threads; the
    // totalPages derivation is unchanged.
    const grouped = renderHook(
      () => useEmailList('mb_1', 'ALL_MAIL', undefined, undefined, undefined, 1, true),
      { wrapper },
    );
    await waitFor(() => expect(grouped.result.current.emails).toHaveLength(1));
    expect(seenGroup).toContain('true');

    seenGroup.length = 0;
    const ungrouped = renderHook(
      () => useEmailList('mb_1', 'ALL_MAIL', undefined, undefined, undefined, 1, false),
      { wrapper },
    );
    await waitFor(() => expect(ungrouped.result.current.emails).toHaveLength(1));
    // When false the param must be absent from every request.
    expect(seenGroup.every((v) => v === null)).toBe(true);
  });

  it('keeps the previous page while the next page is loading (keepPreviousData)', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, async ({ request }) => {
        const offset = new URL(request.url).searchParams.get('offset');
        // Delay the second page so the placeholder window is observable.
        if (offset === '50') {
          await delay(80);
          return HttpResponse.json({
            items: [makeEmail('p2')],
            total: 120,
            limit: 50,
            offset: 50,
          });
        }
        return HttpResponse.json({ items: [makeEmail('p1')], total: 120, limit: 50, offset: 0 });
      }),
    );

    const { result, rerender } = renderHook(
      ({ page }: { page: number }) =>
        useEmailList('mb_1', 'ALL_MAIL', undefined, undefined, undefined, page),
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

describe('useEmailList — sort + quick-filter controls', () => {
  it('omits sort/filter params on the wire for the default controls', async () => {
    let seenUrl: URL | null = null;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        seenUrl = new URL(request.url);
        return HttpResponse.json({ items: [makeEmail('m_1')], total: 1, limit: 50, offset: 0 });
      }),
    );

    const { result } = renderHook(
      () =>
        useEmailList(
          'mb_1',
          'ALL_MAIL',
          undefined,
          undefined,
          undefined,
          1,
          false,
          DEFAULT_LIST_CONTROLS,
        ),
      { wrapper },
    );

    await waitFor(() => expect(result.current.emails).toHaveLength(1));
    const params = seenUrl!.searchParams;
    // The default (date/desc, all chips off) must produce a byte-identical URL
    // to the pre-feature one: none of the new keys are present.
    for (const key of ['sort', 'sort_dir', 'unread', 'has_attachment', 'favorite_only']) {
      expect(params.get(key)).toBeNull();
    }
  });

  it('sends the wire-named params for a non-default sort and the chips', async () => {
    let seenUrl: URL | null = null;
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        seenUrl = new URL(request.url);
        return HttpResponse.json({ items: [makeEmail('m_1')], total: 1, limit: 50, offset: 0 });
      }),
    );

    const controls: ListControlsState = {
      sort: 'subject',
      dir: 'asc',
      unread: true,
      hasAttachment: true,
      favorite: true,
    };
    const { result } = renderHook(
      () => useEmailList('mb_1', 'ALL_MAIL', undefined, undefined, undefined, 1, false, controls),
      { wrapper },
    );

    await waitFor(() => expect(result.current.emails).toHaveLength(1));
    const params = seenUrl!.searchParams;
    // Internal control names map to the documented wire names.
    expect(params.get('sort')).toBe('subject');
    expect(params.get('sort_dir')).toBe('asc');
    expect(params.get('unread')).toBe('true');
    expect(params.get('has_attachment')).toBe('true');
    expect(params.get('favorite_only')).toBe('true');
  });

  it('caches two distinct control states under separate query keys', async () => {
    const seenSorts: (string | null)[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, ({ request }) => {
        const url = new URL(request.url);
        seenSorts.push(url.searchParams.get('sort'));
        // Echo a row whose id reflects the sort so we can tell the caches apart.
        const sort = url.searchParams.get('sort') ?? 'date';
        return HttpResponse.json({ items: [makeEmail(sort)], total: 1, limit: 50, offset: 0 });
      }),
    );

    const dateControls: ListControlsState = { ...DEFAULT_LIST_CONTROLS };
    const subjectControls: ListControlsState = { ...DEFAULT_LIST_CONTROLS, sort: 'subject' };

    const { result, rerender } = renderHook(
      ({ controls }: { controls: ListControlsState }) =>
        useEmailList('mb_1', 'ALL_MAIL', undefined, undefined, undefined, 1, false, controls),
      { wrapper, initialProps: { controls: dateControls } },
    );

    await waitFor(() => expect(result.current.emails[0]?.provider_message_id).toBe('date'));

    // Switching the control dimension issues a new request (different key) and
    // resolves to the subject-sorted row — not served from the date cache.
    rerender({ controls: subjectControls });
    await waitFor(() => expect(result.current.emails[0]?.provider_message_id).toBe('subject'));
    // Both sort values reached the wire — proof the keys did not collapse.
    expect(seenSorts).toContain(null); // date default omits ``sort``
    expect(seenSorts).toContain('subject');
  });
});

describe('useEmailList — manual refresh & sync mark', () => {
  it('sync() fires a provider sync-metadata POST', async () => {
    const seenSyncs: string[] = [];
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [makeEmail('m_1')], total: 1, limit: 50, offset: 0 }),
      ),
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, ({ params, request }) => {
        const accountId = new URL(request.url).searchParams.get('account_id');
        seenSyncs.push(`${String(params.mailboxId)}/${accountId}`);
        return HttpResponse.json({ total_synced: 0, accounts: [] });
      }),
    );

    const { result } = renderHook(
      () => useEmailList('mb_1', 'ALL_MAIL', 'a_1', undefined, undefined, 1),
      { wrapper },
    );

    // The mount auto-sync already fires once; capture the count and assert the
    // explicit sync() click adds exactly one more (carrying the scope's
    // account_id).
    await waitFor(() => expect(seenSyncs.length).toBeGreaterThanOrEqual(1));
    const before = seenSyncs.length;

    await act(async () => {
      result.current.sync();
    });

    await waitFor(() => expect(seenSyncs.length).toBe(before + 1));
    expect(seenSyncs[seenSyncs.length - 1]).toBe('mb_1/a_1');
  });

  it('advances lastSyncedAt from null to a numeric epoch and persists it on success', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [makeEmail('m_1')], total: 1, limit: 50, offset: 0 }),
      ),
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, () =>
        HttpResponse.json({ total_synced: 0, accounts: [] }),
      ),
    );

    const { result } = renderHook(
      () => useEmailList('mb_1', 'ALL_MAIL', 'a_1', undefined, undefined, 1),
      { wrapper },
    );

    // Starts null (storage cleared); the resolved sync stamps it.
    expect(result.current.lastSyncedAt).toBeNull();

    await waitFor(() => expect(typeof result.current.lastSyncedAt).toBe('number'));
    // Persisted under the mailbox+account scope (independent of box/q/page).
    expect(readLastSyncedAt('emails:mb_1:a_1')).toBe(result.current.lastSyncedAt);
  });

  it('a failed sync sets syncError without emptying the already-loaded listing', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/mb_1/emails`, () =>
        HttpResponse.json({ items: [makeEmail('m_1')], total: 1, limit: 50, offset: 0 }),
      ),
      // The provider sync fails; the listing read keeps working.
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/sync-metadata`, () =>
        HttpResponse.json(
          { error: { code: 'forbidden', message: 'Mailbox not accessible' } },
          { status: 403 },
        ),
      ),
    );

    const { result } = renderHook(
      () => useEmailList('mb_1', 'ALL_MAIL', 'a_1', undefined, undefined, 1),
      { wrapper },
    );

    // The listing still renders its row.
    await waitFor(() => expect(result.current.emails).toHaveLength(1));
    // The sync failure surfaces as a non-blocking syncError, separate from the
    // table-replacing ``error`` (which stays null), and the mark never advances.
    await waitFor(() => expect(result.current.syncError).not.toBeNull());
    expect(result.current.error).toBeNull();
    expect(result.current.emails).toHaveLength(1);
    expect(result.current.lastSyncedAt).toBeNull();
  });
});
