/**
 * Integration tests for useFolders (MSW at the network boundary).
 *
 * The real endpoint functions, Zod validation and React Query cache all run;
 * only the HTTP response is synthesized by the shared MSW handlers. The
 * mutation → invalidation contract is observed via a spy on the shared
 * ``queryClient.invalidateQueries`` (same technique as useBackfillStatus).
 */

import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import useFolders from './useFolders';
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

describe('useFolders', () => {
  it('exposes the folder list from the server', async () => {
    server.use(
      http.get(`${API_BASE}/folders`, () =>
        HttpResponse.json([
          {
            folder_id: 'f1',
            owner_user_id: 'u1',
            name: 'Universidad',
            color: null,
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ]),
      ),
    );
    const { result } = renderHook(() => useFolders(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.folders).toHaveLength(1);
    expect(result.current.folders[0].name).toBe('Universidad');
  });

  it('creates a folder and invalidates the shared ["folders"] key', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useFolders(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      const created = await result.current.create({ name: 'Nueva' });
      expect(created.name).toBe('Nueva');
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['folders'] });
  });

  it('deleting a folder also invalidates ["rules"] and the listings (cascade)', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useFolders(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.remove('f1');
    });
    // A deleted folder cascades to its rules and drops its memberships.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['rules'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['folder-emails'] });
  });

  it('surfaces a name-conflict error through toUiError', async () => {
    server.use(
      http.post(`${API_BASE}/folders`, () =>
        HttpResponse.json(
          { error: { code: 'folder_name_conflict', message: 'dup' } },
          { status: 409 },
        ),
      ),
    );
    const { result } = renderHook(() => useFolders(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await expect(result.current.create({ name: 'Universidad' })).rejects.toBeTruthy();
    });
    await waitFor(() => expect(result.current.error?.code).toBe('folder_name_conflict'));
    // The raw backend text is replaced by the localized message.
    expect(result.current.error?.message).toBe('Ya tienes una carpeta con ese nombre.');
  });
});
