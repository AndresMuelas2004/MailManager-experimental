/**
 * Integration tests for useEmailFolders (MSW at the network boundary).
 *
 * Real endpoint + Zod + React Query cache; only the HTTP response is
 * synthesized. Two load-bearing invariants are pinned: the per-email assign
 * routes to the EMAIL's own mailbox_id / account_id (never the route mailbox —
 * a folder is unified across accounts), and the authoritative folder list from
 * the response repaints the chips on every cached ``['emails']`` page.
 */

import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import useEmailFolders from './useEmailFolders';
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

describe('useEmailFolders', () => {
  it("routes the assign to the email's own mailbox_id and account_id", async () => {
    let captured: Record<string, unknown> = {};
    server.use(
      http.post(
        `${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/emails/:pmid/folders`,
        ({ params }) => {
          captured = params;
          return HttpResponse.json({ folders: [] });
        },
      ),
    );
    const { result } = renderHook(() => useEmailFolders(), { wrapper: Wrapper });

    await act(async () => {
      await result.current.assign({
        mailboxId: 'mb-real',
        accountId: 'acc-1',
        providerMessageId: 'm1',
        folderId: 'f1',
      });
    });
    expect(captured.mailboxId).toBe('mb-real');
    expect(captured.accountId).toBe('acc-1');
    expect(captured.pmid).toBe('m1');
  });

  it('invalidates a SUPERSET of listings (adds folder-emails + folders)', async () => {
    // The invalidation radius is wider than useFavorite's: on top of the shared
    // ['emails'] / ['virtual-mailbox-emails'] / ['conversation'] it also drops
    // ['folder-emails'] (an unassign removes the row from a folder view) and
    // ['folders'] (membership counts change).
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useEmailFolders(), { wrapper: Wrapper });

    await act(async () => {
      await result.current.assign({
        mailboxId: 'mb-real',
        accountId: 'acc-1',
        providerMessageId: 'm1',
        folderId: 'f1',
      });
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['folder-emails'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['folders'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['emails'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['conversation'] });
  });

  it('surfaces an assign error through toUiError', async () => {
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/accounts/:accountId/emails/:pmid/folders`, () =>
        HttpResponse.json(
          { error: { code: 'folder_not_found', message: 'gone' } },
          { status: 404 },
        ),
      ),
    );
    const { result } = renderHook(() => useEmailFolders(), { wrapper: Wrapper });

    await act(async () => {
      await expect(
        result.current.assign({
          mailboxId: 'mb-real',
          accountId: 'acc-1',
          providerMessageId: 'm1',
          folderId: 'f1',
        }),
      ).rejects.toBeTruthy();
    });
    await waitFor(() => expect(result.current.error?.code).toBe('folder_not_found'));
  });
});
