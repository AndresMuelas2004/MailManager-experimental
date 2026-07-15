/**
 * Integration tests for useBackfillStatus (the accounts twin — the emails twin
 * is byte-identical, sharing the endpoint + query key, so one test covers both).
 *
 * HTTP is intercepted at MSW; the real endpoint + Zod validation + React Query
 * caching run. Polling is driven explicitly with ``refetchQueries`` (a poll
 * tick) rather than real 2.5s timers, and the progressive-invalidation contract
 * is observed via a spy on the shared ``queryClient.invalidateQueries``.
 */

import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import useBackfillStatus from './useBackfillStatus';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';

const API_BASE = 'http://localhost:8000';
const MAILBOX_ID = 'mb1';

let queryClient: QueryClient;
function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function statusResponse(fetched: number, active: boolean, status = 'running') {
  return HttpResponse.json({
    accounts: [
      { account_id: 'a1', status, fetched_count: fetched, target_total: 100000, done: !active },
    ],
    active,
  });
}

beforeEach(() => {
  queryClient = createTestQueryClient();
});

afterEach(() => {
  server.resetHandlers();
});

describe('useBackfillStatus', () => {
  it('exposes the per-account status map and the active flag', async () => {
    server.use(
      http.get(`${API_BASE}/mailboxes/:mailboxId/backfill-status`, () => statusResponse(500, true)),
    );

    const { result } = renderHook(() => useBackfillStatus(MAILBOX_ID), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.active).toBe(true));
    const entry = result.current.statuses.get('a1');
    expect(entry?.fetched_count).toBe(500);
    expect(entry?.status).toBe('running');
  });

  it('does not invalidate listings on the first fetch (no previous counter)', async () => {
    // Default handler: no active backfill. A first fetch must not repaint the
    // listings (there is nothing to compare against yet).
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useBackfillStatus(MAILBOX_ID), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.active).toBe(false));
    expect(result.current.statuses.size).toBe(0);
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ['emails'] });
  });

  it("invalidates ['emails'] and ['virtual-mailbox-emails'] when the counter grows", async () => {
    let call = 0;
    server.use(
      http.get(`${API_BASE}/mailboxes/:mailboxId/backfill-status`, () => {
        call += 1;
        return statusResponse(call === 1 ? 10 : 25, true);
      }),
    );
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    const { result } = renderHook(() => useBackfillStatus(MAILBOX_ID), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.statuses.get('a1')?.fetched_count).toBe(10));

    // Simulate a poll tick: refetch (a different method than the spied
    // invalidateQueries) delivers the grown counter.
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['backfill-status', MAILBOX_ID] });
    });
    await waitFor(() => expect(result.current.statuses.get('a1')?.fetched_count).toBe(25));

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['emails'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['virtual-mailbox-emails'] });
  });

  it('invalidates once when the backfill just finished (active true → false)', async () => {
    let call = 0;
    server.use(
      http.get(`${API_BASE}/mailboxes/:mailboxId/backfill-status`, () => {
        call += 1;
        // Same count, but active flips to false on the second tick.
        return call === 1 ? statusResponse(10, true) : statusResponse(10, false, 'completed');
      }),
    );
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    const { result } = renderHook(() => useBackfillStatus(MAILBOX_ID), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.active).toBe(true));

    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['backfill-status', MAILBOX_ID] });
    });
    await waitFor(() => expect(result.current.active).toBe(false));

    // The finished transition repaints the last wave even without growth.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['emails'] });
  });
});
