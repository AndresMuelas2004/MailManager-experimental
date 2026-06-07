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
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse, delay } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import useEmailList from './useEmailList';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';

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
});

afterEach(() => {
  server.resetHandlers();
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
