/**
 * Integration tests for useAccountQuota (the per-user connected-account quota).
 *
 * HTTP is intercepted at MSW; the real endpoint + Zod validation + React Query
 * caching run — nothing above the network is mocked. The hook must degrade
 * gracefully: while loading or after a failed/invalid response ``data`` is
 * undefined, so ``atLimit`` stays false (the "Add account" button is never
 * wrongly blocked) — the backend 409 is the real second-line guard.
 */

import type { ReactNode } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import useAccountQuota from './useAccountQuota';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';

const API_BASE = 'http://localhost:8000';

let queryClient: QueryClient;
function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  queryClient = createTestQueryClient();
});

afterEach(() => {
  server.resetHandlers();
});

describe('useAccountQuota', () => {
  it('exposes connected/limit/remaining and stays below atLimit under the cap', async () => {
    server.use(
      http.get(`${API_BASE}/accounts/quota`, () => HttpResponse.json({ connected: 3, limit: 15 })),
    );

    const { result } = renderHook(() => useAccountQuota(), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.connected).toBe(3));
    expect(result.current.limit).toBe(15);
    expect(result.current.remaining).toBe(12);
    expect(result.current.atLimit).toBe(false);
  });

  it('flags atLimit and zero remaining when connected reaches the limit', async () => {
    server.use(
      http.get(`${API_BASE}/accounts/quota`, () => HttpResponse.json({ connected: 15, limit: 15 })),
    );

    const { result } = renderHook(() => useAccountQuota(), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.atLimit).toBe(true));
    expect(result.current.remaining).toBe(0);
  });

  it('degrades gracefully on an invalid payload: undefined fields, atLimit false', async () => {
    // A malformed body fails Zod validation at the api boundary (schema_mismatch),
    // so the query settles into error with no data. The hook must NOT block the
    // add button in that state.
    server.use(
      http.get(`${API_BASE}/accounts/quota`, () => HttpResponse.json({ connected: 'oops' })),
    );

    const { result } = renderHook(() => useAccountQuota(), { wrapper: Wrapper });

    await waitFor(() =>
      expect(queryClient.getQueryState(['accounts-quota'])?.status).toBe('error'),
    );
    expect(result.current.connected).toBeUndefined();
    expect(result.current.limit).toBeUndefined();
    expect(result.current.remaining).toBeUndefined();
    expect(result.current.atLimit).toBe(false);
  });
});
