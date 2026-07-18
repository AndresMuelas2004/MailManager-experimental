/**
 * Integration tests for ``useEmailBulkActions`` — the archive / unarchive
 * fan-out.
 *
 * A bulk selection can span several REAL mailboxes (a virtual mailbox
 * aggregates accounts that live in different mailboxes), and the backend scopes
 * each archive call to one mailbox via the URL path. The hook therefore groups
 * the selection by ``email.mailbox_id`` and fires one POST per group. HTTP is
 * intercepted at MSW: the real endpoint functions, schema validation and React
 * Query cache all run.
 *
 * Pinned here: archiveItems / unarchiveItems hit the right endpoint, group by
 * mailbox (one call per mailbox, carrying only that mailbox's items), and run
 * the shared onSuccess (clearSelection + refresh).
 */

import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import useEmailBulkActions from './useEmailBulkActions';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { server } from '../../../test/msw/server';
import type { EmailMetadataOut } from '../../../api/types/dto';

const API_BASE = 'http://localhost:8000';

function makeEmail(overrides: Partial<EmailMetadataOut>): EmailMetadataOut {
  return {
    provider_message_id: 'm_1',
    account_id: 'a_1',
    mailbox_id: 'mb_1',
    thread_id: null,
    from_email: 'a@example.com',
    from_name: 'A',
    to_email: null,
    to_name: null,
    subject: 'S',
    received_at: '2024-01-01T00:00:00Z',
    is_read: false,
    box: 'ALL_MAIL',
    has_attachments: false,
    is_favorite: false,
    thread_message_count: 1,
    folders: [],
    ...overrides,
  };
}

function makeWrapper() {
  const client = createTestQueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('useEmailBulkActions — archive / unarchive', () => {
  it("archiveItems fans out one POST /archive per mailbox carrying that mailbox's items", async () => {
    const calls: Array<{ mailbox: string; items: unknown }> = [];
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/archive`, async ({ params, request }) => {
        const body = (await request.json()) as { items: unknown };
        calls.push({ mailbox: String(params.mailboxId), items: body.items });
        return HttpResponse.json({ moved_count: 1, accounts: [] });
      }),
    );

    const refresh = vi.fn(async () => {});
    const clearSelection = vi.fn();
    const { result } = renderHook(() => useEmailBulkActions({ refresh, clearSelection }), {
      wrapper: makeWrapper(),
    });

    // A cross-mailbox selection: two rows in mb_1, one in mb_2.
    await result.current.archiveItems([
      makeEmail({ provider_message_id: 'm_1', account_id: 'a_1', mailbox_id: 'mb_1' }),
      makeEmail({ provider_message_id: 'm_2', account_id: 'a_1', mailbox_id: 'mb_1' }),
      makeEmail({ provider_message_id: 'm_3', account_id: 'a_2', mailbox_id: 'mb_2' }),
    ]);

    await waitFor(() => expect(calls).toHaveLength(2));
    const byMailbox = Object.fromEntries(calls.map((c) => [c.mailbox, c.items]));
    expect(byMailbox['mb_1']).toEqual([
      { account_id: 'a_1', provider_message_id: 'm_1' },
      { account_id: 'a_1', provider_message_id: 'm_2' },
    ]);
    expect(byMailbox['mb_2']).toEqual([{ account_id: 'a_2', provider_message_id: 'm_3' }]);

    // The shared onSuccess ran: selection cleared, listing refreshed.
    expect(clearSelection).toHaveBeenCalled();
    expect(refresh).toHaveBeenCalled();
  });

  it('unarchiveItems posts to /restore-from-archive', async () => {
    const seen: string[] = [];
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/restore-from-archive`, ({ params }) => {
        seen.push(String(params.mailboxId));
        return HttpResponse.json({ moved_count: 1, accounts: [] });
      }),
    );

    const { result } = renderHook(
      () => useEmailBulkActions({ refresh: async () => {}, clearSelection: () => {} }),
      { wrapper: makeWrapper() },
    );

    await result.current.unarchiveItems([makeEmail({ box: 'ARCHIVE', mailbox_id: 'mb_7' })]);

    await waitFor(() => expect(seen).toEqual(['mb_7']));
  });

  it('swallows a backend failure (the catch keeps the bulk fan-out from throwing)', async () => {
    server.use(
      http.post(`${API_BASE}/mailboxes/:mailboxId/emails/archive`, () =>
        HttpResponse.json(
          { error: { code: 'archive_move_error', message: 'provider down' } },
          { status: 502 },
        ),
      ),
    );

    const { result } = renderHook(
      () => useEmailBulkActions({ refresh: async () => {}, clearSelection: () => {} }),
      { wrapper: makeWrapper() },
    );

    // The public wrapper catches so a failing call never rejects the caller.
    await expect(result.current.archiveItems([makeEmail({})])).resolves.toBeUndefined();
    await waitFor(() => expect(result.current.error).not.toBeNull());
  });
});
