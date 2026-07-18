/**
 * Integration tests for the read-only folder-list twins (MSW boundary).
 *
 * ``features/emails/hooks/useFolderList`` (assign menu) and
 * ``features/mailboxes/hooks/useFolderList`` (sidebar) share the ``['folders']``
 * query key, so TanStack Query dedupes every reader to ONE fetch — the same
 * contract the two ``useBackfillStatus`` twins have. A cross-feature import is
 * fine here because this is a test file, not production code.
 */

import type { ReactNode } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import useFolderListEmails from './useFolderList';
import useFolderListMailboxes from '../../mailboxes/hooks/useFolderList';
import { server } from '../../../test/msw/server';
import { createTestQueryClient } from '../../../test/renderWithProviders';

const API_BASE = 'http://localhost:8000';

let queryClient: QueryClient;
function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

const _folder = {
  folder_id: 'f1',
  owner_user_id: 'u1',
  name: 'Universidad',
  color: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

beforeEach(() => {
  queryClient = createTestQueryClient();
});

afterEach(() => {
  server.resetHandlers();
});

describe('useFolderList twins', () => {
  it('both twins read the same list, deduped to a single fetch', async () => {
    let calls = 0;
    server.use(
      http.get(`${API_BASE}/folders`, () => {
        calls += 1;
        return HttpResponse.json([_folder]);
      }),
    );
    const { result } = renderHook(
      () => ({ emails: useFolderListEmails(), mailboxes: useFolderListMailboxes() }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.emails.loading).toBe(false));
    await waitFor(() => expect(result.current.mailboxes.loading).toBe(false));

    expect(result.current.emails.folders).toHaveLength(1);
    expect(result.current.mailboxes.folders).toHaveLength(1);
    // The shared ['folders'] key collapses both readers into ONE network fetch.
    expect(calls).toBe(1);
  });
});
