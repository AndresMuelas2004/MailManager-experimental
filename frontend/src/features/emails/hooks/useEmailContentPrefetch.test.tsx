/**
 * Integration tests for ``useEmailContentPrefetch`` (MSW at the network
 * boundary). The real ``prefetchQuery``, endpoint function and React Query
 * cache all run; only the ``/content`` response is synthesized.
 *
 * Pins the two properties the bounded-concurrency warm loop must guarantee:
 * every eligible target is still warmed (no target dropped), and no more than
 * ``PREFETCH_CONCURRENCY`` ``/content`` requests are ever in flight at once —
 * the pre-fix loop fired one request per target with no wait (up to a full page
 * ~50 in a burst), starving the open the user was attempting. Plus cancellation:
 * an unmount (page/target change) stops launching requests for the stale page.
 */

import { type ReactNode } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse, delay } from 'msw';
import { describe, expect, it } from 'vitest';

import useEmailContentPrefetch, { PREFETCH_CONCURRENCY } from './useEmailContentPrefetch';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';
import type { EmailMetadataOut } from '../../../api/types/dto';

const API_BASE = 'http://localhost:8000';
const CONTENT_PATH = `${API_BASE}/mailboxes/:mailboxId/emails/:pmid/content`;

// A prefetch target: unread + ALL_MAIL + received within 48h (``isPrefetchTarget``).
function makeTarget(id: string): EmailMetadataOut {
  return {
    provider_message_id: id,
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: `t_${id}`,
    from_email: `${id}@example.com`,
    from_name: id,
    to_email: 'me@example.com',
    to_name: null,
    subject: `Subject ${id}`,
    received_at: new Date().toISOString(), // now → inside the 48h window
    is_read: false,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: false,
    thread_message_count: 1,
  };
}

const SIX_TARGETS = ['m1', 'm2', 'm3', 'm4', 'm5', 'm6'].map(makeTarget);

function wrapperFor(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('useEmailContentPrefetch', () => {
  it('warms every eligible target', async () => {
    const client = createTestQueryClient();
    server.use(
      http.get(CONTENT_PATH, () =>
        HttpResponse.json({ html_body: null, text_body: null, attachments: [] }),
      ),
    );

    renderHook(() => useEmailContentPrefetch(SIX_TARGETS), { wrapper: wrapperFor(client) });

    // Bounding concurrency must not drop any target — all six land in cache.
    await waitFor(() => {
      for (const t of SIX_TARGETS) {
        expect(
          client.getQueryData(['email-content', t.mailbox_id, t.account_id, t.provider_message_id]),
        ).toBeDefined();
      }
    });
  });

  it('never runs more than PREFETCH_CONCURRENCY /content requests at once', async () => {
    const client = createTestQueryClient();
    let inFlight = 0;
    let maxInFlight = 0;
    let completed = 0;
    server.use(
      http.get(CONTENT_PATH, async () => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await delay(20); // hold so overlapping requests are observable
        inFlight -= 1;
        completed += 1;
        return HttpResponse.json({ html_body: null, text_body: null, attachments: [] });
      }),
    );

    // Six targets against a cap of three: an unbounded burst would peak at 6.
    renderHook(() => useEmailContentPrefetch(SIX_TARGETS), { wrapper: wrapperFor(client) });

    await waitFor(() => expect(completed).toBe(SIX_TARGETS.length), { timeout: 3000 });
    // The pool parallelises up to the cap and never exceeds it.
    expect(maxInFlight).toBe(PREFETCH_CONCURRENCY);
  });

  it('stops launching new requests for a stale page after unmount', async () => {
    const client = createTestQueryClient();
    let requestCount = 0;
    let releaseGate: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      releaseGate = resolve;
    });
    server.use(
      http.get(CONTENT_PATH, async () => {
        requestCount += 1;
        await gate; // hold every response so the first batch stays in flight
        return HttpResponse.json({ html_body: null, text_body: null, attachments: [] });
      }),
    );

    const { unmount } = renderHook(() => useEmailContentPrefetch(SIX_TARGETS), {
      wrapper: wrapperFor(client),
    });

    // The first batch (== the cap) is dispatched and blocked on the gate.
    await waitFor(() => expect(requestCount).toBe(PREFETCH_CONCURRENCY));
    unmount(); // cleanup flips ``aborted`` → the pool must not pull more items
    releaseGate(); // let the in-flight batch settle
    await new Promise((r) => setTimeout(r, 50));
    // The remaining targets were never launched.
    expect(requestCount).toBe(PREFETCH_CONCURRENCY);
  });
});
